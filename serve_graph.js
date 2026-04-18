// server.js
const express = require('express');
const path = require('path');
const sqlite3 = require('sqlite3').verbose(); // Use verbose for more detailed errors

const app = express();
const port = 3000;
const dbPath = path.join(__dirname, 'generic_store.db'); // Assumes DB is in the same directory

// --- Database Helper Functions ---

// Function to open the database connection (read-only)
function openDb() {
    return new Promise((resolve, reject) => {
        const db = new sqlite3.Database(dbPath, sqlite3.OPEN_READONLY, (err) => {
            if (err) {
                console.error('Error opening database:', err.message);
                reject(new Error(`Could not open database: ${err.message}`));
            } else {
                console.log('Database connection opened successfully.');
                // Enable foreign keys (good practice, though less critical for read-only)
                db.run('PRAGMA foreign_keys = ON;', (pragmaErr) => {
                   if (pragmaErr) {
                       console.warn('Could not enable foreign keys:', pragmaErr.message);
                       // Don't reject, just warn for read-only scenarios
                   }
                   resolve(db);
                });
            }
        });
    });
}

// Function to close the database connection
function closeDb(db) {
    return new Promise((resolve, reject) => {
        if (db) {
            db.close((err) => {
                if (err) {
                    console.error('Error closing database:', err.message);
                    reject(new Error(`Could not close database: ${err.message}`));
                } else {
                    console.log('Database connection closed.');
                    resolve();
                }
            });
        } else {
            resolve(); // No DB to close
        }
    });
}

// Function to query the database
function queryDb(db, sql, params = []) {
    return new Promise((resolve, reject) => {
        db.all(sql, params, (err, rows) => {
            if (err) {
                console.error('Error running query:', sql, params, err.message);
                reject(new Error(`Database query failed: ${err.message}`));
            } else {
                resolve(rows);
            }
        });
    });
}

// --- Helper to determine display value/label ---
function getNodeDisplayInfo(entityType, valueStr, valueNum, valueBool) {
    let value = null;
    let label = entityType; // Default label is the type

    switch (entityType) {
        case 'String':
            value = valueStr;
            label = `"${valueStr}"`; // Add quotes for clarity
            break;
        case 'Number':
            value = valueNum;
            label = String(valueNum);
            break;
        case 'Boolean':
            value = Boolean(valueBool); // Convert 0/1 to false/true
            label = String(value);
            break;
        case 'Null':
            value = null;
            label = 'null';
            break;
        // For containers, value remains null, label is already type
        case 'Object':
        case 'Array':
        case 'Version': // Represents OrderedDict
            break;
        default:
            label = `${entityType}?`; // Unknown type
    }
    return { value, label };
}

function getSpecifierDisplayInfo(specEntityType, specValueStr, specValueNum, specValueBool) {
    let specValue = null;
    let label = '';
    let isComplex = false;

    switch (specEntityType) {
        case 'String':
            specValue = specValueStr;
            label = `"${specValueStr}"`; // Key as string
            break;
        case 'Number':
            specValue = specValueNum;
            label = String(specValueNum); // Index as number
            break;
        case 'Boolean':
             specValue = Boolean(specValueBool);
             label = String(specValue); // Boolean key/index (less common)
             break;
        case 'Null':
             specValue = null;
             label = 'null'; // Null key/index (less common)
             break;
        case 'Object':
        case 'Array':
        case 'Version':
            specValue = null; // Value is complex, not primitive
            label = `<${specEntityType}>`; // Indicate complex key/specifier
            isComplex = true;
            break;
        default:
            label = `<?>`; // Unknown specifier type
            isComplex = true; // Assume complex if unknown
    }
    return { specValue, label, isComplex };
}


// --- Express Routes ---

// Serve the static HTML file
app.get('/', (req, res) => {
    res.sendFile(path.join(__dirname, 'index.html'));
});

