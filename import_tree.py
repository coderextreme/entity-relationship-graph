#!/usr/bin/env python3
"""
import_data.py - Imports Python object graphs into the SQLite database.
"""

import sqlite3
import argparse
import os
import json
import importlib.util
import sys
import collections
from pprint import pprint

def get_entity_type_map(cursor):
    """Get a mapping of entity type names to IDs."""
    cursor.execute("SELECT id, name FROM EntityType")
    return {name: type_id for type_id, name in cursor.fetchall()}

def import_file(file_path, cursor, entity_type_map):
    """Import data from a single file (.py or .json)."""
    _, ext = os.path.splitext(file_path)
    ext = ext.lower()
    
    data = None
    
    try:
        if ext == '.py':
            # Import the Python file
            module_name = os.path.basename(file_path).replace('.py', '')
            spec = importlib.util.spec_from_file_location(module_name, file_path)
            if spec is None:
                raise ImportError(f"Failed to load module specification from {file_path}")
            
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            
            if hasattr(module, 'data_structure'):
                data = module.data_structure
            else:
                print(f"Warning: No 'data_structure' variable found in {file_path}")
                return None
        
        elif ext == '.json':
            # Load the JSON file
            with open(file_path, 'r') as f:
                data = json.load(f)
        else:
            print(f"Unsupported file extension: {ext}")
            return None
    
    except Exception as e:
        print(f"Error importing file {file_path}: {e}")
        return None
    
    # Import the data into the database
    return import_node(data, cursor, entity_type_map)

def import_directory(dir_path, cursor, entity_type_map):
    """Import all .py and .json files from a directory and its subdirectories."""
    # Create a root directory object
    root_entity_id = import_node({}, cursor, entity_type_map)  # Empty dict to represent directory
    
    # Keep track of path to entity ID mapping for linking subdirectories
    path_to_entity_id = {dir_path: root_entity_id}
    
    for root, dirs, files in os.walk(dir_path):
        parent_entity_id = path_to_entity_id[root]
        
        # Process subdirectories first
        for dir_name in dirs:
            dir_path = os.path.join(root, dir_name)
            # Create an entity for the subdirectory
            subdir_entity_id = import_node({}, cursor, entity_type_map)
            path_to_entity_id[dir_path] = subdir_entity_id
            
            # Create a relationship between parent dir and subdir
            dir_name_entity_id = import_node(dir_name, cursor, entity_type_map)
            create_relationship(parent_entity_id, subdir_entity_id, dir_name_entity_id, cursor)
        
        # Process files
        for file_name in files:
            file_path = os.path.join(root, file_name)
            _, ext = os.path.splitext(file_name)
            ext = ext.lower()
            
            if ext not in ['.py', '.json']:
                continue
            
            # Import the file's data
            file_data_entity_id = import_file(file_path, cursor, entity_type_map)
            
            if file_data_entity_id is not None:
                # Use the file name (without extension) as the relationship specifier
                base_name = os.path.basename(file_path).replace(ext, '')
                specifier_entity_id = import_node(base_name, cursor, entity_type_map)
                
                # Create relationship between parent directory and file data
                create_relationship(parent_entity_id, file_data_entity_id, specifier_entity_id, cursor)
    
    return root_entity_id

