# create_schema.py
import sqlite3
import os
import sys

DB_FILE = "generic_store.db"

# SQL statements
SQL_CREATE_ENTITY_TYPE = """
CREATE TABLE IF NOT EXISTS EntityType (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL
);
"""

SQL_CREATE_ENTITY = """
CREATE TABLE IF NOT EXISTS Entity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type_id INTEGER NOT NULL,
    value_str TEXT,      -- Stores String value
    value_num REAL,      -- Stores Number value (int/float)
    value_bool INTEGER,  -- Stores Boolean value (0/1)
    FOREIGN KEY (entity_type_id) REFERENCES EntityType(id)
);
"""

SQL_CREATE_RELATIONSHIP = """
CREATE TABLE IF NOT EXISTS Relationship (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_entity_id INTEGER NOT NULL,
    child_entity_id INTEGER NOT NULL,
    relationship_specifier_id INTEGER NOT NULL, -- FK to Entity (ANY type allowed)
    FOREIGN KEY (parent_entity_id) REFERENCES Entity(id),
    FOREIGN KEY (child_entity_id) REFERENCES Entity(id),
    FOREIGN KEY (relationship_specifier_id) REFERENCES Entity(id),
    -- Ensures a parent doesn't have the exact same specifier entity pointing to something else
    UNIQUE(parent_entity_id, relationship_specifier_id)
);
"""

SQL_INSERT_ENTITY_TYPES = """
INSERT OR IGNORE INTO EntityType (name) VALUES
('Object'), ('Array'), ('Version'), ('String'), ('Number'), ('Boolean'), ('Null');
"""

SQL_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_relationship_parent ON Relationship(parent_entity_id);
CREATE INDEX IF NOT EXISTS idx_relationship_child ON Relationship(child_entity_id);
CREATE INDEX IF NOT EXISTS idx_relationship_specifier ON Relationship(relationship_specifier_id);
CREATE INDEX IF NOT EXISTS idx_entity_type ON Entity(entity_type_id);
CREATE INDEX IF NOT EXISTS idx_entity_value_str ON Entity(value_str);
CREATE INDEX IF NOT EXISTS idx_entity_value_num ON Entity(value_num);
"""

def create_database(db_file=DB_FILE):
    """Creates the SQLite database and schema."""
    print(f"Creating database schema in '{db_file}'...")
    if os.path.exists(db_file):
        print(f"Deleting existing database file '{db_file}'.")
        try:
            os.remove(db_file)
        except OSError as e:
            print(f"Error deleting database file: {e}", file=sys.stderr)
            return # Stop if deletion fails

    conn = None
    try:
        conn = sqlite3.connect(db_file)
        # Enable foreign key enforcement (critical!)
        conn.execute("PRAGMA foreign_keys = ON;")
        cursor = conn.cursor()

        print("Creating table EntityType...")
        cursor.execute(SQL_CREATE_ENTITY_TYPE)

        print("Creating table Entity...")
        cursor.execute(SQL_CREATE_ENTITY)

        print("Creating table Relationship...")
        cursor.execute(SQL_CREATE_RELATIONSHIP)

        print("Populating EntityType table...")
        cursor.execute(SQL_INSERT_ENTITY_TYPES)

        print("Creating indexes...")
        cursor.executescript(SQL_INDEXES)

        conn.commit()
        print("Database schema created successfully.")

    except sqlite3.Error as e:
        print(f"Database error: {e}", file=sys.stderr)
        if conn:
            conn.rollback() # Rollback changes on error
    finally:
        if conn:
            conn.close()
            print("Database connection closed.")

if __name__ == "__main__":
    create_database()
