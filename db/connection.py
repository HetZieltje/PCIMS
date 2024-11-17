import sqlite3
import os

def get_connection():
    """Get a database connection, creating the database and schema if necessary."""
    db_path = os.path.join(os.path.dirname(__file__), 'pcims_db.db')

    # Connect to the database
    connection = sqlite3.connect(db_path)
    
    return connection
