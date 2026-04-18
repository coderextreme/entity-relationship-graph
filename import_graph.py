#!/usr/bin/env python3
import sqlite3
import argparse
import os
import sys
import json
import importlib.util
from collections import OrderedDict
import pprint # Keep for potential debugging, though not used for export here

# --- Database Configuration ---
DEFAULT_DB_PATH = 'generic_store.db'

# --- Type Mapping ---
# Map Python types to database EntityType names
TYPE_MAP = {
    dict: 'Object',
    list: 'Array',
    tuple: 'Array', # Treat tuples like lists for storage/graphing
    str: 'String',
    int: 'Number',
    float: 'Number',
    bool: 'Boolean',
    type(None): 'Null',
}

# --- Helper Functions ---

def get_entity_type_id(cursor, type_name):
    """Gets the ID for a given EntityType name."""
    cursor.execute("SELECT id FROM EntityType WHERE name = ?", (type_name,))
    row = cursor.fetchone()
    if row:
        return row[0]
    else:
        raise ValueError(f"EntityType '{type_name}' not found in database.")

def get_primitive_entity(cursor, entity_type_id, value, primitive_cache):
    """
    Checks cache and DB for an existing primitive entity.
    Returns existing entity_id or None if not found.
    Updates cache if found in DB but not cache.
    """
    type_name = next((name for name, et_id in primitive_cache['type_ids'].items() if et_id == entity_type_id), None)
    if not type_name:
         raise ValueError(f"Cannot map entity_type_id {entity_type_id} back to a name for caching.") # Should not happen

    cache_key = (entity_type_id, value)
    if value is None and type_name != 'Null': # Ensure None is only cached for Null type itself
        cache_key = (entity_type_id, "___NONE_MARKER___") # Avoid collision with actual Null type

    # 1. Check in-memory cache first
    cached_id = primitive_cache['data'].get(cache_key)
    if cached_id:
        # print(f"  CACHE HIT: Primitive '{type_name}' Value: {value} -> ID: {cached_id}")
        return cached_id

    # 2. If not in cache, check database
    sql = f"SELECT id FROM Entity WHERE entity_type_id = ?"
    params = [entity_type_id]

    if type_name == 'String':
        sql += " AND value_str = ?"
        params.append(value)
    elif type_name == 'Number':
        sql += " AND value_num = ?"
        params.append(value)
    elif type_name == 'Boolean':
        sql += " AND value_bool = ?"
        params.append(1 if value else 0)
    elif type_name == 'Null':
        sql += " AND value_str IS NULL AND value_num IS NULL AND value_bool IS NULL"
        # No extra params needed for Null check based only on type_id and null values
    else:
        # Should not happen for primitives
         return None # Or raise error

    # print(f"  CACHE MISS: Querying DB for {type_name} Value: {value}")
    cursor.execute(sql, params)
    row = cursor.fetchone()

    if row:
        existing_id = row[0]
        # print(f"    DB HIT: Found existing ID: {existing_id}. Caching.")
        primitive_cache['data'][cache_key] = existing_id # Add to cache
        return existing_id
    else:
        # print(f"    DB MISS: Primitive not found in DB.")
        return None # Not found in DB

def insert_entity(cursor, entity_type_id, value_str=None, value_num=None, value_bool=None):
    """Inserts a new entity and returns its ID."""
    sql = """
        INSERT INTO Entity (entity_type_id, value_str, value_num, value_bool)
        VALUES (?, ?, ?, ?)
    """
    # Convert boolean True/False to 1/0 for storage
    db_value_bool = None
    if isinstance(value_bool, bool):
        db_value_bool = 1 if value_bool else 0

    cursor.execute(sql, (entity_type_id, value_str, value_num, db_value_bool))
    return cursor.lastrowid

def insert_relationship(cursor, parent_id, child_id, specifier_id):
    """Inserts a relationship, handling potential unique constraint violations."""
    try:
        sql = """
            INSERT INTO Relationship (parent_entity_id, child_entity_id, relationship_specifier_id)
            VALUES (?, ?, ?)
        """
        cursor.execute(sql, (parent_id, child_id, specifier_id))
        return cursor.lastrowid
    except sqlite3.IntegrityError as e:
        if "UNIQUE constraint failed: Relationship.parent_entity_id, Relationship.relationship_specifier_id" in str(e):
            print(f"Warning: Relationship from parent {parent_id} via specifier {specifier_id} already exists. Skipping insertion.", file=sys.stderr)
            # Optionally, fetch the existing relationship ID if needed, otherwise return None or 0
            # cursor.execute("SELECT id FROM Relationship WHERE parent_entity_id = ? AND relationship_specifier_id = ?", (parent_id, specifier_id))
            # existing_rel = cursor.fetchone()
            # return existing_rel[0] if existing_rel else None
            return None
        else:
            # Re-raise other integrity errors
            raise e


