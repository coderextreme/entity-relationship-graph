<?php
/**
 * serve_graph.php - Simple PHP server script to serve the D3.js visualization.
 * Run with: php -S localhost:8000 serve_graph.php
 */

// Database configuration
$db_path = 'generic_store.db';

// Function to get entity type map
function get_entity_type_map($pdo) {
    $stmt = $pdo->query("SELECT id, name FROM EntityType");
    $entity_types = [];
    while ($row = $stmt->fetch(PDO::FETCH_ASSOC)) {
        $entity_types[$row['id']] = $row['name'];
    }
    return $entity_types;
}

// Function to get entity info
function get_entity_info($entity_id, $pdo) {
    $stmt = $pdo->prepare("
        SELECT e.entity_type_id, et.name, e.value_str, e.value_num, e.value_bool
        FROM Entity e
        JOIN EntityType et ON e.entity_type_id = et.id
        WHERE e.id = ?
    ");
    $stmt->execute([$entity_id]);
    $row = $stmt->fetch(PDO::FETCH_ASSOC);
    
    if (!$row) {
        throw new Exception("Entity ID $entity_id not found");
    }
    
    return [
        'id' => $entity_id,
        'type_id' => $row['entity_type_id'],
        'type_name' => $row['name'],
        'value_str' => $row['value_str'],
        'value_num' => $row['value_num'],
        'value_bool' => $row['value_bool']
    ];
}

// Function to find root entities
function find_root_entities($pdo) {
    $stmt = $pdo->query("
        SELECT id FROM Entity WHERE id NOT IN (
            SELECT DISTINCT child_entity_id FROM Relationship
        )
    ");
    $roots = [];
    while ($row = $stmt->fetch(PDO::FETCH_ASSOC)) {
        $roots[] = $row['id'];
    }
    return $roots;
}

// Function to generate D3.js compatible JSON
function generate_d3_json($pdo) {
    // Find potential root entities
    $root_ids = find_root_entities($pdo);
    if (empty($root_ids)) {
        throw new Exception("No root entities found in the database");
    }
    
    // Prepare placeholder for IN clause
    $placeholders = implode(',', array_fill(0, count($root_ids), '?'));
    
    // Get all entities that are either parents or children
    $stmt = $pdo->prepare("
        SELECT DISTINCT parent_entity_id AS entity_id FROM Relationship
        UNION
        SELECT DISTINCT child_entity_id AS entity_id FROM Relationship
        UNION
        SELECT id AS entity_id FROM Entity WHERE id IN ($placeholders)
    ");
    
    $stmt->execute($root_ids);
    $node_ids = [];
    while ($row = $stmt->fetch(PDO::FETCH_ASSOC)) {
        $node_ids[] = $row['entity_id'];
    }
    
    // Build the nodes list
    $nodes = [];
    foreach ($node_ids as $node_id) {
        $entity_info = get_entity_info($node_id, $pdo);
        $node_data = [
            "id" => $node_id,
            "type" => $entity_info["type_name"]
        ];
        
        // Add value for primitive types
        if (in_array($entity_info["type_name"], ["String", "Number", "Boolean", "Null"])) {
            if ($entity_info["type_name"] == "String") {
                $node_data["value"] = $entity_info["value_str"];
                $node_data["label"] = "\"" . $entity_info["value_str"] . "\"";
            } elseif ($entity_info["type_name"] == "Number") {
                $node_data["value"] = $entity_info["value_num"];
                $node_data["label"] = (string)$entity_info["value_num"];
            } elseif ($entity_info["type_name"] == "Boolean") {
                $node_data["value"] = (bool)$entity_info["value_bool"];
                $node_data["label"] = $entity_info["value_bool"] ? "true" : "false";
            } else { // Null
                $node_data["value"] = null;
                $node_data["label"] = "null";
            }
        } else {
            // For container types, use type as label
            $node_data["label"] = $entity_info["type_name"];
        }
        
        $nodes[] = $node_data;
    }
    
    // Build the links list with specifier information
    $stmt = $pdo->query("
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
    ");
    
    $links = [];
    while ($row = $stmt->fetch(PDO::FETCH_ASSOC)) {
        $rel_id = $row['id'];
        $parent_id = $row['parent_entity_id'];
        $child_id = $row['child_entity_id'];
        $specifier_id = $row['relationship_specifier_id'];
        $spec_type = $row['spec_type'];
        $value_str = $row['value_str'];
        $value_num = $row['value_num'];
        $value_bool = $row['value_bool'];
        
        // Skip links where either source or target is not in our filtered node list
        if (!in_array($parent_id, $node_ids) || !in_array($child_id, $node_ids)) {
            continue;
        }
        
        $link_data = [
            "id" => $rel_id,
            "source" => $parent_id,
            "target" => $child_id,
            "specifier_id" => $specifier_id,
            "spec_type" => $spec_type
        ];
        
        // Add specifier value based on its type
        if ($spec_type == "String") {
            $link_data["spec_value"] = $value_str;
            $link_data["label"] = "\"" . $value_str . "\"";
        } elseif ($spec_type == "Number") {
            $link_data["spec_value"] = $value_num;
            $link_data["label"] = (string)$value_num;
        } elseif ($spec_type == "Boolean") {
            $link_data["spec_value"] = (bool)$value_bool;
            $link_data["label"] = $value_bool ? "true" : "false";
        } elseif ($spec_type == "Null") {
            $link_data["spec_value"] = null;
            $link_data["label"] = "null";
        } else {
            // For complex specifiers (Object, Array, Version)
            $link_data["specifier_complex"] = true;
            $link_data["label"] = "[$spec_type]";
        }
        
        $links[] = $link_data;
    }
    
    // Create the final output structure
    return [
        "nodes" => $nodes,
        "links" => $links
    ];
}

// Main server logic
$request_uri = $_SERVER['REQUEST_URI'];

// Serve different content based on the request URI
if ($request_uri == '/' || $request_uri == '/index.html') {
    // Serve the HTML page
    include('index.html');
    exit;
} elseif ($request_uri == '/data') {
    // Serve the graph data as JSON
    header('Content-Type: application/json');
    
    try {
        // Check if database file exists
        if (!file_exists($db_path)) {
            throw new Exception("Database file not found: $db_path");
        }
        
        // Connect to the database
        $pdo = new PDO("sqlite:$db_path");
        $pdo->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);
        
        // Enable foreign key constraints
        $pdo->exec("PRAGMA foreign_keys = ON");
        
        // Generate the D3.js compatible JSON
        $d3_data = generate_d3_json($pdo);
        
        // Output the JSON
        echo json_encode($d3_data, JSON_PRETTY_PRINT);
    } catch (Exception $e) {
        // Return error as JSON
        http_response_code(500);
        echo json_encode(['error' => $e->getMessage()]);
    }
    
    exit;
} else {
    // Handle 404 for other URIs
    http_response_code(404);
    echo "404 Not Found";
    exit;
}
?>
