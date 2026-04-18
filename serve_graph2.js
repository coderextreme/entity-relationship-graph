// server.js
const express = require('express');
const sqlite3 = require('sqlite3').verbose(); // Use verbose for more detailed errors
const path = require('path');

const app = express();
const port = process.env.PORT || 3000; // Use environment port or default

// Configuration
const dbPath = path.resolve(__dirname, 'generic_store.db'); // Absolute path to DB
const indexHtmlPath = path.resolve(__dirname, 'index.html');
const xiteHtmlPath = path.resolve(__dirname, 'x3dom.html');
const staticDir = path.resolve(__dirname); // Serve static files from the node server directory

// --- Database Helper Functions ---

// Promisify db.all and db.get for easier async/await usage
function dbAll(db, sql, params = []) {
    return new Promise((resolve, reject) => {
        db.all(sql, params, (err, rows) => {
            if (err) {
                console.error("Database query error (all):", err.message);
                reject(err);
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
                console.error("Database query error (get):", err.message);
                reject(err);
            } else {
                resolve(row);
            }
        });
    });
}

async function getEntityDetails(db, entityId) {
    const sql = `
        SELECT et.name, e.value_str, e.value_num, e.value_bool
        FROM Entity e
        JOIN EntityType et ON e.entity_type_id = et.id
        WHERE e.id = ?
    `;
    const row = await dbGet(db, sql, [entityId]);
    if (!row) {
        return [null, null];
    }

    const typeName = row.name;
    let value = null;
    switch (typeName) {
        case 'String': value = row.value_str; break;
        case 'Number': value = row.value_num === null ? null : Number(row.value_num); break;
        case 'Boolean': value = row.value_bool === null ? null : Boolean(row.value_bool); break;
        case 'Null': value = null; break;
    }
    return [typeName, value];
}

async function getSpecifierDetails(db, specifierId, cache) {
     if (cache[specifierId]) {
        return cache[specifierId];
     }

    const [specTypeName, specValue] = await getEntityDetails(db, specifierId);
    if (specTypeName === null) {
        const result = ["Unknown", null, false, "[Invalid Specifier]"];
        cache[specifierId] = result;
        return result;
    }

    const isComplex = ['Object', 'Array', 'Version'].includes(specTypeName);
    let label = "";
    if (specTypeName === 'String') {
        label = specValue !== null ? specValue : "''";
    } else if (specTypeName === 'Number') {
        label = String(specValue); // JS handles number formatting implicitly
    } else if (specTypeName === 'Boolean') {
        label = String(Boolean(specValue));
    } else if (specTypeName === 'Null') {
        label = 'null';
    } else if (isComplex) {
        label = `[${specTypeName}]`;
    } else {
        label = `[${specTypeName}]`; // Fallback
    }

    const result = [specTypeName, specValue, isComplex, label];
    cache[specifierId] = result;
    return result;
}