def import_node(cursor, node, visited_objects, primitive_cache):
    """
    Recursively imports a Python object/data structure into the database.
    Handles object identity (cycles) and primitive value sharing.
    Returns the entity_id of the imported node.
    """
    node_type = type(node)
    node_id_in_python = id(node)

    # 1. Handle Object Identity (Cycles and Shared Instances within *this* structure)
    # Only cache containers or complex objects by their Python ID
    is_container = node_type in (dict, list, tuple)
    if is_container and node_id_in_python in visited_objects:
        # print(f"  CYCLE/SHARED DETECTED: Reusing Entity ID {visited_objects[node_id_in_python]} for Python object ID {node_id_in_python}")
        return visited_objects[node_id_in_python]

    # Get DB Entity Type Name and ID
    entity_type_name = TYPE_MAP.get(node_type)
    if not entity_type_name:
        print(f"Warning: Unsupported Python type '{node_type.__name__}'. Storing as String representation.", file=sys.stderr)
        entity_type_name = 'String'
        node = repr(node) # Store the representation as a string
        node_type = str # Treat it as a string from now on
        # Fall through to primitive handling

    entity_type_id = primitive_cache['type_ids'].get(entity_type_name) # Use cached type IDs
    if not entity_type_id:
         raise ValueError(f"EntityType '{entity_type_name}' ID not found in cache.")


    entity_id = None

    # 2. Handle Containers (dict, list, tuple)
    if is_container:
        # Insert container entity (value columns are NULL)
        entity_id = insert_entity(cursor, entity_type_id)
        # print(f"  INSERTED CONTAINER: Type: {entity_type_name}, New Entity ID: {entity_id}, Python ID: {node_id_in_python}")
        visited_objects[node_id_in_python] = entity_id # Cache *before* recursing

        # Recursively import children/items and create relationships
        if entity_type_name == 'Object': # dict
            items_to_process = node.items()
            for key, value in items_to_process:
                try:
                    # Import the key (specifier)
                    specifier_id = import_node(cursor, key, visited_objects, primitive_cache)
                    # Import the value (child)
                    child_id = import_node(cursor, value, visited_objects, primitive_cache)
                    # Create relationship
                    insert_relationship(cursor, entity_id, child_id, specifier_id)
                except Exception as e:
                     print(f"Error processing item ({key!r}: {value!r}) in {entity_type_name} (ID: {entity_id}): {e}", file=sys.stderr)
                     # Decide whether to continue or stop

        elif entity_type_name == 'Array': # list or tuple
            for index, value in enumerate(node):
                try:
                    # Import the index (specifier) - always a Number
                    specifier_id = import_node(cursor, index, visited_objects, primitive_cache)
                    # Import the value (child)
                    child_id = import_node(cursor, value, visited_objects, primitive_cache)
                    # Create relationship
                    insert_relationship(cursor, entity_id, child_id, specifier_id)
                except Exception as e:
                     print(f"Error processing index {index} (value: {value!r}) in {entity_type_name} (ID: {entity_id}): {e}", file=sys.stderr)


    # 3. Handle Primitives (str, int, float, bool, None) - Implement Sharing
    else:
        value_to_store = node
        # Check cache/DB for existing primitive
        existing_id = get_primitive_entity(cursor, entity_type_id, value_to_store, primitive_cache)

        if existing_id:
            entity_id = existing_id # Reuse existing entity
             # print(f"  REUSING PRIMITIVE: Type: {entity_type_name}, Value: {value_to_store!r}, Using Entity ID: {entity_id}")
        else:
            # Insert new primitive entity
            value_str, value_num, value_bool = None, None, None
            if entity_type_name == 'String':
                value_str = value_to_store
            elif entity_type_name == 'Number':
                value_num = value_to_store
            elif entity_type_name == 'Boolean':
                value_bool = value_to_store # Will be converted to 1/0 in insert_entity
            elif entity_type_name == 'Null':
                pass # All value columns remain NULL

            entity_id = insert_entity(cursor, entity_type_id, value_str, value_num, value_bool)
            # print(f"  INSERTED PRIMITIVE: Type: {entity_type_name}, Value: {value_to_store!r}, New Entity ID: {entity_id}")

            # Add the newly inserted primitive to the cache
            cache_key = (entity_type_id, value_to_store)
            if value_to_store is None and entity_type_name != 'Null':
                 cache_key = (entity_type_id, "___NONE_MARKER___")
            primitive_cache['data'][cache_key] = entity_id
            # print(f"    Cached new primitive.")

    # Return the final entity_id (either newly created or reused)
    if entity_id is None:
         raise RuntimeError(f"Failed to obtain an entity ID for node: {node!r} (Type: {node_type})") # Should not happen
    return entity_id


