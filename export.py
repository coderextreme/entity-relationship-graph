#!/usr/bin/env python3
"""
export_data.py - Exports object graphs from the SQLite database in various formats.
"""

import sqlite3
import argparse
import os
import json
import pickle
import pprint
import collections
from typing import Dict, List, Any, Set, Tuple

def get_entity_type_map(cursor) -> Dict[int, str]:
    """Get a mapping of entity type IDs to type names."""
    cursor.execute("SELECT id, name FROM EntityType")
    return {type_id: name for type_id, name in cursor.fetchall()}

def find_root_entities(cursor) -> List[int]:
    """Find entities that are not referenced as children (potential root entities)."""
    cursor.execute("""
    SELECT id FROM Entity WHERE id NOT IN (
        SELECT DISTINCT child_entity_id FROM Relationship
    )
    """)
    return [row[0] for row in cursor.fetchall()]

def get_entity_info(entity_id: int, cursor) -> Dict[str, Any]:
    """Get information about an entity from the database."""
    cursor.execute("""
    SELECT e.entity_type_id, et.name, e.value_str, e.value_num, e.value_bool
    FROM Entity e
    JOIN EntityType et ON e.entity_type_id = et.id
    WHERE e.id = ?
    """, (entity_id,))
    
    row = cursor.fetchone()
    if not row:
        raise ValueError(f"Entity ID {entity_id} not found")
    
    type_id, type_name, value_str, value_num, value_bool = row
    
    return {
        'id': entity_id,
        'type_id': type_id,
        'type_name': type_name,
        'value_str': value_str,
        'value_num': value_num,
        'value_bool': value_bool
    }

def get_children(entity_id: int, cursor) -> List[Tuple[int, int, int]]:
    """Get all children of an entity along with their relationship specifiers."""
    cursor.execute("""
    SELECT child_entity_id, relationship_specifier_id, id
    FROM Relationship
    WHERE parent_entity_id = ?
    """, (entity_id,))
    
    return cursor.fetchall()

def rebuild_node(entity_id: int, cursor, entity_type_map: Dict[int, str], 
                built_objects: Dict[int, Any] = None, 
                in_progress: Set[int] = None) -> Any:
    """
    Recursively rebuild a Python object from the database.
    Uses caching to handle shared objects and cycles.
    """
    if built_objects is None:
        built_objects = {}
    
    if in_progress is None:
        in_progress = set()
    
    # Check if this entity has already been built
    if entity_id in built_objects:
        return built_objects[entity_id]
    
    # Check if we're in a cycle
    if entity_id in in_progress:
        return "[Circular Reference]"
    
    # Mark entity as in progress (for cycle detection)
    in_progress.add(entity_id)
    
    # Get entity information
    entity_info = get_entity_info(entity_id, cursor)
    type_name = entity_info['type_name']
    
    result = None
    
    # Handle different entity types
    if type_name == 'Object':
        result = {}
        # Cache the empty object to handle cycles
        built_objects[entity_id] = result
        
        # Get all children
        children = get_children(entity_id, cursor)
        
        for child_id, specifier_id, _ in children:
            # Rebuild the key
            key = rebuild_node(specifier_id, cursor, entity_type_map, built_objects, in_progress)
            
            # Handle non-hashable keys
            if not (isinstance(key, (str, int, float, bool, type(None))) or 
                    (isinstance(key, tuple) and all(isinstance(k, (str, int, float, bool, type(None))) for k in key))):
                print(f"Warning: Non-hashable key encountered: {key}, using string representation")
                key = str(key)
            
            # Rebuild the value
            value = rebuild_node(child_id, cursor, entity_type_map, built_objects, in_progress)
            
            # Add to the result dictionary
            result[key] = value
    
    elif type_name == 'Version':
        # Handle OrderedDict
        result = collections.OrderedDict()
        # Cache the empty OrderedDict to handle cycles
        built_objects[entity_id] = result
        
        # Get all children
        children = get_children(entity_id, cursor)
        
        # Sort children by relationship ID to maintain order
        children.sort(key=lambda x: x[2])
        
        for child_id, specifier_id, _ in children:
            # Rebuild the key
            key = rebuild_node(specifier_id, cursor, entity_type_map, built_objects, in_progress)
            
            # Handle non-hashable keys
            if not (isinstance(key, (str, int, float, bool, type(None))) or 
                    (isinstance(key, tuple) and all(isinstance(k, (str, int, float, bool, type(None))) for k in key))):
                print(f"Warning: Non-hashable key encountered: {key}, using string representation")
                key = str(key)
            
            # Rebuild the value
            value = rebuild_node(child_id, cursor, entity_type_map, built_objects, in_progress)
            
            # Add to the result OrderedDict
            result[key] = value
    
    elif type_name == 'Array':
        # Get all children
        children = get_children(entity_id, cursor)
        
        # Check if all indices are integers and form a continuous sequence
        indices = []
        for _, specifier_id, _ in children:
            spec_info = get_entity_info(specifier_id, cursor)
            if spec_info['type_name'] == 'Number' and spec_info['value_num'] is not None:
                idx = int(spec_info['value_num'])
                indices.append(idx)
        
        is_standard_array = (len(indices) == len(children) and 
                            sorted(indices) == list(range(min(indices), max(indices) + 1)))
        
        if is_standard_array:
            # Create a list with enough space
            result = [None] * (max(indices) + 1) if indices else []
            # Cache the list to handle cycles
            built_objects[entity_id] = result
            
            # Fill the list
            for child_id, specifier_id, _ in children:
                spec_info = get_entity_info(specifier_id, cursor)
                idx = int(spec_info['value_num'])
                value = rebuild_node(child_id, cursor, entity_type_map, built_objects, in_progress)
                result[idx] = value
        else:
            # Handle non-standard indices (convert to list of pairs)
            result = []
            # Cache the list to handle cycles
            built_objects[entity_id] = result
            
            for child_id, specifier_id, _ in children:
                key = rebuild_node(specifier_id, cursor, entity_type_map, built_objects, in_progress)
                value = rebuild_node(child_id, cursor, entity_type_map, built_objects, in_progress)
                result.append((key, value))
    
    elif type_name == 'String':
        result = entity_info['value_str']
    
    elif type_name == 'Number':
        result = entity_info['value_num']
        # Convert integers stored as floats back to int
        if isinstance(result, float) and result.is_integer():
            result = int(result)
    
    elif type_name == 'Boolean':
        result = bool(entity_info['value_bool'])
    
    elif type_name == 'Null':
        result = None
    
    else:
        raise ValueError(f"Unknown entity type: {type_name}")
    
    # Cache the built object
    built_objects[entity_id] = result
    
    # Remove entity from in-progress set
    in_progress.remove(entity_id)
    
    return result

