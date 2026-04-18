<?php
// serve_graph.php (Refined - Filters Nodes, Puts Spec Info on Links)

// --- Configuration ---
$db_file = __DIR__ . '/generic_store.db'; // Assumes DB is in the same directory
$html_file = __DIR__ . '/index.html';    // Assumes HTML is in the same directory

// --- Simple Router ---
$request_uri = parse_url($_SERVER['REQUEST_URI'], PHP_URL_PATH);

if ($request_uri === '/data') {
    serve_d3_json($db_file);
} elseif ($request_uri === '/' || $request_uri === '/index.html') {
    serve_html($html_file);
} else {
    http_response_code(404);
    echo "404 Not Found";
}

exit; // Important to prevent any further output

// --- Function to Serve HTML ---
function serve_html($file_path) {
    if (file_exists($file_path)) {
        header('Content-Type: text/html; charset=utf-8');
        readfile($file_path);
    } else {
        http_response_code(500);
        error_log("Error: HTML file not found at " . $file_path);
        echo "Error: HTML file not found.";
    }
}

// --- Function to Serve D3 JSON Data ---
function serve_d3_json($db_path) {
    if (!file_exists($db_path)) {
        http_response_code(500);
        header('Content-Type: application/json');
        error_log("Error: Database file not found at " . $db_path);
        echo json_encode(['error' => 'Database file not found.']);
        return;
    }

    try {
        // Connect to SQLite database using PDO
        $pdo = new PDO('sqlite:' . $db_path);
        $pdo->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION); // Enable error reporting
        $pdo->setAttribute(PDO::ATTR_DEFAULT_FETCH_MODE, PDO::FETCH_ASSOC); // Fetch rows as associative arrays

        // --- Step 1: Identify Connected Node IDs (Strictly Parents, Children, and Roots) ---
        $connected_ids = []; // Use keys for efficient lookup

        // Get all parent and child IDs from relationships
        $stmt_rel_ids = $pdo->query("
            SELECT DISTINCT parent_entity_id as id FROM Relationship
            UNION
            SELECT DISTINCT child_entity_id as id FROM Relationship
        ");
        while ($row = $stmt_rel_ids->fetch()) {
            $connected_ids[(int)$row['id']] = true;
        }

        // Get root node IDs (nodes not appearing as children in ANY relationship)
        $stmt_root_ids = $pdo->query("
            SELECT e.id
            FROM Entity e
            LEFT JOIN Relationship r ON e.id = r.child_entity_id
            WHERE r.id IS NULL
        ");
         while ($row = $stmt_root_ids->fetch()) {
            $connected_ids[(int)$row['id']] = true; // Add roots
        }

        // Handle edge case: DB has entities but no relationships (all are roots)
        if (empty($connected_ids)) {
             $stmt_all_nodes = $pdo->query("SELECT id FROM Entity");
              while ($row = $stmt_all_nodes->fetch()) {
                 $connected_ids[(int)$row['id']] = true;
             }
        }


        // --- Step 2: Fetch ONLY Connected Nodes ---
        $nodes = [];
        if (!empty($connected_ids)) {
            $ids_to_fetch = array_keys($connected_ids);
            // Ensure we handle the case of a single ID correctly for IN clause
             if (count($ids_to_fetch) === 1) {
                 $sql_nodes = "SELECT e.id, et.name as type, e.value_str, e.value_num, e.value_bool FROM Entity e JOIN EntityType et ON e.entity_type_id = et.id WHERE e.id = ?";
                 $stmt_nodes = $pdo->prepare($sql_nodes);
                 $stmt_nodes->execute($ids_to_fetch); // Execute with single param
             } elseif (count($ids_to_fetch) > 1) {
                 $placeholders = implode(',', array_fill(0, count($ids_to_fetch), '?'));
                 $sql_nodes = "SELECT e.id, et.name as type, e.value_str, e.value_num, e.value_bool FROM Entity e JOIN EntityType et ON e.entity_type_id = et.id WHERE e.id IN ($placeholders)";
                 $stmt_nodes = $pdo->prepare($sql_nodes);
                 $stmt_nodes->execute($ids_to_fetch); // Execute with array
             } else {
                 // Should not happen if edge case above works, but safety first
                 $stmt_nodes = null;
             }


            if ($stmt_nodes) {
                 while ($row = $stmt_nodes->fetch()) {
                     $node_data = [ "id" => (int)$row['id'], "type" => $row['type'] ];
                     $val = null;
                     // Determine actual value
                     if ($row['type'] === 'String' && $row['value_str'] !== null) { $val = $row['value_str']; }
                     elseif ($row['type'] === 'Number' && $row['value_num'] !== null) { $num = (float)$row['value_num']; $val = (floor($num) == $num) ? (int)$num : $num; }
                     elseif ($row['type'] === 'Boolean' && $row['value_bool'] !== null) { $val = (bool)$row['value_bool']; }
                     elseif ($row['type'] === 'Null') { $val = null; } // Explicit null
                     // Store value if present
                     if ($val !== null || $row['type'] === 'Null') { $node_data['value'] = $val; }

                     // Determine label for visualization
                     if (isset($node_data['value'])) {
                         $label_val = is_string($val) ? $val : json_encode($val);
                         $node_data['label'] = strlen($label_val) > 15 ? substr($label_val, 0, 12) . '...' : $label_val;
                     } elseif (in_array($row['type'], ['Object', 'Array', 'Version'])) {
                         $node_data['label'] = $row['type']; // Label containers by type
                     } else {
                         $node_data['label'] = $row['type'] . ':' . $row['id']; // Fallback label
                     }
                     $nodes[] = $node_data;
                 }
            }
        }


        // --- Step 3: Fetch Relationships and Add Specifier Info to the SINGLE Link ---
        $links = [];
        // Fetch all relationships, including specifier details
        $stmt_links = $pdo->query("
            SELECT
                r.parent_entity_id as source,
                r.child_entity_id as target,
                r.relationship_specifier_id as spec_id,
                spet.name as spec_type_name,
                spe.value_str as spec_val_str,
                spe.value_num as spec_val_num,
                spe.value_bool as spec_val_bool
            FROM Relationship r
            JOIN Entity spe ON r.relationship_specifier_id = spe.id
            JOIN EntityType spet ON spe.entity_type_id = spet.id
        ");

        while ($row = $stmt_links->fetch()) {
            $source_id = (int)$row['source'];
            $target_id = (int)$row['target'];

            // CRITICAL: Only include links where BOTH source and target are in our filtered node list
            if (!isset($connected_ids[$source_id]) || !isset($connected_ids[$target_id])) {
                continue; // Skip link if source or target node was filtered out
            }

            // Create ONE link object from parent to child
            $link_data = [
                "source" => $source_id,
                "target" => $target_id,
                "specifier_id" => (int)$row['spec_id'] // Keep spec ID for potential debugging/info
            ];

            $spec_type = $row['spec_type_name'];
            $link_data['spec_type'] = $spec_type; // Always include specifier type

            // Extract specifier value and add appropriate property/label
            $spec_val = null;
            $link_label = '['.$spec_type.' Spec]'; // Default label

            if ($spec_type === 'String' && $row['spec_val_str'] !== null) {
                $spec_val = $row['spec_val_str'];
                $link_data['key'] = $spec_val;
                $link_label = $spec_val;
            } elseif ($spec_type === 'Number' && $row['spec_val_num'] !== null) {
                $num = (float)$row['spec_val_num'];
                $spec_val = (floor($num) == $num) ? (int)$num : $num;
                $link_data['index'] = $spec_val;
                $link_label = (string)$spec_val;
            } elseif ($spec_type === 'Boolean' && $row['spec_val_bool'] !== null) {
                $spec_val = (bool)$row['spec_val_bool'];
                $link_data['spec_value'] = $spec_val;
                $link_label = json_encode($spec_val);
            } elseif ($spec_type === 'Null') {
                 $link_data['spec_value'] = null;
                 $link_label = 'null';
            } else {
                 // Specifier is complex type (Object/Array/Version)
                 $link_data['specifier_complex'] = true; // Flag it
                 // Label already defaults to '[Type Spec]'
            }
            $link_data['label'] = $link_label; // Set the determined label

            $links[] = $link_data;
        }

        // --- Combine and Output ---
        $output_data = ["nodes" => $nodes, "links" => $links];
        header('Content-Type: application/json; charset=utf-8');
        // Added flags for better JSON output
        echo json_encode($output_data, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE);

    } catch (PDOException $e) {
        http_response_code(500); header('Content-Type: application/json'); error_log("Database Error: " . $e->getMessage()); echo json_encode(['error' => 'Database query failed.']);
    } catch (Exception $e) {
        http_response_code(500); header('Content-Type: application/json'); error_log("General Error: " . $e->getMessage()); echo json_encode(['error' => 'An unexpected server error occurred.']);
    }
}
?>
