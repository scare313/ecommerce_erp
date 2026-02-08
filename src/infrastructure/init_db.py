# import os
# from sqlalchemy import create_engine, text

# # --- PATH CONFIGURATION ---
# SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))

# DB_PATH = os.path.join(PROJECT_ROOT, "data", "db", "ecommerce.db")
# SCHEMA_PATH = os.path.join(SCRIPT_DIR, "schema.sql")

# # Ensure DB directory exists
# os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

# DB_URL = f"sqlite:///{DB_PATH}"

# def init_database():
#     print(f"📂 Project Root: {PROJECT_ROOT}")
#     print(f"💾 Database Path: {DB_PATH}")

#     if not os.path.exists(SCHEMA_PATH):
#         print(f"❌ Error: schema.sql not found at {SCHEMA_PATH}")
#         return

#     engine = create_engine(DB_URL)

#     with open(SCHEMA_PATH, 'r') as f:
#         sql_script = f.read()

#     print("🏗️  Initializing Database Schema...")
#     with engine.connect() as conn:
#         # Split by ';' to run commands safely
#         statements = sql_script.split(';')
#         for statement in statements:
#             if statement.strip():
#                 try:
#                     conn.execute(text(statement))
#                 except Exception as e:
#                     print(f"⚠️ Warning: {e}")
        
#         conn.commit()
    
#     print("✅ Database ready!")

# if __name__ == "__main__":
#     init_database()

# import os
# from sqlalchemy import create_engine, text
# from database import get_engine

# engine = get_engine()

# sql_patch = """
# -- 1. Create the dedicated Inventory Master table
# CREATE TABLE IF NOT EXISTS inventory_master (
#     sku VARCHAR(50) PRIMARY KEY REFERENCES product_master(sku),
#     godown_stock_packs INT DEFAULT 0,
#     shop_stock_pieces INT DEFAULT 0,
#     last_updated DATETIME DEFAULT CURRENT_TIMESTAMP
# );

# -- 2. Create the Ledger table for audit trails
# CREATE TABLE IF NOT EXISTS stock_ledger (
#     id INTEGER PRIMARY KEY AUTOINCREMENT,
#     sku VARCHAR(50) REFERENCES product_master(sku),
#     transaction_type VARCHAR(50), 
#     location VARCHAR(20),        
#     qty_change INT,              
#     unit_type VARCHAR(20),       
#     multiplier INT,              
#     reason TEXT,
#     timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
# );

# -- 3. Pre-populate Inventory table with existing SKUs
# INSERT OR IGNORE INTO inventory_master (sku)
# SELECT sku FROM product_master;
# """

# def patch_db():
#     print("🩹 Patching database with Inventory tables...")
#     with engine.connect() as conn:
#         for statement in sql_patch.split(';'):
#             if statement.strip():
#                 conn.execute(text(statement))
#         conn.commit()
#     print("✅ Database updated successfully without data loss!")

# if __name__ == "__main__":
#     patch_db()

from sqlalchemy import text
from database import get_engine

engine = get_engine()

with engine.connect() as conn:
    # Drop the old confusing ledger if it exists
    conn.execute(text("DROP TABLE IF EXISTS stock_ledger;"))
    
    # Create the simplified Ledger table as requested
    conn.execute(text("""
        CREATE TABLE stock_ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sku VARCHAR(50),
            transaction_type VARCHAR(20), -- 'ADD' or 'REMOVE'
            packs INT,
            multiplier INT,               
            total_pieces_affected INT,    
            reason TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        );
    """))
    conn.commit()
print("✅ stock_ledger table recreated with correct columns!")