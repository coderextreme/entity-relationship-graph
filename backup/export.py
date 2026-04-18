import sqlite3
import json
import argparse
import sys
import os
import pickle
import pprint
import warnings
from collections import OrderedDict

DB_FILE = "generic_store.db"

# Cache for already rebuilt nodes during export to handle shared structures/cycles
# Key: entity_id, Value: rebuilt Python object/value or placeholder
rebuild_cache = {}
# Global DB cursor and type map for helper functions
_db_cursor = None
_type_map_reverse = None

# Placeholder object for cycle detection during rebuild
CYCLE_PLACEHOLDER = object()

def get_entity_type_map_reverse(cursor):
    """Fetches entity types and returns an id -> name map."""
    cursor.execute("SELECT id, name FROM EntityType")
    return {id: name for id, name in cursor.fetchall()}

def find_root_entity_id(cursor):
    """
    Finds a potential root entity ID (one not appearing as child).
    May not be unique if multiple structures were imported.
    """
    cursor.execute("""
        SELECT e.id FROM Entity e
        LEFT JOIN Relationship r ON e.id = r.child_entity_id
        WHERE r.id IS NULL LIMIT 1
    """)
    result = cursor.fetchone()
    if result: return result['id'] # Assuming row_factory is set
    # Fallback: find max ID if no clear root (e.g., single node, cycles only)
    cursor.execute("SELECT MAX(id) as max_id FROM Entity")
    result = cursor.fetchone()
    return result['max_id'] if result and result['max_id'] is not None else None


def rebuild_node(entity_id):
    """
    Recursively reconstructs the Python object/value from the database,
    handling complex specifiers and attempting standard array reconstruction.
    Uses global cursor, type map, and cache.
    """
    global rebuild_cache, _db_cursor, _type_map_reverse

    if _db_cursor is None or _type_map_reverse is None:
         raise RuntimeError("Database cursor or reverse type map not initialized.")

    # --- Cache Check ---
    if entity_id in rebuild_cache:
        cached_value = rebuild_cache[entity_id]
        # If it's the placeholder, we've hit a cycle during this rebuild path
        if cached_value is CYCLE_PLACEHOLDER:
            # Indicate cycle - caller (like JS exporter) needs to handle this
            # Returning None or a specific marker object might be options
            return "[Cycle Detected]" # Or raise specific error
        return cached_value # Return previously built object (sharing)

    # --- Fetch Entity Data ---
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

    # --- Placeholder for Cycles ---
    is_container = entity_type_name in ('Object', 'Array', 'Version')
    if is_container:
         rebuild_cache[entity_id] = CYCLE_PLACEHOLDER

    # --- Rebuild Value ---
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
            data = {}
            rebuild_cache[entity_id] = data # Replace placeholder with actual dict
            _db_cursor.execute("""
                SELECT r.child_entity_id, r.relationship_specifier_id
                FROM Relationship r WHERE r.parent_entity_id = ?
            """, (entity_id,))
            for row in _db_cursor.fetchall():
                specifier = rebuild_node(row['relationship_specifier_id'])
                child = rebuild_node(row['child_entity_id'])
                try:
                    data[specifier] = child
                except TypeError:
                    warnings.warn(f"Unhashable specifier type ({type(specifier)}) encountered for object entity {entity_id}. Converting specifier to string for key.", UserWarning)
                    data[str(specifier)] = child
            rebuilt_val = data

        elif entity_type_name == 'Array': # Handles Array/Tuple
            # Attempt standard list reconstruction
            items = []
            possible_standard = True
            max_index = -1
            _db_cursor.execute("""
                SELECT r.child_entity_id, r.relationship_specifier_id
                FROM Relationship r WHERE r.parent_entity_id = ?
            """, (entity_id,))
            rows = _db_cursor.fetchall()

            rebuilt_pairs = []
            for row in rows:
                specifier = rebuild_node(row['relationship_specifier_id'])
                child = rebuild_node(row['child_entity_id'])
                rebuilt_pairs.append({'spec': specifier, 'child': child})
                if not isinstance(specifier, int) or specifier < 0:
                    possible_standard = False
                elif isinstance(specifier, int):
                    max_index = max(max_index, specifier)

            if possible_standard and len(rebuilt_pairs) != (max_index + 1):
                possible_standard = False # Gap or duplicate index

            if possible_standard and len(rebuilt_pairs) > 0:
                rebuilt_pairs.sort(key=lambda p: p['spec'])
                standard_list = [p['child'] for p in rebuilt_pairs]
                rebuilt_val = standard_list
            elif len(rebuilt_pairs) == 0: # Empty array
                 rebuilt_val = []
            else:
                # Fallback to list of pairs
                non_standard_list = [[p['spec'], p['child']] for p in rebuilt_pairs]
                rebuilt_val = non_standard_list
                if len(rebuilt_pairs) > 0: # Avoid warning for genuinely empty arrays
                     warnings.warn(f"Entity {entity_id} (Array/Tuple) reconstructed as list of [specifier, value] pairs.", UserWarning)

            rebuild_cache[entity_id] = rebuilt_val # Replace placeholder

        elif entity_type_name == 'Version': # OrderedDict
            data = OrderedDict()
            rebuild_cache[entity_id] = data # Replace placeholder
            # Order by relationship ID to preserve insertion order
            _db_cursor.execute("""
                SELECT r.child_entity_id, r.relationship_specifier_id
                FROM Relationship r WHERE r.parent_entity_id = ? ORDER BY r.id ASC
            """, (entity_id,))
            for row in _db_cursor.fetchall():
                 specifier = rebuild_node(row['relationship_specifier_id'])
                 child = rebuild_node(row['child_entity_id'])
                 try:
                    data[specifier] = child
                 except TypeError:
                    warnings.warn(f"Unhashable specifier type ({type(specifier)}) encountered for version entity {entity_id}. Converting specifier to string for key.", UserWarning)
                    data[str(specifier)] = child
            rebuilt_val = data
        else:
            raise TypeError(f"Unknown entity type name '{entity_type_name}' encountered during export for entity {entity_id}.")

    except Exception as e:
        # If error during rebuild, remove placeholder to allow retry if needed elsewhere
        if entity_id in rebuild_cache and rebuild_cache[entity_id] is CYCLE_PLACEHOLDER:
            del rebuild_cache[entity_id]
        raise # Re-raise the exception

    # --- Final Cache Update ---
    # Only store final value if it wasn't a placeholder or already cached
    if not is_container or (entity_id in rebuild_cache and rebuild_cache[entity_id] is CYCLE_PLACEHOLDER):
         rebuild_cache[entity_id] = rebuilt_val

    return rebuilt_val

