const express = require('express');
const sqlite3 = require('sqlite3').verbose(); // Use verbose for better debugging
const path = require('path');
const { promisify } = require('util'); // To promisify db methods if needed

const app = express();
const PORT = process.env.PORT || 3000; // Use environment port or default to 3000

// --- Configuration ---
const DB_PATH = path.join(__dirname, 'generic_store.db');
const HTML_PATH = path.join(__dirname, 'index.html');

// --- Database Connection Helper ---
// Function to open DB connection (basic example, no pooling)
function connectDb() {
    return new Promise((resolve, reject) => {
        const db = new sqlite3.Database(DB_PATH, sqlite3.OPEN_READONLY, (err) => {
            if (err) {
                console.error("Error connecting to database:", err.message);
                reject(new Error('Could not connect to database.'));
            } else {
                console.log('Connected to the SQLite database.');
                resolve(db);
            }
        });
    });
}

// Promisify db.all for easier async/await usage
function dbAll(db, sql, params = []) {
    return new Promise((resolve, reject) => {
        db.all(sql, params, (err, rows) => {
            if (err) {
                console.error("Database query error:", err.message);
                reject(new Error('Database query failed.'));
            } else {
                resolve(rows);
            }
        });
    });
}

// --- Routes ---

// Serve the HTML file
app.get('/', (req, res) => {
    res.sendFile(HTML_PATH, (err) => {
        if (err) {
            console.error("Error sending HTML file:", err);
            res.status(500).send("Error loading visualization page.");
        }
    });
});

// Serve the D3.js JSON data
app.get('/data', async (req, res) => {
    let db;
    try {
        db = await connectDb(); // Open connection for this request

        const nodes = [];
        const links = [];

        // --- Fetch Nodes ---
        const nodeSql = `
            SELECT e.id, et.name as type, e.value_str, e.value_num, e.value_bool
            FROM Entity e
            JOIN EntityType et ON e.entity_type_id = et.id
        `;
        const nodeRows = await dbAll(db, nodeSql);

        for (const row of nodeRows) {
            const nodeData = {
                id: row.id, // Keep original ID from DB
                type: row.type
            };

            // Add primitive value if present and relevant
            let val = undefined; // Use undefined to distinguish from actual null value
            if (row.type === 'String' && row.value_str !== null) {
                val = row.value_str;
            } else if (row.type === 'Number' && row.value_num !== null) {
                // Check if it's an integer
                val = Number.isInteger(row.value_num) ? parseInt(row.value_num) : parseFloat(row.value_num);
            } else if (row.type === 'Boolean' && row.value_bool !== null) {
                val = Boolean(row.value_bool);
            } else if (row.type === 'Null') {
                val = null;
            }

            if (val !== undefined) {
                nodeData.value = val;
            }

            // Add a label for visualization
             if (val !== undefined) {
                 // Create a shorter label from value if needed
                 const label = typeof val === 'string' ? val : JSON.stringify(val);
                 nodeData.label = label.length > 15 ? label.substring(0, 12) + '...' : label;
             } else if (['Object', 'Array', 'Version'].includes(row.type)) {
                 nodeData.label = row.type; // Default label for containers
             } else {
                 nodeData.label = `${row.type}:${row.id}`; // Fallback label
             }


            nodes.push(nodeData);
        }

        // --- Fetch Links ---
        const linkSql = `
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
        `;
        const linkRows = await dbAll(db, linkSql);

        for (const row of linkRows) {
            const linkData = {
                source: row.source, // Use original ID
                target: row.target  // Use original ID
            };

            const spec_type = row.spec_type_name;
            const spec_id = row.spec_id;

            // Extract specifier value or info
            let spec_val = undefined;
            let spec_info_added = false;

            if (spec_type === 'String' && row.spec_val_str !== null) {
                spec_val = row.spec_val_str;
                linkData.label = spec_val; // Use string keys as link label
                spec_info_added = true;
            } else if (spec_type === 'Number' && row.spec_val_num !== null) {
                spec_val = Number.isInteger(row.spec_val_num) ? parseInt(row.spec_val_num) : parseFloat(row.spec_val_num);
                linkData.index = spec_val; // Use number index as link property
                spec_info_added = true;
            } else if (spec_type === 'Boolean' && row.spec_val_bool !== null) {
                spec_val = Boolean(row.spec_val_bool);
                linkData.spec_value = spec_val;
                spec_info_added = true;
            } else if (spec_type === 'Null') {
                 linkData.spec_value = null;
                 spec_info_added = true;
            }

            // If specifier was a container or value was NULL (except for Null type itself)
            if (!spec_info_added) {
                linkData.specifier_id = spec_id;
                linkData.specifier_type = spec_type;
            }

            links.push(linkData);
        }

        // --- Combine and Output ---
        const outputData = { nodes, links };
        res.json(outputData); // Express sets Content-Type to application/json

    } catch (err) {
        console.error("Error processing /data request:", err);
        res.status(500).json({ error: err.message || 'Failed to retrieve graph data.' });
    } finally {
        // Close the database connection
        if (db) {
            db.close((err) => {
                if (err) {
                    console.error("Error closing database connection:", err.message);
                } else {
                    console.log('Database connection closed.');
                }
            });
        }
    }
});

// --- Start Server ---
app.listen(PORT, () => {
    console.log(`Server listening on http://localhost:${PORT}/`);
    console.log(`Database file: ${DB_PATH}`);
    console.log(`HTML file: ${HTML_PATH}`);
});

// Basic 404 for other routes
app.use((req, res) => {
    res.status(404).send("404 Not Found");
});