def js_serialize(obj, visited=None):
    """
    Serialize a Python object to a JavaScript literal string.
    Handles circular references.
    """
    if visited is None:
        visited = set()
    
    obj_id = id(obj)
    
    # Check for circular references
    if obj_id in visited:
        return '"[Circular Reference]"'
    
    visited.add(obj_id)
    
    if isinstance(obj, dict):
        items = []
        for k, v in obj.items():
            # Convert non-string keys to strings
            if not isinstance(k, str):
                k = str(k)
                print(f"Warning: Non-string key {k} converted to string for JavaScript output")
            
            key_str = json.dumps(k)
            val_str = js_serialize(v, visited)
            items.append(f"{key_str}: {val_str}")
        
        result = "{" + ", ".join(items) + "}"
    
    elif isinstance(obj, list):
        items = [js_serialize(x, visited) for x in obj]
        result = "[" + ", ".join(items) + "]"
    
    elif isinstance(obj, str):
        result = json.dumps(obj)
    
    elif isinstance(obj, (int, float)):
        result = str(obj)
    
    elif isinstance(obj, bool):
        result = "true" if obj else "false"
    
    elif obj is None:
        result = "null"
    
    elif isinstance(obj, tuple):
        # Handle tuples as arrays
        items = [js_serialize(x, visited) for x in obj]
        result = "[" + ", ".join(items) + "]"
    
    else:
        # Handle other types by converting to string
        result = json.dumps(str(obj))
    
    visited.remove(obj_id)
    return result

