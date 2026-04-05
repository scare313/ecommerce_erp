import os
from sqlalchemy import create_engine, text
from src.infrastructure.migration_script import run_migration

# --- PATH CONFIGURATION ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))

DB_PATH = os.path.join(PROJECT_ROOT, "data", "db", "ecommerce.db")
SCHEMA_PATH = os.path.join(SCRIPT_DIR, "schema.sql")

# Ensure DB directory exists
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

DB_URL = f"sqlite:///{DB_PATH}"

def init_database():
    """Initialize database and ensure data is loaded."""
    print(f"📂 Database Path: {DB_PATH}")
    
    # Check if database file exists
    db_exists = os.path.exists(DB_PATH)
    
    if db_exists:
        print(f"✅ Database file found: {os.path.getsize(DB_PATH)} bytes")
        # Check if it has the necessary tables with data
        try:
            engine = create_engine(DB_URL)
            with engine.connect() as conn:
                result = conn.execute(text("SELECT COUNT(*) FROM product_master"))
                product_count = result.scalar()
            
            if product_count > 0:
                print(f"✅ Database has {product_count} products. Ready to use.")
                return True
            else:
                print("⚠️  Database is empty. Running migration...")
                run_migration()
                return True
        except Exception as e:
            print(f"⚠️  Error checking database: {e}. Running migration...")
            run_migration()
            return True
    else:
        # Create new database
        if not os.path.exists(SCHEMA_PATH):
            print(f"❌ Error: schema.sql not found at {SCHEMA_PATH}")
            return False

        try:
            engine = create_engine(DB_URL)

            with open(SCHEMA_PATH, 'r') as f:
                sql_script = f.read()

            print("🏗️  Creating Database Schema...")
            with engine.connect() as conn:
                statements = sql_script.split(';')
                for statement in statements:
                    if statement.strip():
                        try:
                            conn.execute(text(statement))
                        except Exception as e:
                            print(f"⚠️  {e}")
                conn.commit()
            
            print("✅ Database schema created.")
            print("\n📦 Loading data from Master Catalog...")
            run_migration()
            return True
        except Exception as e:
            print(f"❌ Database initialization failed: {str(e)}")
            return False

if __name__ == "__main__":
    init_database()