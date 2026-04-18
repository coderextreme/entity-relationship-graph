// server.js (Refined - Filters Nodes, Puts Spec Info on Links)
const express = require('express');
const sqlite3 = require('sqlite3').verbose();
const path = require('path');
const { promisify } = require('util'); // Not strictly needed with sqlite3 async, but good pattern

const app = express();
const PORT = process.env.PORT || 3000; // Use 3000 for Node.js convention

// --- Configuration ---
const DB_PATH = path.join(__dirname, 'generic_store.db');
const HTML_PATH = path.join(__dirname, 'index.html');

// --- Database Connection Helper ---
function connectDb() {
    return new Promise((resolve, reject) => {
        // Open READONLY - prevents accidental modification
        const db = new sqlite3.Database(DB_PATH, sqlite3.OPEN_READONLY, (err) => {
            if (err) {
                console.error("Error connecting to database:", err.message);
                reject(new Error('Could not connect to database.'));
            } else {
                console.log('Connected to the SQLite database (read-only).');
                resolve(db);
            }
        });
    });
}
// Promisify db.all and db.get (robust error handling)
function dbAll(db, sql, params = []) {
    return new Promise((resolve, reject) => {
        db.all(sql, params, (err, rows) => {
            if (err) {
                console.error("DB Query Error (all):", sql, params, err.message);
                reject(new Error('Database query failed (all).'));
            } else {
                resolve(rows);
            }
        });
    });
}
function dbGet(db, sql, params = []) {
     return new Promise((resolve, reject) => {
        db.get(sql, params, (err, row) => {
            if (err) {
                console.error("DB Query Error (get):", sql, params, err.message);
                reject(new Error('Database query failed (get).'));
            } else {
                resolve(row); // row is undefined if not found
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
        db = await connectDb();

        // --- Step 1: Identify Connected Node IDs (Strictly Parents, Children, Roots) ---
        const connectedIds = new Set();
        const relIdSql = `SELECT DISTINCT parent_entity_id as id FROM Relationship UNION SELECT DISTINCT child_entity_id as id FROM Relationship`;
        const relIdRows = await dbAll(db, relIdSql);
        relIdRows.forEach(row => connectedIds.add(row.id));
        const rootIdSql = `SELECT e.id FROM Entity e LEFT JOIN Relationship r ON e.id = r.child_entity_id WHERE r.id IS NULL`;
        const rootIdRows = await dbAll(db, rootIdSql);
        rootIdRows.forEach(row => connectedIds.add(row.id));
        if (connectedIds.size === 0) {
            const allNodeRows = await dbAll(db, "SELECT id FROM Entity");
            allNodeRows.forEach(row => connectedIds.add(row.id));
        }

        // --- Step 2: Fetch ONLY Connected Nodes ---
        const nodes = [];
        if (connectedIds.size > 0) {
            const idsToFetch = Array.from(connectedIds);
            const placeholders = idsToFetch.map(() => '?').join(',');
            const nodeSql = `SELECT e.id, et.name as type, e.value_str, e.value_num, e.value_bool FROM Entity e JOIN EntityType et ON e.entity_type_id = et.id WHERE e.id IN (${placeholders})`;
            const nodeRows = await dbAll(db, nodeSql, idsToFetch);
            for (const row of nodeRows) {
                 const nodeData = { id: row.id, type: row.type };
                 let val = undefined; // Use undefined to distinguish from actual null
                 if (row.type === 'String' && row.value_str !== null) { val = row.value_str;}
                 else if (row.type === 'Number' && row.value_num !== null) { val = Number.isInteger(row.value_num) ? parseInt(row.value_num) : parseFloat(row.value_num);}
                 else if (row.type === 'Boolean' && row.value_bool !== null) { val = Boolean(row.value_bool);}
                 else if (row.type === 'Null') { val = null;} // Explicit null
                 if (val !== undefined) { nodeData.value = val; }

                 // Determine Label
                 if (val !== undefined) {
                     const labelVal = typeof val === 'string' ? val : JSON.stringify(val);
                     nodeData.label = labelVal.length > 15 ? labelVal.substring(0, 12) + '...' : labelVal;
                 } else if (['Object', 'Array', 'Version'].includes(row.type)) {
                     nodeData.label = row.type;
                 } else {
                     nodeData.label = `${row.type}:${row.id}`;
                 }
                 nodes.push(nodeData);
             }
        }


        // --- Step 3: Fetch Relationships and Add Specifier Info to the SINGLE Link ---
        const links = [];
        const linkSql = `
            SELECT r.parent_entity_id as source, r.child_entity_id as target, r.relationship_specifier_id as spec_id,
                   spet.name as spec_type_name, spe.value_str as spec_val_str, spe.value_num as spec_val_num, spe.value_bool as spec_val_bool
            FROM Relationship r JOIN Entity spe ON r.relationship_specifier_id = spe.id JOIN EntityType spet ON spe.entity_type_id = spet.id
        `;
        const linkRows = await dbAll(db, linkSql);
        for (const row of linkRows) {
            const sourceId = row.source;
            const targetId = row.target;

            // CRITICAL: Filter links based on fetched nodes
            if (!connectedIds.has(sourceId) || !connectedIds.has(targetId)) {
                continue; // Skip if source or target isn't in our node list
            }

            const linkData = { source: sourceId, target: targetId, specifier_id: row.spec_id };
            const spec_type = row.spec_type_name;
            linkData.spec_type = spec_type; // Always include type

            let spec_val = undefined;
            let link_label = `[${spec_type} Spec]`; // Default label

            if (spec_type === 'String' && row.spec_val_str !== null) {
                spec_val = row.spec_val_str; linkData.key = spec_val; link_label = spec_val;
            } else if (spec_type === 'Number' && row.spec_val_num !== null) {
                spec_val = Number.isInteger(row.spec_val_num) ? parseInt(row.spec_val_num) : parseFloat(row.spec_val_num); linkData.index = spec_val; link_label = String(spec_val);
            } else if (spec_type === 'Boolean' && row.spec_val_bool !== null) {
                spec_val = Boolean(row.spec_val_bool); linkData.spec_value = spec_val; link_label = String(spec_val);
            } else if (spec_type === 'Null') {
                linkData.spec_value = null; link_label = 'null';
            } else {
                 linkData.specifier_complex = true; // Flag complex specifier
            }
            linkData.label = link_label;

            links.push(linkData);
        }

        // --- Combine and Output ---
        const outputData = { nodes, links };
        res.json(outputData); // Handles JSON conversion and Content-Type

    } catch (err) {
        console.error("Error processing /data request:", err);
        res.status(500).json({ error: err.message || 'Failed to retrieve graph data.' });
    } finally {
        if (db) {
            db.close((err) => {
                if (err) { console.error("Error closing database connection:", err.message); }
                else { console.log('Database connection closed.'); }
            });
        }
    }
});

// --- Start Server ---
app.listen(PORT, () => {
    console.log(`Node.js server listening on http://localhost:${PORT}`);
    console.log(`Database file: ${DB_PATH}`);
    console.log(`HTML file: ${HTML_PATH}`);
});

// Basic 404 for other routes
app.use((req, res) => {
    res.status(404).send("404 Not Found");
});
