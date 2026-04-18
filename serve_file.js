// server_fs.js (UPDATED)
const express = require('express');
const path = require('path');
const sqlite3 = require('sqlite3').verbose();

const app = express();
const port = 3000;
const dbPath = path.join(__dirname, 'generic_store.db');

// --- Database Helper Functions (same as before) ---
function openDb() {
    return new Promise((resolve, reject) => {
        const db = new sqlite3.Database(dbPath, sqlite3.OPEN_READONLY, (err) => {
            if (err) {
                console.error('Error opening database:', err.message);
                reject(new Error(`Could not open database: ${err.message}`));
            } else {
                console.log('Database connection opened successfully.');
                db.run('PRAGMA foreign_keys = ON;', (pragmaErr) => {
                   if (pragmaErr) {
                       console.warn('Could not enable foreign keys:', pragmaErr.message);
                   }
                   resolve(db);
                });
            }
        });
    });
}

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
            resolve();
        }
    });
}

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


// --- File System Specific Functions ---

async function buildFileSystemTree(db) {
    console.log("buildFileSystemTree started"); // Added log

    // 1. Fetch all entities that are either folders (Objects) or files (Arrays).
    const entities = await queryDb(db, `
        SELECT e.id, et.name as type, e.value_str, e.value_num, e.value_bool
        FROM Entity e
        JOIN EntityType et ON e.entity_type_id = et.id
        WHERE et.name IN ('Object', 'Array')
    `);

    console.log("Entities fetched:", entities); // Added log

    // Create a map of entity ID to entity data.
    const entityMap = new Map(entities.map(entity => [entity.id, entity]));

    // 2. Fetch all relationships between folders and files.
    const relationships = await queryDb(db, `
        SELECT r.parent_entity_id, r.child_entity_id, se.value_str AS name
        FROM Relationship r
        JOIN Entity se ON r.relationship_specifier_id = se.id
        JOIN EntityType setype ON se.entity_type_id = setype.id
        WHERE setype.name = 'String'
    `);

    console.log("Relationships fetched:", relationships); // Added log


    // 3. Build the tree structure.
    const rootNodes = [];
    const childrenMap = new Map(); // parentId -> [children]

    for (const rel of relationships) {
        const parentId = rel.parent_entity_id;
        const childId = rel.child_entity_id;
        const childName = rel.name;

        if (!entityMap.has(parentId) || !entityMap.has(childId)) {
            console.warn(`Parent or child entity missing (parentId: ${parentId}, childId: ${childId})`);
            continue; // Parent or child not an object/array
        }

        const childEntity = entityMap.get(childId);
        const childNode = {
            id: childId,
            text: childName, // Use relationship specifier (filename) as text
            type: childEntity.type === 'Object' ? 'directory' : 'file', // Use lower case to match jstree's needs.
            children: [], // Initialize children array
            data: {
                value_str: childEntity.value_str,
                value_num: childEntity.value_num,
                value_bool: childEntity.value_bool
            }
        };

        if (!childrenMap.has(parentId)) {
            childrenMap.set(parentId, []);
        }
        childrenMap.get(parentId).push(childNode);
    }

    //Find root directories (those with no parents).  Important if the database doesn't have a single top-level directory.
    const allParentIds = new Set(relationships.map(r => r.parent_entity_id));
    const rootDirectories = entities.filter(e => e.type === 'Object' && !allParentIds.has(e.id));

    for(const rootDir of rootDirectories) {
        const rootNode = {
            id: rootDir.id,
            text: rootDir.value_str || "Root",  //Label of root directory
            type: 'directory',
            children: childrenMap.get(rootDir.id) || [],
            data: {
                value_str: rootDir.value_str,
                value_num: rootDir.value_num,
                value_bool: rootDir.value_bool
            }
        };
        rootNodes.push(rootNode);
    }


    // Assign children to their parents
    for (const rel of relationships) {
      const parentId = rel.parent_entity_id;
        if (entityMap.get(parentId).type === 'Array') continue; // skip files as parents
      const childId = rel.child_entity_id;
      const parentChildren = childrenMap.get(parentId);
      if (parentChildren) {
          //No need to add.  Already added when creating the map
      }
    }

    console.log("File system tree built:", rootNodes); // Added log

    return rootNodes.length === 1 ? rootNodes[0] : rootNodes; // Return array of roots or single root
}



// --- Express Routes ---

// Serve static files (HTML, CSS, JS for visualization)
app.use(express.static(path.join(__dirname, 'public')));

app.get('/', (req, res) => {
    res.sendFile(path.join(__dirname, 'public', 'index.html')); // Or your main HTML
});

// API endpoint to get the file system data
app.get('/fsdata', async (req, res) => {
    let db;
    try {
        db = await openDb();
        const fileSystemData = await buildFileSystemTree(db);
        res.json(fileSystemData);
    } catch (error) {
        console.error("Error fetching file system data:", error);
        res.status(500).json({ error: "Failed to fetch file system data.", details: error.message });
    } finally {
        if (db) {
            await closeDb(db);
        }
    }
});

// Start the server
app.listen(port, () => {
    console.log(`Server listening at http://localhost:${port}`);
    console.log(`Serving static files from ${path.join(__dirname, 'public')}`);
    console.log(`Serving data from ${dbPath}`);
});

process.on('uncaughtException', (err) => {
    console.error('Uncaught Exception:', err);
});
