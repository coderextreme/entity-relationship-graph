const express = require('express');
const sqlite3 = require('sqlite3').verbose();
const path = require('path');

const app = express();
const port = 3000;

// Serve static files
app.use(express.static('public'));

// Database connection
const db = new sqlite3.Database('./EntityRelationship.sqlite3', (err) => {
  if (err) {
    console.error('Error opening database:', err.message);
  } else {
    console.log('Connected to the EntityRelationship database');
  }
});

// API endpoint to get graph data
app.get('/api/graph', (req, res) => {
  const query = `
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
  `;

  db.all(query, [], (err, rows) => {
    if (err) {
      res.status(500).json({ error: err.message });
      return;
    }
    
    // Process data to create nodes and links
    const nodes = new Map();
    const links = [];
    
    // Helper function to get node label
    const getNodeLabel = (node) => {
      if (node.type === 'INTEGER' && node.int_value !== null) return node.int_value.toString();
      if (node.type === 'TEXT' && node.text_value !== null) return node.text_value;
      if (node.type === 'BOOLEAN' && node.bool_value !== null) return node.bool_value ? 'true' : 'false';
      if (node.type === 'REAL' && node.real_value !== null) return node.real_value.toString();
      if (node.type === 'NUMERIC' && node.numeric_value !== null) return node.numeric_value.toString();
      if (node.type === 'OBJECT') return 'Object';
      if (node.type === 'ARRAY') return 'Array';
      if (node.type === 'NULL') return 'null';
      return node.type;
    };
    
    // Process each relationship
    rows.forEach(row => {
      // Source node
      if (!nodes.has(row.source_node_id)) {
        nodes.set(row.source_node_id, {
          id: row.source_node_id,
          type: row.source_type,
          int_value: row.source_int,
          text_value: row.source_text,
          bool_value: row.source_bool,
          real_value: row.source_real,
          numeric_value: row.source_numeric,
          file: row.source_file,
          label: getNodeLabel({
            type: row.source_type,
            int_value: row.source_int,
            text_value: row.source_text,
            bool_value: row.source_bool,
            real_value: row.source_real,
            numeric_value: row.source_numeric
          })
        });
      }
      
      // Property node
/*
      if (!nodes.has(row.property_node_id)) {
        nodes.set(row.property_node_id, {
          id: row.property_node_id,
          type: row.property_type,
          int_value: row.property_int,
          text_value: row.property_text,
          bool_value: row.property_bool,
          real_value: row.property_real,
          numeric_value: row.property_numeric,
          file: row.property_file,
          label: getNodeLabel({
            type: row.property_type,
            int_value: row.property_int,
            text_value: row.property_text,
            bool_value: row.property_bool,
            real_value: row.property_real,
            numeric_value: row.property_numeric
          })
        });
      }
*/
      
      // Target node (if exists)
      if (row.target_node_id && !nodes.has(row.target_node_id)) {
        nodes.set(row.target_node_id, {
          id: row.target_node_id,
          type: row.target_type,
          int_value: row.target_int,
          text_value: row.target_text,
          bool_value: row.target_bool,
          real_value: row.target_real,
          numeric_value: row.target_numeric,
          file: row.target_file,
          label: getNodeLabel({
            type: row.target_type,
            int_value: row.target_int,
            text_value: row.target_text,
            bool_value: row.target_bool,
            real_value: row.target_real,
            numeric_value: row.target_numeric
          })
        });
      }
      
      // Create links
      if (row.target_node_id) {
        links.push({
          id: row.relationship_id,
          source: row.source_node_id,
          target: row.target_node_id,
          property: row.property_node_id,
          type: row.RELATIONSHIP_TYPE,
          propertyLabel: nodes.get(row.property_node_id) ? nodes.get(row.property_node_id).label : getNodeLabel({
            type: row.property_type,
            int_value: row.property_int,
            text_value: row.property_text,
            bool_value: row.property_bool,
            real_value: row.property_real,
            numeric_value: row.property_numeric
          })
        });
      }
    });
    
    // Convert to array and send response
    res.json({
      nodes: Array.from(nodes.values()),
      links: links
    });
  });
});

// Home route
app.get('/', (req, res) => {
  res.sendFile(path.join(__dirname, 'public', 'index.html'));
});

// Start server
app.listen(port, () => {
  console.log(`Server running at http://localhost:${port}/index.html`);
  console.log(`Server running at http://localhost:${port}/index3danimation.html`);
  console.log(`Server running at http://localhost:${port}/hierarchy.html`);
  console.log(`Server running at http://localhost:${port}/tidytree.html`);
  console.log(`Server running at http://localhost:${port}/api/graph`);
});