def import_node(node, cursor, entity_type_map, visited=None):
    """
    Recursively import a Python object into the database.
    Returns the entity ID of the imported node.
    """
    if visited is None:
        visited = {}
    
    # Check for already visited objects to handle cycles
    node_id = id(node)
    if node_id in visited:
        return visited[node_id]
    
    entity_id = None
    
    # Determine the entity type and create the entity
    if isinstance(node, dict):
        # Handle OrderedDict specifically
        if isinstance(node, collections.OrderedDict):
            entity_id = create_entity(entity_type_map['Version'], None, None, None, cursor)
        else:
            entity_id = create_entity(entity_type_map['Object'], None, None, None, cursor)
        
        # Mark as visited before processing children to handle cycles
        visited[node_id] = entity_id
        
        # Process all key-value pairs
        for key, value in node.items():
            # Import the key as an entity
            key_entity_id = import_node(key, cursor, entity_type_map, visited)
            
            # Import the value as an entity
            value_entity_id = import_node(value, cursor, entity_type_map, visited)
            
            # Create relationship between parent entity and value entity
            create_relationship(entity_id, value_entity_id, key_entity_id, cursor)
    
    elif isinstance(node, (list, tuple)):
        entity_id = create_entity(entity_type_map['Array'], None, None, None, cursor)
        
        # Mark as visited before processing children to handle cycles
        visited[node_id] = entity_id
        
        # Process all items
        for i, item in enumerate(node):
            # Import the index as an entity
            index_entity_id = import_node(i, cursor, entity_type_map, visited)
            
            # Import the item as an entity
            item_entity_id = import_node(item, cursor, entity_type_map, visited)
            
            # Create relationship between parent entity and item entity
            create_relationship(entity_id, item_entity_id, index_entity_id, cursor)
    
    elif isinstance(node, str):
        entity_id = create_entity(entity_type_map['String'], node, None, None, cursor)
    
    elif isinstance(node, (int, float)):
        entity_id = create_entity(entity_type_map['Number'], None, node, None, cursor)
    
    elif isinstance(node, bool):
        entity_id = create_entity(entity_type_map['Boolean'], None, None, 1 if node else 0, cursor)
    
    elif node is None:
        entity_id = create_entity(entity_type_map['Null'], None, None, None, cursor)
    
    else:
        # For unsupported types, convert to string
        str_value = str(node)
        entity_id = create_entity(entity_type_map['String'], f"<{type(node).__name__}: {str_value}>", None, None, cursor)
    
    # Return the ID of the created entity
    return entity_id

def create_entity(entity_type_id, value_str, value_num, value_bool, cursor):
    """Create an entity in the database and return its ID."""
    cursor.execute(
        "INSERT INTO Entity (entity_type_id, value_str, value_num, value_bool) VALUES (?, ?, ?, ?)",
        (entity_type_id, value_str, value_num, value_bool)
    )
    return cursor.lastrowid

def create_relationship(parent_entity_id, child_entity_id, relationship_specifier_id, cursor):
    """Create a relationship between two entities in the database."""
    try:
        cursor.execute(
            """
            INSERT INTO Relationship (parent_entity_id, child_entity_id, relationship_specifier_id)
            VALUES (?, ?, ?)
            """,
            (parent_entity_id, child_entity_id, relationship_specifier_id)
        )
    except sqlite3.IntegrityError:
        # Handle case where the relationship already exists (unique constraint violation)
        # This could happen if the same key is used multiple times in a dictionary
        print(f"Warning: Skipping duplicate relationship: parent={parent_entity_id}, "
              f"specifier={relationship_specifier_id}")

def main():
    parser = argparse.ArgumentParser(description='Import data into the object graph storage system.')
    parser.add_argument('path', help='Path to a .py file, .json file, or a directory')
    parser.add_argument('--db_path', default='generic_store.db', help='Path to the SQLite database file')
    
    args = parser.parse_args()
    
    # Check if the database exists
    if not os.path.exists(args.db_path):
        print(f"Error: Database file not found: {args.db_path}")
        print("Please run create_schema.py first to create the database.")
        return
    
    # Connect to the database
    conn = sqlite3.connect(args.db_path)
    cursor = conn.cursor()
    
    # Enable foreign key constraints
    cursor.execute("PRAGMA foreign_keys = ON")
    
    # Get entity type map
    entity_type_map = get_entity_type_map(cursor)
    
    try:
        # Begin transaction
        conn.execute("BEGIN TRANSACTION")
        
        # Import data based on the input path
        if os.path.isfile(args.path):
            root_entity_id = import_file(args.path, cursor, entity_type_map)
            if root_entity_id is None:
                raise ValueError(f"Failed to import file: {args.path}")
        elif os.path.isdir(args.path):
            root_entity_id = import_directory(args.path, cursor, entity_type_map)
        else:
            raise ValueError(f"Path does not exist or is not accessible: {args.path}")
        
        # Commit the transaction
        conn.commit()
        print(f"Successfully imported data with root entity ID: {root_entity_id}")
    
    except Exception as e:
        # Rollback the transaction in case of error
        conn.rollback()
        print(f"Error during import: {e}")
    
    finally:
        # Close the database connection
        conn.close()

if __name__ == "__main__":
    main()
