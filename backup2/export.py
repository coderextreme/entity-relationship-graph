# export_data.py
import sqlite3
import json
import argparse
import sys
import os
import pickle
import pprint
import warnings
from collections import OrderedDict
import traceback

DB_FILE = "generic_store.db"

rebuild_cache = {}
_db_cursor = None
_type_map_reverse = None
CYCLE_PLACEHOLDER = object()

def get_entity_type_map_reverse(cursor):
    """Fetches entity types and returns an id -> name map."""
    cursor.execute("SELECT id, name FROM EntityType")
    return {id: name for id, name in cursor.fetchall()}

def find_root_entity_id(cursor):
    """Finds a potential root entity ID."""
    cursor.execute("""
        SELECT e.id FROM Entity e
        LEFT JOIN Relationship r ON e.id = r.child_entity_id
        WHERE r.id IS NULL LIMIT 1
    """)
    result = cursor.fetchone()
    if result: return result['id']
    cursor.execute("SELECT MAX(id) as max_id FROM Entity")
    result = cursor.fetchone()
    return result['max_id'] if result and result['max_id'] is not None else None


def rebuild_node(entity_id):
    """Recursively reconstructs the Python object/value from the database."""
    global rebuild_cache, _db_cursor, _type_map_reverse

    if _db_cursor is None or _type_map_reverse is None:
         raise RuntimeError("Database cursor or reverse type map not initialized.")

    if entity_id in rebuild_cache:
        cached_value = rebuild_cache[entity_id]
        if cached_value is CYCLE_PLACEHOLDER: return "[Cycle Detected]"
        return cached_value

    _db_cursor.execute("""
        SELECT et.name, e.value_str, e.value_num, e.value_bool
        FROM Entity e JOIN EntityType et ON e.entity_type_id = et.id
        WHERE e.id = ?
    """, (entity_id,))
    result = _db_cursor.fetchone()
    if not result: raise ValueError(f"Entity with ID {entity_id} not found.")

    entity_type_name = result['name']
    value_str = result['value_str']
    value_num = result['value_num']
    value_bool = result['value_bool']

    is_container = entity_type_name in ('Object', 'Array', 'Version')
    if is_container: rebuild_cache[entity_id] = CYCLE_PLACEHOLDER

    rebuilt_val = None
    try:
        if entity_type_name == 'String':
            rebuilt_val = value_str
        elif entity_type_name == 'Number':
            rebuilt_val = int(value_num) if value_num is not None and value_num == int(value_num) else value_num
        elif entity_type_name == 'Boolean':
            rebuilt_val = bool(value_bool)
        elif entity_type_name == 'Null':
            rebuilt_val = None
        elif entity_type_name == 'Object':
            # ... (Object rebuild logic - same as previous version) ...
            data = {}; rebuild_cache[entity_id] = data
            _db_cursor.execute("SELECT r.child_entity_id, r.relationship_specifier_id FROM Relationship r WHERE r.parent_entity_id = ?", (entity_id,))
            for row in _db_cursor.fetchall():
                specifier = rebuild_node(row['relationship_specifier_id']); child = rebuild_node(row['child_entity_id'])
                try: data[specifier] = child
                except TypeError: warnings.warn(f"Unhashable specifier type ({type(specifier)}) for object {entity_id}. Converting to string.", UserWarning); data[str(specifier)] = child
            rebuilt_val = data
        elif entity_type_name == 'Array':
             # ... (Array/Tuple rebuild logic - same as previous version, attempts standard list, falls back to pairs) ...
            items = []; possible_standard = True; max_index = -1
            _db_cursor.execute("SELECT r.child_entity_id, r.relationship_specifier_id FROM Relationship r WHERE r.parent_entity_id = ?", (entity_id,))
            rows = _db_cursor.fetchall(); rebuilt_pairs = []
            for row in rows:
                specifier = rebuild_node(row['relationship_specifier_id']); child = rebuild_node(row['child_entity_id'])
                rebuilt_pairs.append({'spec': specifier, 'child': child})
                if not isinstance(specifier, int) or specifier < 0: possible_standard = False
                elif isinstance(specifier, int): max_index = max(max_index, specifier)
            if possible_standard and len(rebuilt_pairs) != (max_index + 1): possible_standard = False
            if possible_standard and len(rebuilt_pairs) > 0:
                rebuilt_pairs.sort(key=lambda p: p['spec']); rebuilt_val = [p['child'] for p in rebuilt_pairs]
            elif len(rebuilt_pairs) == 0: rebuilt_val = []
            else:
                rebuilt_val = [[p['spec'], p['child']] for p in rebuilt_pairs]
                if len(rebuilt_pairs) > 0: warnings.warn(f"Entity {entity_id} (Array/Tuple) reconstructed as list of [spec, value] pairs.", UserWarning)
            rebuild_cache[entity_id] = rebuilt_val
        elif entity_type_name == 'Version':
             # ... (Version/OrderedDict rebuild logic - same as previous version) ...
            data = OrderedDict(); rebuild_cache[entity_id] = data
            _db_cursor.execute("SELECT r.child_entity_id, r.relationship_specifier_id FROM Relationship r WHERE r.parent_entity_id = ? ORDER BY r.id ASC", (entity_id,))
            for row in _db_cursor.fetchall():
                specifier = rebuild_node(row['relationship_specifier_id']); child = rebuild_node(row['child_entity_id'])
                try: data[specifier] = child
                except TypeError: warnings.warn(f"Unhashable specifier type ({type(specifier)}) for version {entity_id}. Converting to string.", UserWarning); data[str(specifier)] = child
            rebuilt_val = data
        else:
            raise TypeError(f"Unknown entity type name '{entity_type_name}'.")

    except Exception as e:
        if entity_id in rebuild_cache and rebuild_cache[entity_id] is CYCLE_PLACEHOLDER: del rebuild_cache[entity_id]
        raise

    if not is_container or (entity_id in rebuild_cache and rebuild_cache[entity_id] is CYCLE_PLACEHOLDER):
         rebuild_cache[entity_id] = rebuilt_val

    return rebuilt_val


