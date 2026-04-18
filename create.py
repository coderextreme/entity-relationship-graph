#!/usr/bin/env python3
"""
create_schema.py - Creates or recreates the database schema for the object graph storage system.
"""

import sqlite3
import argparse
import os

def create_schema(db_path):
    """
    Creates or recreates the SQLite database schema with the required tables and initial data.
    """
    # Remove the existing database file if it exists
    if os.path.exists(db_path):
        os.remove(db_path)
    
    # Connect to the database
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Enable foreign key constraints
    cursor.execute("PRAGMA foreign_keys = ON")
    
    # Create EntityType table
    cursor.execute("""
    CREATE TABLE EntityType (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE NOT NULL
    )
    """)
    
    # Create Entity table
    cursor.execute("""
    CREATE TABLE Entity (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        entity_type_id INTEGER NOT NULL,
        value_str TEXT,
        value_num REAL,
        value_bool INTEGER,
        FOREIGN KEY (entity_type_id) REFERENCES EntityType(id)
    )
    """)
    
    # Create Relationship table
    cursor.execute("""
    CREATE TABLE Relationship (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        parent_entity_id INTEGER NOT NULL,
        child_entity_id INTEGER NOT NULL,
        relationship_specifier_id INTEGER NOT NULL,
        FOREIGN KEY (parent_entity_id) REFERENCES Entity(id),
        FOREIGN KEY (child_entity_id) REFERENCES Entity(id),
        FOREIGN KEY (relationship_specifier_id) REFERENCES Entity(id),
        UNIQUE(parent_entity_id, relationship_specifier_id)
    )
    """)
    
    # Create indexes to improve query performance
    cursor.execute("CREATE INDEX idx_entity_type_id ON Entity(entity_type_id)")
    cursor.execute("CREATE INDEX idx_parent_entity_id ON Relationship(parent_entity_id)")
    cursor.execute("CREATE INDEX idx_child_entity_id ON Relationship(child_entity_id)")
    cursor.execute("CREATE INDEX idx_relationship_specifier_id ON Relationship(relationship_specifier_id)")
    
    # Populate EntityType table with initial values
    entity_types = [
        ('Object',),
        ('Array',),
        ('String',),
        ('Number',),
        ('Boolean',),
        ('Null',)
    ]
    
    cursor.executemany("INSERT INTO EntityType (name) VALUES (?)", entity_types)
    
    # Commit the changes and close the connection
    conn.commit()
    conn.close()
    
    print(f"Database created successfully at: {db_path}")

def main():
    parser = argparse.ArgumentParser(description='Create or recreate the database schema for the object graph storage system.')
    parser.add_argument('--db_path', default='generic_store.db', help='Path to the SQLite database file')
    
    args = parser.parse_args()
    create_schema(args.db_path)

if __name__ == "__main__":
    main()
