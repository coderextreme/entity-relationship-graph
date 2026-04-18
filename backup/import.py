import sqlite3
import json
import argparse
import sys
import os
import importlib.util
from collections import OrderedDict

DB_FILE = "generic_store.db"

# Cache for Python object identity -> entity_id within a single structure import
# Needs to be cleared before importing each new file content or root object
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
    Recursively imports any Python node (representing a JS-like structure)
    into the database using the global cursor and type map.
    Handles cycles via global import_cache.
    Returns the entity_id of the imported node.
    """
    global import_cache, _db_cursor, _type_map

    if _db_cursor is None or _type_map is None:
         raise RuntimeError("Database cursor or type map not initialized.")

    # --- Cache Check ---
    try:
        # Use id() for object identity, handle potential unhashable types (though rare for standard structures)
        node_id = id(node)
        if node_id in import_cache:
            return import_cache[node_id]
    except TypeError:
        # If id() fails (highly unusual), we cannot use identity caching for this node
        node_id = None
        pass # Proceed without caching for this specific node if id() fails

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
        entity_type_id = _type_map['Array'] # Storing tuples as Arrays for simplicity
        # Could add a 'Tuple' type if differentiation is critical
    elif node_type is str:
        entity_type_id = _type_map['String']
        value_str = node
    # Important: Check bool before int as bool is subclass of int
    elif node_type is bool:
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

    # --- Recurse for Containers and Create Relationships ---
    container_type_ids = (_type_map['Object'], _type_map['Array'], _type_map['Version'])
    if entity_type_id in container_type_ids:
        if entity_type_id == _type_map['Object'] or entity_type_id == _type_map['Version']:
            # Handles dict and OrderedDict ('Version')
            for key, value in node.items():
                try:
                    # Import key (specifier) and value (child)
                    specifier_entity_id = import_node(key)
                    child_entity_id = import_node(value)
                    # Create relationship
                    _db_cursor.execute("""
                        INSERT INTO Relationship (parent_entity_id, child_entity_id, relationship_specifier_id)
                        VALUES (?, ?, ?)
                    """, (current_entity_id, child_entity_id, specifier_entity_id))
                except sqlite3.IntegrityError as ie:
                    # Likely UNIQUE constraint violation (parent, specifier) - indicates duplicate key?
                    print(f"Warning: Skipping duplicate relationship for parent {current_entity_id}, specifier '{key}' due to constraint: {ie}", file=sys.stderr)
                except Exception as e:
                    print(f"Error processing dict/version item ({key}: {value}): {e}", file=sys.stderr)
                    raise # Re-raise to potentially rollback transaction

        elif entity_type_id == _type_map['Array']:
            # Handles list and tuple (stored as 'Array')
            for index, value in enumerate(node):
                try:
                    # Import index (specifier - creates 'Number' entity) and value (child)
                    specifier_entity_id = import_node(index) # Index is always an int here
                    child_entity_id = import_node(value)
                    # Create relationship
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
    module_name = f"structure_module_{os.path.basename(py_file_path).replace('.py', '')}"
    try:
        spec = importlib.util.spec_from_file_location(module_name, py_file_path)
        if spec is None or spec.loader is None:
             raise ImportError(f"Could not load spec for module at '{py_file_path}'")
        structure_module = importlib.util.module_from_spec(spec)
        # Add module to sys.modules temporarily to handle potential relative imports within the loaded file
        sys.modules[module_name] = structure_module
        spec.loader.exec_module(structure_module)
        if hasattr(structure_module, 'data_structure'):
            return structure_module.data_structure
        else:
            raise AttributeError(f"'data_structure' variable not found in '{py_file_path}'")
    finally:
        # Clean up module from sys.modules
        if module_name in sys.modules:
            del sys.modules[module_name]


def import_directory(dir_path):
    """Imports a directory hierarchy into the database."""
    global import_cache, dir_entity_cache, _db_cursor, _type_map

    print(f"Importing directory structure from: {dir_path}")

    # --- Create root entity for the top-level directory ---
    abs_root_path = os.path.abspath(dir_path)
    _db_cursor.execute("INSERT INTO Entity (entity_type_id) VALUES (?)", (_type_map['Object'],))
    root_dir_entity_id = _db_cursor.lastrowid
    dir_entity_cache[abs_root_path] = root_dir_entity_id
    print(f"Created root entity {root_dir_entity_id} for directory '{abs_root_path}'")

    # --- Walk the directory tree ---
    for dirpath, dirnames, filenames in os.walk(dir_path, topdown=True):
        abs_current_dir_path = os.path.abspath(dirpath)
        print(f"Processing directory: {abs_current_dir_path}")

        # Get parent directory entity ID (should exist from previous iteration or root creation)
        try:
            parent_dir_entity_id = dir_entity_cache[abs_current_dir_path]
        except KeyError:
            print(f"Error: Could not find parent directory entity for '{abs_current_dir_path}'. Skipping.", file=sys.stderr)
            # Clear dirnames to prevent descending further down this broken path
            dirnames[:] = []
            continue

        # --- Process Subdirectories ---
        for dname in dirnames:
            abs_subdir_path = os.path.abspath(os.path.join(dirpath, dname))
            # Create 'Object' entity for the subdirectory
            _db_cursor.execute("INSERT INTO Entity (entity_type_id) VALUES (?)", (_type_map['Object'],))
            subdir_entity_id = _db_cursor.lastrowid
            dir_entity_cache[abs_subdir_path] = subdir_entity_id # Cache it
            print(f"  Created entity {subdir_entity_id} for subdir '{dname}'")

            # Create 'String' entity for the directory name (specifier)
            import_cache.clear() # Clear structure cache before importing specifier
            try:
                specifier_entity_id = import_node(dname) # Import the directory name string
            except Exception as e:
                print(f"  Error creating specifier entity for dir '{dname}': {e}. Skipping relationship.", file=sys.stderr)
                continue

            # Create relationship: parent_dir -> subdir
            try:
                _db_cursor.execute("""
                    INSERT INTO Relationship (parent_entity_id, child_entity_id, relationship_specifier_id)
                    VALUES (?, ?, ?)
                """, (parent_dir_entity_id, subdir_entity_id, specifier_entity_id))
            except sqlite3.IntegrityError as ie:
                print(f"  Warning: Skipping relationship for subdir '{dname}' due to constraint: {ie}", file=sys.stderr)


        # --- Process Files ---
        for filename in filenames:
            file_path = os.path.join(dirpath, filename)
            name, ext = os.path.splitext(filename)
            ext = ext.lower()

            content_data = None
            print(f"  Processing file: {filename}")

            # Load content based on extension
            if ext == '.py':
                try:
                    content_data = load_from_py_file(file_path)
                except Exception as e:
                    print(f"  Error loading Python file '{filename}': {e}. Skipping.", file=sys.stderr)
                    continue
            elif ext == '.json':
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        content_data = json.load(f)
                except json.JSONDecodeError as e:
                    print(f"  Error decoding JSON file '{filename}': {e}. Skipping.", file=sys.stderr)
                    continue
                except IOError as e:
                    print(f"  Error reading file '{filename}': {e}. Skipping.", file=sys.stderr)
                    continue
                except Exception as e:
                     print(f"  Unexpected error loading JSON file '{filename}': {e}. Skipping.", file=sys.stderr)
                     continue
            else:
                print(f"  Skipping unsupported file type: {filename}")
                continue # Skip non .py or .json files

            # Import the loaded content data
            import_cache.clear() # Clear structure cache before importing file content
            try:
                content_entity_id = import_node(content_data)
            except Exception as e:
                 print(f"  Error importing content from '{filename}': {e}. Skipping relationship.", file=sys.stderr)
                 continue

            # Create specifier ('String' entity for filename without extension)
            import_cache.clear()
            try:
                 specifier_entity_id = import_node(name) # Filename without ext
            except Exception as e:
                 print(f"  Error creating specifier entity for filename '{name}': {e}. Skipping relationship.", file=sys.stderr)
                 continue

            # Create relationship: parent_dir -> file_content
            try:
                _db_cursor.execute("""
                    INSERT INTO Relationship (parent_entity_id, child_entity_id, relationship_specifier_id)
                    VALUES (?, ?, ?)
                """, (parent_dir_entity_id, content_entity_id, specifier_entity_id))
                print(f"    Linked content entity {content_entity_id} with specifier '{name}' (ID {specifier_entity_id})")
            except sqlite3.IntegrityError as ie:
                print(f"  Warning: Skipping relationship for file content '{filename}' due to constraint: {ie}", file=sys.stderr)

    return root_dir_entity_id # Return the entity ID of the top-level directory


def import_main(input_path, db_file=DB_FILE):
    """Main function to handle import of file or directory."""
    global import_cache, dir_entity_cache, _db_cursor, _type_map

    conn = None
    try:
        conn = sqlite3.connect(db_file)
        conn.execute("PRAGMA foreign_keys = ON;")
        _db_cursor = conn.cursor() # Set global cursor

        # Fetch type map once
        _type_map = get_entity_type_map(_db_cursor)
        if not _type_map:
            print(f"Error: EntityType table is empty/missing in '{db_file}'. Run create_schema.py first.", file=sys.stderr)
            sys.exit(1)

        root_imported_id = None
        conn.execute("BEGIN TRANSACTION;") # Start transaction

        if os.path.isdir(input_path):
            dir_entity_cache = {} # Reset directory cache
            root_imported_id = import_directory(input_path)

        elif os.path.isfile(input_path):
            name, ext = os.path.splitext(input_path)
            ext = ext.lower()
            data_to_import = None
            print(f"Importing single file: {input_path}")

            if ext == '.py':
                try:
                    data_to_import = load_from_py_file(input_path)
                except Exception as e:
                    print(f"Error loading Python file: {e}", file=sys.stderr)
                    conn.rollback()
                    sys.exit(1)
            elif ext == '.json':
                try:
                    with open(input_path, 'r', encoding='utf-8') as f:
                        data_to_import = json.load(f)
                except Exception as e:
                    print(f"Error loading JSON file: {e}", file=sys.stderr)
                    conn.rollback()
                    sys.exit(1)
            else:
                print(f"Error: Unsupported single file type '{ext}'. Must be .py or .json.", file=sys.stderr)
                conn.rollback()
                sys.exit(1)

            # Import the loaded structure
            import_cache = {} # Reset structure cache
            try:
                root_imported_id = import_node(data_to_import)
            except Exception as e:
                 print(f"Error during import of file content: {e}", file=sys.stderr)
                 conn.rollback()
                 sys.exit(1)

        else:
            print(f"Error: Input path '{input_path}' is neither a file nor a directory.", file=sys.stderr)
            sys.exit(1) # No need to rollback if nothing happened

        conn.commit() # Commit transaction
        print(f"\nImport completed successfully.")
        if root_imported_id is not None:
            print(f"Root Entity ID of imported structure/directory: {root_imported_id}")

    except sqlite3.Error as e:
        print(f"\nDatabase error during import: {e}", file=sys.stderr)
        if conn: conn.rollback()
        sys.exit(1)
    except Exception as e:
        print(f"\nAn unexpected error occurred during import: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        if conn: conn.rollback()
        sys.exit(1)
    finally:
        # Clear global state
        import_cache = {}
        dir_entity_cache = {}
        _db_cursor = None
        _type_map = None
        if conn:
            conn.close()
            print("Database connection closed.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Import data from JSON/Python file or directory hierarchy into SQLite DB.")
    parser.add_argument("input_path", help="Path to the input file (.py or .json) or directory.")
    parser.add_argument("-db", "--database", default=DB_FILE, help=f"Path to the SQLite database file (default: {DB_FILE}).")
    args = parser.parse_args()

    if not os.path.exists(args.input_path):
        print(f"Error: Input path does not exist: {args.input_path}", file=sys.stderr)
        sys.exit(1)

    import_main(args.input_path, args.database)