def load_from_py(filepath):
    """Loads 'data_structure' variable from a .py file."""
    spec = importlib.util.spec_from_file_location("module.name", filepath)
    if not spec or not spec.loader:
         raise ImportError(f"Could not load spec for Python file: {filepath}")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
        if hasattr(module, 'data_structure'):
            return module.data_structure
        else:
            raise AttributeError(f"'data_structure' variable not found in {filepath}")
    except Exception as e:
        raise ImportError(f"Failed to execute or access data in {filepath}: {e}")


def import_directory_structure(cursor, dir_path, visited_objects, primitive_cache):
    """
    Imports a directory structure, creating 'Object' entities for directories
    and linking contents via 'String' specifiers (filenames/dirnames).
    Returns the entity_id of the root directory object.
    Uses path caching to handle potential symlink loops or redundant traversals if needed,
    though os.walk typically handles basic directory structures well.
    """
    print(f"Processing directory: {dir_path}")
    # Use absolute path for reliable caching key if implementing path caching
    abs_dir_path = os.path.abspath(dir_path)

    # This simple implementation doesn't explicitly cache paths, relies on os.walk behavior.
    # For complex scenarios with symlinks, a path cache might be needed:
    # if abs_dir_path in path_cache: return path_cache[abs_dir_path]

    # Create an 'Object' entity for the current directory
    dir_entity_id = insert_entity(cursor, primitive_cache['type_ids']['Object'])
    print(f"  Created 'Object' entity {dir_entity_id} for directory '{os.path.basename(abs_dir_path)}'")
    # path_cache[abs_dir_path] = dir_entity_id # Add to path cache if implementing

    # Use a temporary visited_objects specific to this directory structure import
    # to handle file/dir content cycles *within* this import unit.
    # Keep the global one for sharing *across* different files/dirs in the same run.
    local_visited = {} # Separate cache for items *within* this dir walk context if needed
                        # Using the main `visited_objects` allows sharing across dir tree.

    for root, dirs, files in os.walk(dir_path, topdown=True):
        # Important: Need to map the 'root' path back to its parent's entity ID
        # This requires a more complex traversal or keeping track of parent IDs.
        # Let's simplify: Create the TOP directory object, then link items directly under it.
        # A truly hierarchical DB representation requires passing parent_id down.

        # Find the parent directory entity ID for items in 'root'
        # For this simplified version, we only correctly link items in the *top* directory.
        # A full hierarchical import is significantly more complex.
        # We'll link all found files/subdirs to the initial `dir_entity_id`.
        parent_entity_id = dir_entity_id # Simplification for this example
        current_dir_name = os.path.basename(root)

        print(f"  Scanning subdir: {current_dir_name} (linking to entity {parent_entity_id})")


        # Process subdirectories
        for dirname in sorted(dirs): # Sort for deterministic order
             dir_fullpath = os.path.join(root, dirname)
             try:
                 print(f"    Processing subdirectory entry: {dirname}")
                 # Recursively import the subdirectory structure itself
                 # This creates nested objects, but linking them correctly requires tracking parent IDs.
                 # For simplification, we'll just create a placeholder or skip deep recursion here.
                 # Let's just link an 'Object' representing the subdir.
                 subdir_specifier_id = import_node(cursor, dirname, visited_objects, primitive_cache)
                 subdir_entity_id = insert_entity(cursor, primitive_cache['type_ids']['Object']) # Placeholder object
                 print(f"      Linking placeholder 'Object' {subdir_entity_id} for subdir '{dirname}' using specifier {subdir_specifier_id}")
                 insert_relationship(cursor, parent_entity_id, subdir_entity_id, subdir_specifier_id)
                 # To do this properly: child_id = import_directory_structure(cursor, dir_fullpath, visited_objects, primitive_cache, path_cache)
                 # insert_relationship(cursor, parent_entity_id, child_id, specifier_id)
             except Exception as e:
                 print(f"Error processing subdirectory {dirname}: {e}", file=sys.stderr)


        # Process files
        for filename in sorted(files): # Sort for deterministic order
            filepath = os.path.join(root, filename)
            basename, ext = os.path.splitext(filename)
            ext = ext.lower()

            # Use filename without extension as the relationship specifier (key)
            specifier_str = basename
            specifier_id = import_node(cursor, specifier_str, visited_objects, primitive_cache) # Key is a String

            try:
                print(f"    Processing file: {filename} (using specifier '{specifier_str}' -> ID {specifier_id})")
                data = None
                if ext == '.py':
                    data = load_from_py(filepath)
                elif ext == '.json':
                    with open(filepath, 'r', encoding='utf-8') as f:
                        data = json.load(f)

                if data is not None:
                    # Import the file's content
                    child_node_id = import_node(cursor, data, visited_objects, primitive_cache)
                    # Link file content under the current directory object
                    print(f"      Linking content entity {child_node_id} for file '{filename}' using specifier {specifier_id}")
                    insert_relationship(cursor, parent_entity_id, child_node_id, specifier_id)
                else:
                    print(f"      Skipping file {filename} (unsupported extension '{ext}')")

            except Exception as e:
                print(f"Error processing file {filepath}: {e}", file=sys.stderr)

         # Prune directories for os.walk if doing recursive calls to avoid re-processing
         # dirs[:] = [] # Add this inside the loop if handling subdirs recursively within import_directory_structure

        # BREAK after processing the top level for this simplified version
        break # Only process the immediate children of the starting dir_path


    return dir_entity_id


