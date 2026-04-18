# import_data.py
import sqlite3
import json
import argparse
import sys
import os
import importlib.util
from collections import OrderedDict
import traceback

DB_FILE = "generic_store.db"

# Cache for Python object identity -> entity_id within a single structure import
import_cache = {}
# Cache for directory path -> entity_id during directory import
dir_entity_cache = {}
# Global DB cursor and type map for helper functions
_db_cursor = None
_type_map = None


def get_entity_type_map(cursor):
    """Fetches entity types and returns a name -> id map."""
    cursor.execute("SELECT id, name FROM EntityType")
    return {name: id for id, name in cursor.fetchall()}

def import_node(node):
    """
    Recursively imports any Python node into the database.
    Handles cycles via global import_cache.
    Returns the entity_id of the imported node.
    """
    global import_cache, _db_cursor, _type_map

    if _db_cursor is None or _type_map is None:
         raise RuntimeError("Database cursor or type map not initialized.")

    # --- Cache Check ---
    try:
        node_id = id(node)
        if node_id in import_cache:
            return import_cache[node_id]
    except TypeError:
        node_id = None # Cannot use identity caching

    entity_type_id = None
    value_str = None
    value_num = None
    value_bool = None
    node_type = type(node)

    # --- Determine Entity Type and Value ---
    if node_type is OrderedDict:
        entity_type_id = _type_map['Version']
    elif node_type is dict:
        entity_type_id = _type_map['Object']
    elif node_type is list:
        entity_type_id = _type_map['Array']
    elif node_type is tuple:
        # Consider adding a 'Tuple' EntityType if strict differentiation is needed
        entity_type_id = _type_map['Array']
    elif node_type is str:
        entity_type_id = _type_map['String']
        value_str = node
    elif node_type is bool: # Check bool before int
        entity_type_id = _type_map['Boolean']
        value_bool = 1 if node else 0
    elif isinstance(node, (int, float)):
        entity_type_id = _type_map['Number']
        value_num = node
    elif node is None:
        entity_type_id = _type_map['Null']
    else:
        raise TypeError(f"Unsupported Python type for DB storage: {node_type}")

    # --- Insert Entity ---
    _db_cursor.execute("""
        INSERT INTO Entity (entity_type_id, value_str, value_num, value_bool)
        VALUES (?, ?, ?, ?)
    """, (entity_type_id, value_str, value_num, value_bool))
    current_entity_id = _db_cursor.lastrowid

    # --- Update Cache ---
    if node_id is not None:
        import_cache[node_id] = current_entity_id

    # --- Recurse for Containers ---
    container_type_ids = (_type_map['Object'], _type_map['Array'], _type_map['Version'])
    if entity_type_id in container_type_ids:
        if entity_type_id == _type_map['Object'] or entity_type_id == _type_map['Version']:
            for key, value in node.items():
                try:
                    specifier_entity_id = import_node(key)
                    child_entity_id = import_node(value)
                    _db_cursor.execute("""
                        INSERT INTO Relationship (parent_entity_id, child_entity_id, relationship_specifier_id)
                        VALUES (?, ?, ?)
                    """, (current_entity_id, child_entity_id, specifier_entity_id))
                except sqlite3.IntegrityError as ie:
                    print(f"Warning: Skipping duplicate relationship for parent {current_entity_id}, specifier '{key}' due to constraint: {ie}", file=sys.stderr)
                except Exception as e:
                    print(f"Error processing dict/version item ({key}: {value}): {e}", file=sys.stderr)
                    raise
        elif entity_type_id == _type_map['Array']:
            for index, value in enumerate(node):
                try:
                    specifier_entity_id = import_node(index)
                    child_entity_id = import_node(value)
                    _db_cursor.execute("""
                        INSERT INTO Relationship (parent_entity_id, child_entity_id, relationship_specifier_id)
                        VALUES (?, ?, ?)
                    """, (current_entity_id, child_entity_id, specifier_entity_id))
                except sqlite3.IntegrityError as ie:
                     print(f"Warning: Skipping duplicate relationship for parent {current_entity_id}, index {index} due to constraint: {ie}", file=sys.stderr)
                except Exception as e:
                    print(f"Error processing list/tuple element at index {index} ({value}): {e}", file=sys.stderr)
                    raise

    return current_entity_id


