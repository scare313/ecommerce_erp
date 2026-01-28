import os
from sqlalchemy import create_engine, text

# --- PATH CONFIGURATION ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))

DB_PATH = os.path.join(PROJECT_ROOT, "data", "db", "ecommerce.db")
SCHEMA_PATH = os.path.join(SCRIPT_DIR, "schema.sql")

# Ensure DB directory exists
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

DB_URL = f"sqlite:///{DB_PATH}"

def init_database():
    print(f"📂 Project Root: {PROJECT_ROOT}")
    print(f"💾 Database Path: {DB_PATH}")

    if not os.path.exists(SCHEMA_PATH):
        print(f"❌ Error: schema.sql not found at {SCHEMA_PATH}")
        return

    engine = create_engine(DB_URL)

    with open(SCHEMA_PATH, 'r') as f:
        sql_script = f.read()

    print("🏗️  Initializing Database Schema...")
    with engine.connect() as conn:
        # Split by ';' to run commands safely
        statements = sql_script.split(';')
        for statement in statements:
            if statement.strip():
                try:
                    conn.execute(text(statement))
                except Exception as e:
                    print(f"⚠️ Warning: {e}")
        
        conn.commit()
    
    print("✅ Database ready!")

if __name__ == "__main__":
    init_database()