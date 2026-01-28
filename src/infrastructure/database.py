import os
from sqlalchemy import create_engine

# Robust path handling
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH = os.path.join(BASE_DIR, "data", "db", "ecommerce.db")
DB_URL = f"sqlite:///{DB_PATH}"

def get_engine():
    return create_engine(DB_URL)