def serialize_to_js_literal(obj, visited_ids=None):
    """Recursively converts Python object to JS literal string."""
    if visited_ids is None: visited_ids = set()
    obj_id = id(obj)
    if obj_id in visited_ids: return '"[Circular Reference]"'
    visited_ids.add(obj_id)
    try:
        if isinstance(obj, str): return json.dumps(obj)
        elif isinstance(obj, bool): return 'true' if obj else 'false'
        elif isinstance(obj, (int, float)):
            if obj != obj or obj == float('inf') or obj == float('-inf'): return 'null' # Handle NaN/Inf
            return str(obj)
        elif obj is None: return 'null'
        elif isinstance(obj, (OrderedDict, dict)):
            items = []
            for k, v in obj.items():
                key_str = k
                if not isinstance(k, str):
                    warnings.warn(f"JS export: Converting non-string key '{k}' to string.", UserWarning); key_str = str(k)
                key_js = json.dumps(key_str)
                value_js = serialize_to_js_literal(v, visited_ids.copy()) # Pass copy for parallel branches
                items.append(f"  {key_js}: {value_js}") # Indent items
            return "{\n" + ",\n".join(items) + "\n}"
        elif isinstance(obj, list):
             is_pairs = all(isinstance(item, list) and len(item) == 2 for item in obj)
             if is_pairs and len(obj) > 0: # List of pairs format
                 item_strs = [serialize_to_js_literal(pair, visited_ids.copy()) for pair in obj]
             else: # Standard list
                 item_strs = [serialize_to_js_literal(item, visited_ids.copy()) for item in obj]
             return "[" + ", ".join(item_strs) + "]"
        elif obj == "[Cycle Detected]": return '"[Circular Reference]"'
        else:
            warnings.warn(f"JS export: Unsupported type {type(obj)}. Representing as string.", UserWarning)
            return json.dumps(str(obj))
    finally:
        visited_ids.remove(obj_id)


def export_as_d3json(cursor, output_file):
    """Queries DB and exports data in D3.js nodes/links JSON format."""
    print("Exporting in D3.js JSON format...")
    nodes = []; links = []
    # --- (D3 JSON Node/Link generation - same logic as final PHP/Node versions: Filtered Nodes, Links with spec info) ---
    connected_ids = set();
    rel_rows = cursor.execute("SELECT DISTINCT parent_entity_id as id FROM Relationship UNION SELECT DISTINCT child_entity_id as id FROM Relationship").fetchall()
    for row in rel_rows: connected_ids.add(row['id'])
    root_rows = cursor.execute("SELECT e.id FROM Entity e LEFT JOIN Relationship r ON e.id = r.child_entity_id WHERE r.id IS NULL").fetchall()
    for row in root_rows: connected_ids.add(row['id'])
    if not connected_ids:
        any_node = cursor.execute("SELECT id FROM Entity LIMIT 1").fetchone()
        if any_node: connected_ids.add(any_node['id'])

    if connected_ids:
        ids_to_fetch = list(connected_ids)
        placeholders = ','.join('?' * len(ids_to_fetch))
        node_rows = cursor.execute(f"SELECT e.id, et.name as type, e.value_str, e.value_num, e.value_bool FROM Entity e JOIN EntityType et ON e.entity_type_id = et.id WHERE e.id IN ({placeholders})", ids_to_fetch).fetchall()
        for row in node_rows:
            node_data = {"id": row['id'], "type": row['type']}; val = None
            if row['type'] == 'String': val = row['value_str']
            elif row['type'] == 'Number' and row['value_num'] is not None: num = row['value_num']; val = int(num) if num == int(num) else num
            elif row['type'] == 'Boolean': val = bool(row['value_bool']) if row['value_bool'] is not None else None
            elif row['type'] == 'Null': val = None
            if val is not None or row['type'] == 'Null': node_data['value'] = val
            if 'value' in node_data: label = str(node_data['value']); node_data['label'] = label if len(label) <= 15 else label[:12]+'...'
            elif row['type'] in ('Object','Array','Version'): node_data['label'] = row['type']
            else: node_data['label'] = f"{row['type']}:{row['id']}"
            nodes.append(node_data)

    link_rows = cursor.execute("""
        SELECT r.parent_entity_id as source, r.child_entity_id as target, r.relationship_specifier_id as spec_id,
               spet.name as spec_type_name, spe.value_str as spec_val_str, spe.value_num as spec_val_num, spe.value_bool as spec_val_bool
        FROM Relationship r JOIN Entity spe ON r.relationship_specifier_id = spe.id JOIN EntityType spet ON spe.entity_type_id = spet.id
    """).fetchall()
    for row in link_rows:
        link_data = {"source": row['source'], "target": row['target']}
        spec_type = row['spec_type_name']; spec_val = None
        if spec_type == 'String': spec_val = row['spec_val_str']; link_data['key'] = spec_val; link_data['label'] = spec_val
        elif spec_type == 'Number' and row['spec_val_num'] is not None: num = row['spec_val_num']; spec_val = int(num) if num == int(num) else num; link_data['index'] = spec_val; link_data['label'] = str(spec_val)
        elif spec_type == 'Boolean' and row['spec_val_bool'] is not None: spec_val = bool(row['spec_val_bool']); link_data['spec_value'] = spec_val; link_data['label'] = str(spec_val).lower()
        elif spec_type == 'Null': link_data['spec_value'] = None; link_data['label'] = 'null'
        else: link_data['specifier_type'] = spec_type; link_data['specifier_id'] = row['spec_id']; link_data['label'] = f'[{spec_type} Spec]'
        link_data['spec_type'] = spec_type
        links.append(link_data)

    output_data = {"nodes": nodes, "links": links}
    try:
        with open(output_file, 'w', encoding='utf-8') as f: json.dump(output_data, f, indent=2)
        print("D3 JSON export successful.")
    except Exception as e: print(f"Error writing D3 JSON file '{output_file}': {e}", file=sys.stderr); sys.exit(1)