# --- JavaScript Export Helper ---
def serialize_to_js_literal(obj, visited_ids=None):
    """Recursively converts Python object to JS literal string."""
    if visited_ids is None:
        visited_ids = set()

    obj_id = id(obj)
    if obj_id in visited_ids:
        # Handle cycle - return placeholder string
        return '"[Circular Reference]"'
    # Add current object id to visited set for cycle detection *within this call stack*
    visited_ids.add(obj_id)

    try:
        if isinstance(obj, (str,)):
            # Basic escaping for JS strings
            return json.dumps(obj) # json.dumps handles escaping well
        elif isinstance(obj, bool):
            return 'true' if obj else 'false'
        elif isinstance(obj, (int, float)):
            # Check for NaN/Infinity which are not standard JSON/JS literal numbers
            if obj != obj or obj == float('inf') or obj == float('-inf'):
                return 'null' # Or '"NaN"', '"Infinity"', etc. depending on desired JS behavior
            return str(obj)
        elif obj is None:
            return 'null'
        elif isinstance(obj, (OrderedDict, dict)):
            items = []
            for k, v in obj.items():
                key_str = k
                if not isinstance(k, str):
                    warnings.warn(f"JavaScript export: Converting non-string key '{k}' (type {type(k)}) to string.", UserWarning)
                    key_str = str(k)
                # Key needs to be represented as a JS string literal or valid identifier
                # Using json.dumps ensures proper quoting/escaping for the key string
                key_js = json.dumps(key_str)
                value_js = serialize_to_js_literal(v, visited_ids)
                items.append(f"{key_js}: {value_js}")
            return "{\n" + ",\n".join(items) + "\n}" # Basic pretty printing
        elif isinstance(obj, list):
             # Check if it's potentially a list of [spec, value] pairs
             is_pairs = all(isinstance(item, list) and len(item) == 2 for item in obj)
             if is_pairs and len(obj) > 0: # Represent as array of arrays
                 item_strs = [serialize_to_js_literal(pair, visited_ids) for pair in obj]
             else: # Assume standard array
                 item_strs = [serialize_to_js_literal(item, visited_ids) for item in obj]
             return "[" + ", ".join(item_strs) + "]"
        elif obj == "[Cycle Detected]": # Handle cycle marker from rebuild_node
             return '"[Circular Reference]"'
        else:
            warnings.warn(f"JavaScript export: Unsupported type {type(obj)}. Representing as string.", UserWarning)
            return json.dumps(str(obj)) # Fallback: convert to string
    finally:
        # Remove current object id from visited set as we return up the stack
        visited_ids.remove(obj_id)