async function generateD3Json(db) {
    // 1. Identify potential nodes
    const nodeSql = `
        SELECT DISTINCT parent_entity_id AS id FROM Relationship
        UNION
        SELECT DISTINCT child_entity_id AS id FROM Relationship
        UNION
        SELECT id from Entity e WHERE NOT EXISTS (SELECT 1 FROM Relationship r WHERE r.child_entity_id = e.id)
    `;
    const nodeRows = await dbAll(db, nodeSql);
    const nodeIds = nodeRows.map(row => row.id);

    if (nodeIds.length === 0) {
        return { nodes: [], links: [] };
    }

    // 2. Fetch Node Details
    const placeholders = nodeIds.map(() => '?').join(',');
    const detailSql = `
        SELECT e.id, et.name, e.value_str, e.value_num, e.value_bool
        FROM Entity e
        JOIN EntityType et ON e.entity_type_id = et.id
        WHERE e.id IN (${placeholders})
    `;
    const nodeDetails = await dbAll(db, detailSql, nodeIds);
    const nodesList = [];
    const nodeMap = {}; // For quick lookup by ID

    nodeDetails.forEach(row => {
        const entityId = row.id;
        const typeName = row.name;
        let value = null;
        let label = typeName;

        switch (typeName) {
            case 'String':
                value = row.value_str;
                label = `"${value !== null ? value : ''}"`;
                break;
            case 'Number':
                value = row.value_num === null ? null : Number(row.value_num);
                label = String(value);
                break;
            case 'Boolean':
                value = row.value_bool === null ? null : Boolean(row.value_bool);
                label = String(value);
                break;
            case 'Null':
                value = null;
                label = 'null';
                break;
             case 'Object':
             case 'Array':
             case 'Version':
                 label = typeName; // Keep type name
                 break;
        }

        const nodeData = {
            id: entityId, // Keep as number
            type: typeName,
            value: value,
            label: label
        };
        nodesList.push(nodeData);
        nodeMap[entityId] = nodeData;
    });

    // 3. Fetch Relationships and Add Specifier Info
    const linksList = [];
    const relSql = "SELECT parent_entity_id, child_entity_id, relationship_specifier_id FROM Relationship";
    const relationships = await dbAll(db, relSql);
    const specifierCache = {}; // Cache specifiers for this request

    for (const row of relationships) {
        const parentId = row.parent_entity_id;
        const childId = row.child_entity_id;
        const specifierId = row.relationship_specifier_id;

        // Filter
        if (nodeMap[parentId] && nodeMap[childId]) {
            const [specType, specValue, specComplex, specLabel] = await getSpecifierDetails(db, specifierId, specifierCache);

            linksList.push({
                source: parentId,
                target: childId,
                specifier_id: specifierId,
                spec_type: specType,
                spec_value: specValue,
                specifier_complex: specComplex,
                label: specLabel
            });
        }
    }

    return { nodes: nodesList, links: linksList };
}


// --- Middleware and Routes ---

// Serve static files (HTML, CSS, JS libraries) from the 'node' directory
// app.use(express.static(staticDir)); // If you want to serve other assets

// Route for D3 JSON data
app.get('/data', (req, res) => {
    const db = new sqlite3.Database(dbPath, sqlite3.OPEN_READONLY, (err) => {
        if (err) {
            console.error("Error opening database:", err.message);
            return res.status(500).json({ error: 'Failed to open database' });
        }
        console.log('Connected to the SQLite database for /data request.');
    });

    db.exec("PRAGMA foreign_keys=ON;", async (err) => {
         if (err) {
             console.error("Error enabling foreign keys:", err.message);
              // Non-fatal, proceed but log it
         }

         try {
             const graphData = await generateD3Json(db);
             res.json(graphData);
         } catch (error) {
             console.error("Error generating D3 JSON:", error);
             res.status(500).json({ error: 'Error generating graph data' });
         } finally {
             db.close((err) => {
                 if (err) {
                     console.error("Error closing database:", err.message);
                 } else {
                     console.log('Closed the database connection for /data request.');
                 }
             });
         }
    });
});

// Route for X_ITE page
app.get('/x3dom.html', (req, res) => {
    res.sendFile(xiteHtmlPath, (err) => {
         if (err) {
            console.error("Error sending x3dom.html:", err);
            if (!res.headersSent) { // Avoid setting headers if already sent
                res.status(err.status || 500).send('Error serving X_ITE page');
            }
        }
    });
});
app.get('/x3dom', (req, res) => res.redirect('/x3dom.html')); // Redirect /x3dom to /x3dom.html

// Route for the main D3 visualization page
app.get('/', (req, res) => {
    res.sendFile(indexHtmlPath, (err) => {
        if (err) {
            console.error("Error sending index.html:", err);
             if (!res.headersSent) {
                res.status(err.status || 500).send('Error serving index page');
            }
        }
    });
});
app.get('/index.html', (req, res) => res.sendFile(indexHtmlPath)); // Explicitly serve index.html


// --- Start Server ---
app.listen(port, () => {
    console.log(`Node.js server listening on http://localhost:${port}/x3dom`);
    console.log(`Database path: ${dbPath}`);
    console.log(`Serving index.html from: ${indexHtmlPath}`);
    console.log(`Serving x3dom.html from: ${xiteHtmlPath}`);
});

// Basic Error Handling for uncaught exceptions
process.on('uncaughtException', (err) => {
  console.error('There was an uncaught error', err);
  process.exit(1); // Mandatory shutdown after uncaught exception
});