# --- Main Execution ---

def main():
    parser = argparse.ArgumentParser(description="Import Python data structures or directory hierarchies into an SQLite DB.")
    parser.add_argument("input_path", help="Path to a .py file, .json file, or a directory.")
    parser.add_argument("--db", default=DEFAULT_DB_PATH, help=f"Path to the SQLite database file (default: {DEFAULT_DB_PATH}).")
    args = parser.parse_args()

    if not os.path.exists(args.input_path):
        print(f"Error: Input path '{args.input_path}' not found.", file=sys.stderr)
        sys.exit(1)

    conn = None
    try:
        conn = sqlite3.connect(args.db)
        cursor = conn.cursor()
        cursor.execute("PRAGMA foreign_keys = ON;")

        # --- Pre-cache EntityType IDs ---
        cursor.execute("SELECT name, id FROM EntityType")
        entity_type_rows = cursor.fetchall()
        entity_type_ids = {name: id for name, id in entity_type_rows}
        print("Cached EntityType IDs.")

        # --- Initialize Caches ---
        # Visited objects cache: Tracks object identity within a structure load {python_id: entity_id}
        visited_objects_global = {}
        # Primitive cache: Tracks shared primitives across the entire import { (type_id, value): entity_id }
        primitive_cache = {
            'data': {},
            'type_ids': entity_type_ids # Include type IDs for easy lookup
        }
        # Path cache (optional, for complex directory structures with symlinks)
        # path_cache = {}


        # --- Begin Transaction ---
        conn.execute("BEGIN TRANSACTION;")
        print("Database transaction started.")

        root_entity_id = None

        try:
            if os.path.isdir(args.input_path):
                print(f"Importing directory structure from: {args.input_path}")
                root_entity_id = import_directory_structure(cursor, args.input_path, visited_objects_global, primitive_cache) # Removed path_cache arg for now
            elif os.path.isfile(args.input_path):
                print(f"Importing single file: {args.input_path}")
                _, ext = os.path.splitext(args.input_path)
                ext = ext.lower()
                data_to_import = None

                if ext == '.py':
                    data_to_import = load_from_py(args.input_path)
                elif ext == '.json':
                    with open(args.input_path, 'r', encoding='utf-8') as f:
                        data_to_import = json.load(f)
                else:
                    raise ValueError(f"Unsupported file extension: {ext}. Only .py and .json are supported.")

                if data_to_import is not None:
                     # Pass the global visited_objects cache
                    root_entity_id = import_node(cursor, data_to_import, visited_objects_global, primitive_cache)
                else:
                    print("No data loaded from file.")

            else:
                 raise ValueError(f"Input path '{args.input_path}' is not a file or directory.")

            # --- Commit Transaction ---
            conn.commit()
            print("Database transaction committed successfully.")
            if root_entity_id:
                 print(f"Import complete. Root entity ID: {root_entity_id}")
            else:
                 print("Import complete. No root entity ID generated (possibly empty input or structure).")


        except Exception as e:
            print(f"\nError during import: {e}", file=sys.stderr)
            print("Rolling back database transaction.")
            conn.rollback()
            sys.exit(1)

    except sqlite3.Error as e:
        print(f"Database error: {e}", file=sys.stderr)
        if conn:
            conn.rollback() # Rollback if connection was established but failed later
        sys.exit(1)
    except ImportError as e:
        print(f"Import error: {e}", file=sys.stderr)
        if conn:
            conn.rollback()
        sys.exit(1)
    except Exception as e:
        print(f"An unexpected error occurred: {e}", file=sys.stderr)
        if conn:
            conn.rollback()
        sys.exit(1)
    finally:
        if conn:
            conn.close()
            print("Database connection closed.")

if __name__ == "__main__":
    main()
