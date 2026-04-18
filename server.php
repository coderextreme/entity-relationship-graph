<?php
// Set content type to JSON for API responses
header('Content-Type: application/json');

// Database configuration
$databasePath = './EntityRelationship.sqlite3';

// Initialize PDO SQLite connection
try {
    $db = new PDO("sqlite:$databasePath");
    $db->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);
    error_log('Connected to the EntityRelationship database');
} catch (PDOException $e) {
    error_log('Error opening database: ' . $e->getMessage());
    http_response_code(500);
    echo json_encode(['error' => 'Database connection error']);
    exit;
}

// Route handling
$requestPath = parse_url($_SERVER['REQUEST_URI'], PHP_URL_PATH);

// API endpoint to get graph data
if ($requestPath === '/productconfig/server.php') {
    // Query to fetch graph data
    $query = "
        WITH NodeData AS (
          -- Get all entities
          SELECT 
            e.ID, 
            et.NAME as TYPE,
            e.INTEGER_VALUE,
            e.TEXT_VALUE,
            e.BOOLEAN_VALUE,
            e.REAL_VALUE,
            e.NUMERIC_VALUE,
            e.SOURCE_FILE
          FROM Entity e
          JOIN EntityType et ON e.TYPE_ID = et.ID
        )
        
        SELECT 
          r.ID as relationship_id,
          r.RELATIONSHIP_TYPE,
          r.SOURCE_ID,
          r.PROPERTY_ID,
          r.TARGET_ID,
          
          -- Source node data
          s.ID as source_node_id,
          s.TYPE as source_type,
          s.INTEGER_VALUE as source_int,
          s.TEXT_VALUE as source_text,
          s.BOOLEAN_VALUE as source_bool,
          s.REAL_VALUE as source_real,
          s.NUMERIC_VALUE as source_numeric,
          s.SOURCE_FILE as source_file,
          
          -- Property node data
          p.ID as property_node_id,
          p.TYPE as property_type,
          p.INTEGER_VALUE as property_int,
          p.TEXT_VALUE as property_text,
          p.BOOLEAN_VALUE as property_bool,
          p.REAL_VALUE as property_real,
          p.NUMERIC_VALUE as property_numeric,
          p.SOURCE_FILE as property_file,
          
          -- Target node data
          t.ID as target_node_id,
          t.TYPE as target_type,
          t.INTEGER_VALUE as target_int,
          t.TEXT_VALUE as target_text,
          t.BOOLEAN_VALUE as target_bool,
          t.REAL_VALUE as target_real,
          t.NUMERIC_VALUE as target_numeric,
          t.SOURCE_FILE as target_file
          
        FROM Relationship r
        JOIN NodeData s ON r.SOURCE_ID = s.ID
        JOIN NodeData p ON r.PROPERTY_ID = p.ID
        LEFT JOIN NodeData t ON r.TARGET_ID = t.ID
    ";

    try {
        $stmt = $db->prepare($query);
        $stmt->execute();
        $rows = $stmt->fetchAll(PDO::FETCH_ASSOC);
        
        // Process data to create nodes and links
        $nodes = [];
        $links = [];
        
        // Helper function to get node label
        function getNodeLabel($node) {
            if ($node['type'] === 'INTEGER' && $node['int_value'] !== null) return (string)$node['int_value'];
            if ($node['type'] === 'TEXT' && $node['text_value'] !== null) return $node['text_value'];
            if ($node['type'] === 'BOOLEAN' && $node['bool_value'] !== null) return $node['bool_value'] ? 'true' : 'false';
            if ($node['type'] === 'REAL' && $node['real_value'] !== null) return (string)$node['real_value'];
            if ($node['type'] === 'NUMERIC' && $node['numeric_value'] !== null) return (string)$node['numeric_value'];
            if ($node['type'] === 'OBJECT') return 'Object';
            if ($node['type'] === 'ARRAY') return 'Array';
            if ($node['type'] === 'NULL') return 'null';
            return $node['type'];
        }
        
        // Process each relationship
        foreach ($rows as $row) {
            // Source node
            if (!isset($nodes[$row['source_node_id']])) {
                $nodes[$row['source_node_id']] = [
                    'id' => $row['source_node_id'],
                    'type' => $row['source_type'],
                    'int_value' => $row['source_int'],
                    'text_value' => $row['source_text'],
                    'bool_value' => $row['source_bool'],
                    'real_value' => $row['source_real'],
                    'numeric_value' => $row['source_numeric'],
                    'file' => $row['source_file'],
                    'label' => getNodeLabel([
                        'type' => $row['source_type'],
                        'int_value' => $row['source_int'],
                        'text_value' => $row['source_text'],
                        'bool_value' => $row['source_bool'],
                        'real_value' => $row['source_real'],
                        'numeric_value' => $row['source_numeric']
                    ])
                ];
            }
            
            // Property node
            if (!isset($nodes[$row['property_node_id']])) {
                $nodes[$row['property_node_id']] = [
                    'id' => $row['property_node_id'],
                    'type' => $row['property_type'],
                    'int_value' => $row['property_int'],
                    'text_value' => $row['property_text'],
                    'bool_value' => $row['property_bool'],
                    'real_value' => $row['property_real'],
                    'numeric_value' => $row['property_numeric'],
                    'file' => $row['property_file'],
                    'label' => getNodeLabel([
                        'type' => $row['property_type'],
                        'int_value' => $row['property_int'],
                        'text_value' => $row['property_text'],
                        'bool_value' => $row['property_bool'],
                        'real_value' => $row['property_real'],
                        'numeric_value' => $row['property_numeric']
                    ])
                ];
            }
            
            // Target node (if exists)
            if ($row['target_node_id'] && !isset($nodes[$row['target_node_id']])) {
                $nodes[$row['target_node_id']] = [
                    'id' => $row['target_node_id'],
                    'type' => $row['target_type'],
                    'int_value' => $row['target_int'],
                    'text_value' => $row['target_text'],
                    'bool_value' => $row['target_bool'],
                    'real_value' => $row['target_real'],
                    'numeric_value' => $row['target_numeric'],
                    'file' => $row['target_file'],
                    'label' => getNodeLabel([
                        'type' => $row['target_type'],
                        'int_value' => $row['target_int'],
                        'text_value' => $row['target_text'],
                        'bool_value' => $row['target_bool'],
                        'real_value' => $row['target_real'],
                        'numeric_value' => $row['target_numeric']
                    ])
                ];
            }
            
            // Create links
            if ($row['target_node_id']) {
                $links[] = [
                    'id' => $row['relationship_id'],
                    'source' => $row['source_node_id'],
                    'target' => $row['target_node_id'],
                    'property' => $row['property_node_id'],
                    'type' => $row['RELATIONSHIP_TYPE'],
                    'propertyLabel' => $nodes[$row['property_node_id']]['label']
                ];
            }
        }
        
        // Convert nodes array to indexed array (equivalent to Array.from(nodes.values()))
        $nodesArray = array_values($nodes);
        
        // Send response
        echo json_encode([
            'nodes' => $nodesArray,
            'links' => $links
        ]);
        
    } catch (PDOException $e) {
        http_response_code(500);
        echo json_encode(['error' => $e->getMessage()]);
    }
} 
// Home route
elseif ($requestPath === '/' || $requestPath === '/index.php') {
    header('Content-Type: text/html');
    include('public/indexphp.html');
} 
// Serve static files
else {
    $filePath = 'public' . $requestPath;
    
    if (file_exists($filePath)) {
        // Set appropriate content type based on file extension
        $extension = pathinfo($filePath, PATHINFO_EXTENSION);
        switch ($extension) {
            case 'css':
                header('Content-Type: text/css');
                break;
            case 'js':
                header('Content-Type: application/javascript');
                break;
            case 'html':
                header('Content-Type: text/html');
                break;
            case 'json':
                header('Content-Type: application/json');
                break;
            case 'png':
                header('Content-Type: image/png');
                break;
            case 'jpg':
            case 'jpeg':
                header('Content-Type: image/jpeg');
                break;
            default:
                header('Content-Type: application/octet-stream');
        }
        
        readfile($filePath);
    } else {
        http_response_code(404);
        echo 'File not found';
    }
}
?>