def export_main(output_file, db_file=DB_FILE, format='pickle'):
    """Main function to handle export."""
    global rebuild_cache, _db_cursor, _type_map_reverse

    conn = None
    try:
        conn = sqlite3.connect(db_file)
        conn.row_factory = sqlite3.Row
        _db_cursor = conn.cursor()
        _type_map_reverse = get_entity_type_map_reverse(_db_cursor)

        if not _type_map_reverse: print(f"Error: EntityType table missing.", file=sys.stderr); sys.exit(1)

        if format == 'd3json':
            export_as_d3json(_db_cursor, output_file)
            return

        rebuild_cache = {}
        root_entity_id = find_root_entity_id(_db_cursor)
        reconstructed_data = None
        if root_entity_id is None: print("Warning: No root entity found. Exporting None.", file=sys.stderr)
        else:
             print(f"Found root entity ID: {root_entity_id}. Rebuilding structure...")
             try: reconstructed_data = rebuild_node(root_entity_id); print(f"Reconstruction complete.")
             except RecursionError: print("ERROR: Max recursion depth exceeded.", file=sys.stderr); sys.exit(1)
             except Exception as e: print(f"Error during rebuild: {e}", file=sys.stderr); traceback.print_exc(); sys.exit(1)

        print(f"Writing data to '{output_file}' in format '{format}'...")
        try:
            if format == 'pickle':
                with open(output_file, 'wb') as f: pickle.dump(reconstructed_data, f, protocol=pickle.HIGHEST_PROTOCOL)
            elif format == 'pprint':
                with open(output_file, 'w', encoding='utf-8') as f: f.write(pprint.pformat(reconstructed_data, indent=2, width=120) + '\n')
            elif format == 'javascript':
                 js_string = serialize_to_js_literal(reconstructed_data)
                 with open(output_file, 'w', encoding='utf-8') as f: f.write(f"const exportedData = {js_string};\n")
            else: raise ValueError(f"Internal Error: Format '{format}'.")
            print("Export successful.")
        except Exception as e: print(f"Error writing output file: {e}", file=sys.stderr); sys.exit(1)

    except sqlite3.Error as e: print(f"Database error: {e}", file=sys.stderr); sys.exit(1)
    except Exception as e: print(f"Unexpected error: {e}", file=sys.stderr); traceback.print_exc(); sys.exit(1)
    finally:
        rebuild_cache = {}; _db_cursor = None; _type_map_reverse = None
        if conn: conn.close(); print("Database connection closed.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Export data structure from SQLite DB.")
    parser.add_argument("output_file", help="Path to write the output file.")
    parser.add_argument("-f", "--format", choices=['pickle', 'pprint', 'javascript', 'js', 'd3json'], default='pickle', help="Output format.")
    parser.add_argument("-db", "--database", default=DB_FILE, help=f"Path to the SQLite DB file (default: {DB_FILE}).")
    args = parser.parse_args()
    if not os.path.exists(args.database): print(f"Error: DB file not found: {args.database}", file=sys.stderr); sys.exit(1)
    export_format = 'javascript' if args.format == 'js' else args.format
    export_main(args.output_file, args.database, export_format)