def export_d3json(output_file, cursor, entity_type_map):
    """
    Export the database to a D3.js-compatible JSON format.
    Links will contain the relationship specifier information directly.
    """
    # Find potential root entities
    root_ids = find_root_entities(cursor)
    if not root_ids:
        raise ValueError("No root entities found in the database")
    
    # Get all entities that are either parents or children
    cursor.execute("""
    SELECT DISTINCT parent_entity_id AS entity_id FROM Relationship
    UNION
    SELECT DISTINCT child_entity_id AS entity_id FROM Relationship
    UNION
    SELECT id AS entity_id FROM Entity WHERE id IN ({})
    """.format(','.join(['?'] * len(root_ids))), root_ids)
    
    node_ids = [row[0] for row in cursor.fetchall()]
    
    # Build the nodes list
    nodes = []
    for node_id in node_ids:
        entity_info = get_entity_info(node_id, cursor)
        node_data = {
            "id": node_id,
            "type": entity_info["type_name"]
        }
        
        # Add value for primitive types
        if entity_info["type_name"] in ["String", "Number", "Boolean", "Null"]:
            if entity_info["type_name"] == "String":
                node_data["value"] = entity_info["value_str"]
                node_data["label"] = f'"{entity_info["value_str"]}"'
            elif entity_info["type_name"] == "Number":
                node_data["value"] = entity_info["value_num"]
                node_data["label"] = str(entity_info["value_num"])
            elif entity_info["type_name"] == "Boolean":
                node_data["value"] = bool(entity_info["value_bool"])
                node_data["label"] = "true" if entity_info["value_bool"] else "false"
            else:  # Null
                node_data["value"] = None
                node_data["label"] = "null"
        else:
            # For container types, use type as label
            node_data["label"] = entity_info["type_name"]
        
        nodes.append(node_data)
    
    # Build the links list with specifier information
    cursor.execute("""
    SELECT 
        r.id, 
        r.parent_entity_id, 
        r.child_entity_id, 
        r.relationship_specifier_id,
        spec.entity_type_id,
        spec_type.name AS spec_type,
        spec.value_str,
        spec.value_num,
        spec.value_bool
    FROM Relationship r
    JOIN Entity spec ON r.relationship_specifier_id = spec.id
    JOIN EntityType spec_type ON spec.entity_type_id = spec_type.id
    """)
    
    links = []
    for row in cursor.fetchall():
        rel_id, parent_id, child_id, specifier_id, spec_type_id, spec_type, value_str, value_num, value_bool = row
        
        # Skip links where either source or target is not in our filtered node list
        if parent_id not in node_ids or child_id not in node_ids:
            continue
        
        link_data = {
            "id": rel_id,
            "source": parent_id,
            "target": child_id,
            "specifier_id": specifier_id,
            "spec_type": spec_type
        }
        
        # Add specifier value based on its type
        if spec_type == "String":
            link_data["spec_value"] = value_str
            link_data["label"] = f'"{value_str}"'
        elif spec_type == "Number":
            link_data["spec_value"] = value_num
            link_data["label"] = str(value_num)
        elif spec_type == "Boolean":
            link_data["spec_value"] = bool(value_bool)
            link_data["label"] = "true" if value_bool else "false"
        elif spec_type == "Null":
            link_data["spec_value"] = None
            link_data["label"] = "null"
        else:
            # For complex specifiers (Object, Array, Version)
            link_data["specifier_complex"] = True
            link_data["label"] = f"[{spec_type}]"
        
        links.append(link_data)
    
    # Create the final output structure
    d3_data = {
        "nodes": nodes,
        "links": links
    }
    
    # Write to the output file
    with open(output_file, 'w') as f:
        json.dump(d3_data, f, indent=2)
    
    print(f"D3.js compatible JSON exported to: {output_file}")
    print(f"Exported {len(nodes)} nodes and {len(links)} links")

def main():
    parser = argparse.ArgumentParser(description='Export data from the object graph storage system.')
    parser.add_argument('output_file', help='Path to the output file')
    parser.add_argument('--db_path', default='generic_store.db', help='Path to the SQLite database file')
    parser.add_argument('--format', choices=['pickle', 'pprint', 'javascript', 'js', 'd3json'], 
                        default='d3json', help='Output format')
    
    args = parser.parse_args()
    
    # Check if the database exists
    if not os.path.exists(args.db_path):
        print(f"Error: Database file not found: {args.db_path}")
        return
    
    # Connect to the database
    conn = sqlite3.connect(args.db_path)
    cursor = conn.cursor()
    
    # Enable foreign key constraints
    cursor.execute("PRAGMA foreign_keys = ON")
    
    # Get entity type map
    entity_type_map = get_entity_type_map(cursor)
    
    try:
        if args.format == 'd3json':
            export_d3json(args.output_file, cursor, entity_type_map)
        else:
            # Find root entities
            root_ids = find_root_entities(cursor)
            if not root_ids:
                raise ValueError("No root entities found in the database")
            
            # Use the first root entity
            root_id = root_ids[0]
            
            # Rebuild the object graph
            obj = rebuild_node(root_id, cursor, entity_type_map)
            
            # Export in the requested format
            if args.format == 'pickle':
                with open(args.output_file, 'wb') as f:
                    pickle.dump(obj, f)
                print(f"Object pickled to: {args.output_file}")
            
            elif args.format == 'pprint':
                with open(args.output_file, 'w') as f:
                    f.write(pprint.pformat(obj, width=100, indent=2))
                print(f"Pretty-printed object written to: {args.output_file}")
            
            elif args.format in ['javascript', 'js']:
                with open(args.output_file, 'w') as f:
                    js_obj = js_serialize(obj)
                    f.write(f"const exportedData = {js_obj};\n")
                print(f"JavaScript object written to: {args.output_file}")
    
    except Exception as e:
        print(f"Error during export: {e}")
    
    finally:
        # Close the database connection
        conn.close()

if __name__ == "__main__":
    main()