def load_from_py_file(py_file_path):
    """Loads 'data_structure' variable from a .py file."""
    module_name = f"structure_module_{os.path.basename(py_file_path).replace('.py', '')}_{os.getpid()}" # Add PID for uniqueness
    try:
        spec = importlib.util.spec_from_file_location(module_name, py_file_path)
        if spec is None or spec.loader is None:
             raise ImportError(f"Could not load spec for module at '{py_file_path}'")
        structure_module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = structure_module # Handle potential relative imports
        spec.loader.exec_module(structure_module)
        if hasattr(structure_module, 'data_structure'):
            return structure_module.data_structure
        else:
            raise AttributeError(f"'data_structure' variable not found in '{py_file_path}'")
    finally:
        if module_name in sys.modules:
            del sys.modules[module_name] # Clean up


def import_directory(dir_path):
    """Imports a directory hierarchy into the database."""
    global import_cache, dir_entity_cache, _db_cursor, _type_map

    print(f"Importing directory structure from: {dir_path}")
    abs_root_path = os.path.abspath(dir_path)

    # Check if root already exists (e.g., re-importing into existing DB)
    _db_cursor.execute("SELECT id FROM Entity WHERE entity_type_id = ? AND value_str = ?", (_type_map['Object'], f"dir:{abs_root_path}"))
    existing_root = _db_cursor.fetchone()

    if existing_root:
        root_dir_entity_id = existing_root[0]
        print(f"Found existing root entity {root_dir_entity_id} for directory '{abs_root_path}'")
        # Populate dir_entity_cache by querying existing structure if needed (complex)
        # For simplicity, we'll just use the root ID and assume subdirs will be created if missing.
        dir_entity_cache[abs_root_path] = root_dir_entity_id

    else:
        # Create root 'Object' entity for the top-level directory, maybe store path?
        # Storing path might be useful but violates pure structure idea. Let's stick to simple Object.
        _db_cursor.execute("INSERT INTO Entity (entity_type_id) VALUES (?)", (_type_map['Object'],))
        root_dir_entity_id = _db_cursor.lastrowid
        dir_entity_cache[abs_root_path] = root_dir_entity_id
        print(f"Created root entity {root_dir_entity_id} for directory '{abs_root_path}'")


    for dirpath, dirnames, filenames in os.walk(dir_path, topdown=True):
        abs_current_dir_path = os.path.abspath(dirpath)
        print(f"Processing directory: {abs_current_dir_path}")

        try:
            parent_dir_entity_id = dir_entity_cache[abs_current_dir_path]
        except KeyError:
            print(f"Error: Could not find parent entity for '{abs_current_dir_path}'. Skipping.", file=sys.stderr)
            dirnames[:] = [] # Don't descend further
            continue

        # Process Subdirectories
        current_subdirs = {}
        for dname in dirnames:
            abs_subdir_path = os.path.abspath(os.path.join(dirpath, dname))
            if abs_subdir_path not in dir_entity_cache: # Avoid creating duplicates if walking over symlinks etc.
                _db_cursor.execute("INSERT INTO Entity (entity_type_id) VALUES (?)", (_type_map['Object'],))
                subdir_entity_id = _db_cursor.lastrowid
                dir_entity_cache[abs_subdir_path] = subdir_entity_id
                print(f"  Created entity {subdir_entity_id} for subdir '{dname}'")
            else:
                subdir_entity_id = dir_entity_cache[abs_subdir_path]
                print(f"  Found existing entity {subdir_entity_id} for subdir '{dname}'")

            current_subdirs[dname] = subdir_entity_id # Track for linking

        # Link subdirectories discovered in this pass
        for dname, subdir_entity_id in current_subdirs.items():
             import_cache.clear()
             try:
                 specifier_entity_id = import_node(dname) # Directory name as specifier
             except Exception as e:
                 print(f"  Error creating specifier entity for dir '{dname}': {e}. Skipping relationship.", file=sys.stderr)
                 continue
             try:
                 _db_cursor.execute("""
                     INSERT INTO Relationship (parent_entity_id, child_entity_id, relationship_specifier_id)
                     VALUES (?, ?, ?)
                 """, (parent_dir_entity_id, subdir_entity_id, specifier_entity_id))
             except sqlite3.IntegrityError:
                 # Ignore if relationship already exists (e.g., re-run)
                 pass
             except Exception as e:
                 print(f"  Error linking subdir '{dname}': {e}", file=sys.stderr)


        # Process Files
        for filename in filenames:
            file_path = os.path.join(dirpath, filename)
            name, ext = os.path.splitext(filename)
            ext = ext.lower()

            content_data = None
            print(f"  Processing file: {filename}")

            if ext == '.py':
                try: content_data = load_from_py_file(file_path)
                except Exception as e: print(f"  Error loading Python file '{filename}': {e}. Skipping.", file=sys.stderr); continue
            elif ext == '.json':
                try:
                    with open(file_path, 'r', encoding='utf-8') as f: content_data = json.load(f)
                except Exception as e: print(f"  Error loading JSON file '{filename}': {e}. Skipping.", file=sys.stderr); continue
            else:
                print(f"  Skipping unsupported file type: {filename}")
                continue

            import_cache.clear()
            try: content_entity_id = import_node(content_data)
            except Exception as e: print(f"  Error importing content from '{filename}': {e}. Skipping.", file=sys.stderr); continue

            import_cache.clear()
            try: specifier_entity_id = import_node(name) # Filename without ext
            except Exception as e: print(f"  Error creating specifier entity for filename '{name}': {e}. Skipping.", file=sys.stderr); continue

            try:
                _db_cursor.execute("""
                    INSERT INTO Relationship (parent_entity_id, child_entity_id, relationship_specifier_id)
                    VALUES (?, ?, ?)
                """, (parent_dir_entity_id, content_entity_id, specifier_entity_id))
                print(f"    Linked content entity {content_entity_id} ('{name}' ID {specifier_entity_id})")
            except sqlite3.IntegrityError:
                 # Ignore if relationship already exists
                 pass
            except Exception as e:
                 print(f"  Error linking file content '{filename}': {e}", file=sys.stderr)

    return root_dir_entity_id