// Serve the D3.js compatible JSON data
app.get('/data', async (req, res) => {
    let db;
    try {
        db = await openDb();

        // 1. Identify IDs of nodes that should appear in the graph
        // These are entities that are either parents or children in *any* relationship.
        // Using UNION ensures distinct IDs.
        const primaryNodeIdRows = await queryDb(db, `
            SELECT DISTINCT parent_entity_id as id FROM Relationship
            UNION
            SELECT DISTINCT child_entity_id as id FROM Relationship
        `);
        const primaryNodeIds = new Set(primaryNodeIdRows.map(row => row.id));

        // Handle potential root nodes that might not have relationships (rare if data imported)
        // A more robust (but potentially slower) way for roots without relationships:
        // Find entities not present as child_entity_id
        // const potentialRootRows = await queryDb(db, `
        //     SELECT id FROM Entity e WHERE NOT EXISTS (
        //         SELECT 1 FROM Relationship r WHERE r.child_entity_id = e.id
        //     )`);
        // potentialRootRows.forEach(row => primaryNodeIds.add(row.id));
        // Simplified approach assumes relevant data is linked.

        if (primaryNodeIds.size === 0) {
             // If no relationships, maybe fetch the first entity as a single node?
             // Or just return empty graph. Let's return empty for now.
            console.log("No relationships found, returning empty graph data.");
             res.json({ nodes: [], links: [] });
             await closeDb(db);
             return;
        }

        // 2. Fetch data *only* for these primary nodes
        const nodesData = await queryDb(db, `
            SELECT e.id, et.name as type, e.value_str, e.value_num, e.value_bool
            FROM Entity e
            JOIN EntityType et ON e.entity_type_id = et.id
            WHERE e.id IN (${Array.from(primaryNodeIds).join(',')})
        `);

        const nodes = nodesData.map(row => {
            const { value, label } = getNodeDisplayInfo(row.type, row.value_str, row.value_num, row.value_bool);
            return {
                id: row.id,
                type: row.type,
                value: value, // Store original value
                label: label  // Store display label
            };
        });

        // Create a quick lookup map for nodes that are actually included
        const nodesById = new Map(nodes.map(n => [n.id, n]));


        // 3. Fetch all relationships, joining with specifier entity details
        const relationshipsData = await queryDb(db, `
            SELECT
                r.parent_entity_id,
                r.child_entity_id,
                r.relationship_specifier_id,
                se.id as spec_id,
                setype.name as spec_type,
                se.value_str as spec_value_str,
                se.value_num as spec_value_num,
                se.value_bool as spec_value_bool
            FROM Relationship r
            JOIN Entity se ON r.relationship_specifier_id = se.id
            JOIN EntityType setype ON se.entity_type_id = setype.id
        `);

        // 4. Generate links, embedding specifier info, and filtering
        const links = [];
        for (const row of relationshipsData) {
            // IMPORTANT: Filter links - only include if BOTH source and target nodes are in our primary node list
            if (nodesById.has(row.parent_entity_id) && nodesById.has(row.child_entity_id)) {
                const { specValue, label: specLabel, isComplex } = getSpecifierDisplayInfo(
                    row.spec_type,
                    row.spec_value_str,
                    row.spec_value_num,
                    row.spec_value_bool
                );

                links.push({
                    source: row.parent_entity_id,
                    target: row.child_entity_id,
                    // Embed specifier info directly into the link object
                    specifier_id: row.relationship_specifier_id,
                    spec_type: row.spec_type,
                    spec_value: specValue,
                    specifier_complex: isComplex, // Flag if specifier was Object/Array etc.
                    label: specLabel // The label to display on the link
                });
            }
        }

        // 5. Assemble final structure and send
        const graphData = { nodes, links };
        res.json(graphData);

    } catch (error) {
        console.error("Error fetching graph data:", error);
        res.status(500).json({ error: "Failed to fetch graph data from the server.", details: error.message });
    } finally {
        if (db) {
            await closeDb(db);
        }
    }
});

// Start the server
app.listen(port, () => {
    console.log(`Server listening at http://localhost:${port}`);
    console.log(`Serving index.html from ${path.join(__dirname, 'index.html')}`);
    console.log(`Serving data from ${dbPath}`);
});

// Basic error handling for uncaught exceptions
process.on('uncaughtException', (err) => {
    console.error('Uncaught Exception:', err);
    // Consider exiting gracefully depending on the error
    // process.exit(1);
});
