<?php

// --- Configuration --- (Same)
$db_file = __DIR__ . '/generic_store.db';
$html_file = __DIR__ . '/index.html';

// --- Simple Router --- (Same)
$request_uri = parse_url($_SERVER['REQUEST_URI'], PHP_URL_PATH);
// ... (routing logic same as before) ...

// --- Function to Serve HTML --- (Same)
function serve_html($file_path) { /* ... */ }

// --- MODIFIED Function to Serve D3 JSON Data ---
function serve_d3_json($db_path) {
    if (!file_exists($db_path)) { /* ... error handling ... */ return; }

    try {
        $pdo = new PDO('sqlite:' . $db_path);
        $pdo->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);
        $pdo->setAttribute(PDO::ATTR_DEFAULT_FETCH_MODE, PDO::FETCH_ASSOC);

        // --- Step 1: Identify Connected Node IDs (Parents, Children, Roots - Excludes pure specifiers) ---
        $connected_ids = [];
        // ... (Exactly the same logic as the previous "fix" version to get connected_ids) ...
        $stmt_rel_ids = $pdo->query("SELECT DISTINCT parent_entity_id as id FROM Relationship UNION SELECT DISTINCT child_entity_id as id FROM Relationship");
        while ($row = $stmt_rel_ids->fetch()) { $connected_ids[(int)$row['id']] = true; }
        $stmt_root_ids = $pdo->query("SELECT e.id FROM Entity e LEFT JOIN Relationship r ON e.id = r.child_entity_id WHERE r.id IS NULL");
         while ($row = $stmt_root_ids->fetch()) { $connected_ids[(int)$row['id']] = true; }
        if (empty($connected_ids)) { /* ... handle empty/single node ... */ }


        // --- Step 2: Fetch ONLY Connected Nodes ---
        $nodes = [];
        if (!empty($connected_ids)) {
             // ... (Exactly the same logic as the previous "fix" version to fetch nodes based on connected_ids) ...
            $placeholders = implode(',', array_fill(0, count($connected_ids), '?'));
            $ids_to_fetch = array_keys($connected_ids);
            $sql_nodes = "SELECT e.id, et.name as type, e.value_str, e.value_num, e.value_bool FROM Entity e JOIN EntityType et ON e.entity_type_id = et.id WHERE e.id IN ($placeholders)";
            $stmt_nodes = $pdo->prepare($sql_nodes);
            $stmt_nodes->execute($ids_to_fetch);
             while ($row = $stmt_nodes->fetch()) {
                 // ... (Node processing logic - same assigns id, type, value, label) ...
                  $node_data = [ "id" => (int)$row['id'], "type" => $row['type'] ];
                  $val = null;
                  if ($row['type'] === 'String' && $row['value_str'] !== null) { $val = $row['value_str']; }
                  elseif ($row['type'] === 'Number' && $row['value_num'] !== null) { $num = (float)$row['value_num']; $val = (floor($num) == $num) ? (int)$num : $num; }
                  elseif ($row['type'] === 'Boolean' && $row['value_bool'] !== null) { $val = (bool)$row['value_bool']; }
                  elseif ($row['type'] === 'Null') { $val = null; }
                  if ($val !== null || $row['type'] === 'Null') { $node_data['value'] = $val; }
                  if (!isset($node_data['value']) && in_array($row['type'], ['Object', 'Array', 'Version'])) { $node_data['label'] = $row['type']; }
                  elseif (isset($node_data['value'])) { $label = is_string($val) ? $val : json_encode($val); $node_data['label'] = strlen($label) > 15 ? substr($label, 0, 12) . '...' : $label;}
                  else { $node_data['label'] = $row['type'] . ':' . $row['id']; }
                 $nodes[] = $node_data;
             }
        } else { $nodes = []; }


        // --- Step 3: Fetch Relationships and Add Specifier Info to the SINGLE Link ---
        $links = [];
        $stmt_links = $pdo->query("
            SELECT
                r.parent_entity_id as source,
                r.child_entity_id as target,
                r.relationship_specifier_id as spec_id, -- Specifier's own ID
                spet.name as spec_type_name,           -- Specifier's Type
                spe.value_str as spec_val_str,         -- Specifier's Value (if primitive)
                spe.value_num as spec_val_num,
                spe.value_bool as spec_val_bool
            FROM Relationship r
            JOIN Entity spe ON r.relationship_specifier_id = spe.id
            JOIN EntityType spet ON spe.entity_type_id = spet.id
        ");

        while ($row = $stmt_links->fetch()) {
            // Create ONE link from parent to child
            $link_data = [
                "source" => (int)$row['source'],
                "target" => (int)$row['target']
                // Add specifier details directly to this link object
                // "specifier_id" => (int)$row['spec_id'] // Could include if needed
            ];

            $spec_type = $row['spec_type_name'];

            // Extract specifier value or type info and add to link
            $spec_val = null;
            if ($spec_type === 'String' && $row['spec_val_str'] !== null) {
                $spec_val = $row['spec_val_str'];
                $link_data['key'] = $spec_val; // Use 'key' property for string specifiers
                $link_data['label'] = $spec_val; // Also use as default label
            } elseif ($spec_type === 'Number' && $row['spec_val_num'] !== null) {
                $num = (float)$row['spec_val_num'];
                $spec_val = (floor($num) == $num) ? (int)$num : $num;
                $link_data['index'] = $spec_val; // Use 'index' property for number specifiers
                 $link_data['label'] = (string)$spec_val; // Default label is index as string
            } elseif ($spec_type === 'Boolean' && $row['spec_val_bool'] !== null) {
                $spec_val = (bool)$row['spec_val_bool'];
                $link_data['spec_value'] = $spec_val; // Use generic property
                 $link_data['label'] = json_encode($spec_val); // Default label
            } elseif ($spec_type === 'Null') {
                 $link_data['spec_value'] = null;
                 $link_data['label'] = 'null'; // Default label
            } else {
                // Specifier was a container (Object/Array/Version)
                $link_data['specifier_type'] = $spec_type;
                $link_data['specifier_id'] = (int)$row['spec_id']; // Include ID for complex specifiers
                $link_data['label'] = '['.$spec_type.' Spec]'; // Default label
            }

             $link_data['spec_type'] = $spec_type; // Always include specifier type


            $links[] = $link_data;
        }

        // --- Combine and Output ---
        $output_data = ["nodes" => $nodes, "links" => $links];

        header('Content-Type: application/json; charset=utf-8');
        echo json_encode($output_data, JSON_PRETTY_PRINT);

    } catch (PDOException $e) { /* ... error handling ... */ }
      catch (Exception $e) { /* ... error handling ... */ }
}
?>