def import_main(input_path, db_file=DB_FILE):
    """Main function to handle import of file or directory."""
    global import_cache, dir_entity_cache, _db_cursor, _type_map

    conn = None
    try:
        conn = sqlite3.connect(db_file)
        conn.execute("PRAGMA foreign_keys = ON;")
        _db_cursor = conn.cursor()

        _type_map = get_entity_type_map(_db_cursor)
        if not _type_map:
            print(f"Error: EntityType table is empty/missing in '{db_file}'. Run create_schema.py first.", file=sys.stderr)
            sys.exit(1)

        root_imported_id = None
        conn.execute("BEGIN TRANSACTION;")

        if os.path.isdir(input_path):
            dir_entity_cache = {}
            root_imported_id = import_directory(input_path)
        elif os.path.isfile(input_path):
            # ... (Single file loading logic - same as previous version) ...
            name, ext = os.path.splitext(input_path); ext = ext.lower(); data_to_import = None
            print(f"Importing single file: {input_path}")
            if ext == '.py':
                try: data_to_import = load_from_py_file(input_path)
                except Exception as e: print(f"Error loading Python file: {e}", file=sys.stderr); conn.rollback(); sys.exit(1)
            elif ext == '.json':
                try:
                    with open(input_path, 'r', encoding='utf-8') as f: data_to_import = json.load(f)
                except Exception as e: print(f"Error loading JSON file: {e}", file=sys.stderr); conn.rollback(); sys.exit(1)
            else: print(f"Error: Unsupported single file type '{ext}'.", file=sys.stderr); conn.rollback(); sys.exit(1)
            import_cache = {};
            try: root_imported_id = import_node(data_to_import)
            except Exception as e: print(f"Error during import of file content: {e}", file=sys.stderr); conn.rollback(); sys.exit(1)
        else:
            print(f"Error: Input path '{input_path}' is not a file or directory.", file=sys.stderr)
            sys.exit(1)

        conn.commit()
        print(f"\nImport completed successfully.")
        if root_imported_id is not None:
            print(f"Root Entity ID of imported structure/directory: {root_imported_id}")

    except sqlite3.Error as e:
        print(f"\nDatabase error during import: {e}", file=sys.stderr)
        if conn: conn.rollback()
        sys.exit(1)
    except Exception as e:
        print(f"\nAn unexpected error occurred during import: {e}", file=sys.stderr)
        traceback.print_exc()
        if conn: conn.rollback()
        sys.exit(1)
    finally:
        import_cache = {}; dir_entity_cache = {}; _db_cursor = None; _type_map = None
        if conn: conn.close(); print("Database connection closed.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Import data from file or directory hierarchy into SQLite DB.")
    parser.add_argument("input_path", help="Path to the input file (.py or .json) or directory.")
    parser.add_argument("-db", "--database", default=DB_FILE, help=f"Path to the SQLite DB file (default: {DB_FILE}).")
    args = parser.parse_args()
    if not os.path.exists(args.input_path):
        print(f"Error: Input path does not exist: {args.input_path}", file=sys.stderr)
        sys.exit(1)
    import_main(args.input_path, args.database)