# --- D3 JSON Export Helper ---
def export_as_d3json(cursor, output_file):
    """Queries DB and exports data in D3.js nodes/links JSON format."""
    print("Exporting in D3.js JSON format...")
    nodes = []
    links = []
    entity_values = {} # Cache primitive values for nodes

    # Get all entities (nodes)
    cursor.execute("""
        SELECT e.id, et.name as type, e.value_str, e.value_num, e.value_bool
        FROM Entity e JOIN EntityType et ON e.entity_type_id = et.id
    """)
    for row in cursor.fetchall():
        node_data = {"id": row['id'], "type": row['type']}
        # Store primitive value if present
        val = None
        if row['type'] == 'String': val = row['value_str']
        elif row['type'] == 'Number': val = int(row['value_num']) if row['value_num'] is not None and row['value_num'] == int(row['value_num']) else row['value_num']
        elif row['type'] == 'Boolean': val = bool(row['value_bool'])
        # No value needed for Null, Object, Array, Version in the node itself for D3
        if val is not None:
            node_data['value'] = val
            entity_values[row['id']] = val # Cache for links
        nodes.append(node_data)

    # Get all relationships (links) and specifier info
    cursor.execute("""
        SELECT
            r.parent_entity_id as source,
            r.child_entity_id as target,
            r.relationship_specifier_id as spec_id,
            spe.entity_type_id as spec_type_id,
            spet.name as spec_type_name,
            spe.value_str as spec_val_str,
            spe.value_num as spec_val_num,
            spe.value_bool as spec_val_bool
        FROM Relationship r
        JOIN Entity spe ON r.relationship_specifier_id = spe.id
        JOIN EntityType spet ON spe.entity_type_id = spet.id
    """)
    spec_type_map_reverse = get_entity_type_map_reverse(cursor) # Get ID -> Name mapping
    for row in cursor.fetchall():
        link_data = {"source": row['source'], "target": row['target']}
        spec_type = row['spec_type_name']
        spec_id = row['spec_id']

        # Add specifier information to the link
        spec_val = None
        if spec_type == 'String': spec_val = row['spec_val_str']
        elif spec_type == 'Number': spec_val = int(row['spec_val_num']) if row['spec_val_num'] is not None and row['spec_val_num'] == int(row['spec_val_num']) else row['spec_val_num']
        elif spec_type == 'Boolean': spec_val = bool(row['spec_val_bool'])
        # Null type has no value

        if spec_val is not None or spec_type == 'Null':
             # Use a generic 'label' or type-specific keys
             if spec_type == 'String': link_data['label'] = spec_val
             elif spec_type == 'Number': link_data['index'] = spec_val
             else: link_data['spec_value'] = spec_val # Boolean, Null
        else:
            # Specifier is a container
            link_data['specifier_id'] = spec_id
            link_data['specifier_type'] = spec_type

        links.append(link_data)

    # Combine and dump as JSON
    output_data = {"nodes": nodes, "links": links}
    try:
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(output_data, f, indent=2) # Use indent for readability
        print("D3 JSON export successful.")
    except IOError as e:
        print(f"Error writing D3 JSON file '{output_file}': {e}", file=sys.stderr)
        sys.exit(1)
    except TypeError as e:
        print(f"Error serializing data for D3 JSON (should not happen): {e}", file=sys.stderr)
        sys.exit(1)


def export_main(output_file, db_file=DB_FILE, format='pickle'):
    """Main function to handle export in various formats."""
    global rebuild_cache, _db_cursor, _type_map_reverse

    conn = None
    try:
        conn = sqlite3.connect(db_file)
        conn.row_factory = sqlite3.Row # Access columns by name
        _db_cursor = conn.cursor()
        _type_map_reverse = get_entity_type_map_reverse(_db_cursor) # Needed for rebuild

        if not _type_map_reverse:
            print(f"Error: EntityType table is empty/missing in '{db_file}'.", file=sys.stderr)
            sys.exit(1)

        # --- D3 JSON Export (Special Case) ---
        if format == 'd3json':
            export_as_d3json(_db_cursor, output_file)
            return # Done

        # --- Rebuild-based Exports ---
        rebuild_cache = {} # Reset rebuild cache
        root_entity_id = find_root_entity_id(_db_cursor)

        if root_entity_id is None:
            print("Warning: Could not find a root entity. Database might be empty or only contain cycles/fragments.", file=sys.stderr)
            reconstructed_data = None
        else:
             print(f"Found root entity ID: {root_entity_id}. Rebuilding structure...")
             try:
                 reconstructed_data = rebuild_node(root_entity_id)
                 print(f"Reconstruction complete.")
             except RecursionError:
                  print("ERROR: Maximum recursion depth exceeded during rebuild. Cycles likely present and not handled correctly by cache/detection.", file=sys.stderr)
                  sys.exit(1)
             except Exception as e:
                  print(f"Error during structure rebuild: {e}", file=sys.stderr)
                  import traceback
                  traceback.print_exc()
                  sys.exit(1)

        print(f"Writing data to '{output_file}' in format '{format}'...")

        # --- Perform Export Based on Format ---
        try:
            if format == 'pickle':
                with open(output_file, 'wb') as f:
                    pickle.dump(reconstructed_data, f, protocol=pickle.HIGHEST_PROTOCOL)
            elif format == 'pprint':
                with open(output_file, 'w', encoding='utf-8') as f:
                    pretty_string = pprint.pformat(reconstructed_data, indent=2, width=120)
                    f.write(pretty_string)
                    f.write('\n')
            elif format == 'javascript' or format == 'js':
                 js_string = serialize_to_js_literal(reconstructed_data)
                 with open(output_file, 'w', encoding='utf-8') as f:
                     f.write(f"const exportedData = {js_string};\n") # Assign to variable
            else:
                 # Should be caught by argparse choices, but defense in depth
                 raise ValueError(f"Internal Error: Unsupported export format '{format}'.")

            print("Export successful.")

        except (IOError, pickle.PicklingError, ValueError, TypeError) as e:
            print(f"Error writing output file '{output_file}' (format: {format}): {e}", file=sys.stderr)
            sys.exit(1)

    except sqlite3.Error as e:
        print(f"Database error during export: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"An unexpected error occurred during export: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        # Clear global state
        rebuild_cache = {}
        _db_cursor = None
        _type_map_reverse = None
        if conn:
            conn.close()
            print("Database connection closed.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Export data structure from SQLite DB.")
    parser.add_argument("output_file", help="Path to write the output file.")
    parser.add_argument("-f", "--format", choices=['pickle', 'pprint', 'javascript', 'js', 'd3json'], default='pickle', help="Output format (default: pickle).")
    parser.add_argument("-db", "--database", default=DB_FILE, help=f"Path to the SQLite database file (default: {DB_FILE}).")
    args = parser.parse_args()

    if not os.path.exists(args.database):
        print(f"Error: Database file not found: {args.database}", file=sys.stderr)
        sys.exit(1)

    # Normalize format alias
    export_format = 'javascript' if args.format == 'js' else args.format

    export_main(args.output_file, args.database, export_format)
