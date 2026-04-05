import pandas as pd
from sqlalchemy import text
from src.infrastructure.database import get_engine

class CatalogService:
    def __init__(self):
        self.engine = get_engine()

    def get_product_dropdown(self):
        """Returns list of Master SKUs. Cleans up display if Name == SKU."""
        with self.engine.connect() as conn:
            result = conn.execute(text("SELECT sku, name FROM product_master ORDER BY sku"))
            options = []
            for row in result:
                # If name matches SKU (dummy data), just show SKU
                if row.name and row.name == row.sku:
                    options.append(row.sku)
                else:
                    options.append(f"{row.sku} | {row.name}")
            return options

    def get_pack_dropdown(self):
        """Returns list of Pack SKUs for dropdowns"""
        with self.engine.connect() as conn:
            result = conn.execute(text("SELECT pack_sku FROM pack_master ORDER BY pack_sku"))
            return [row.pack_sku for row in result]

    def get_category_dropdown(self):
        """Fetches unique categories from Pricing Rules to ensure consistency"""
        with self.engine.connect() as conn:
            result = conn.execute(text("SELECT DISTINCT category_ref FROM pricing_rules WHERE category_ref IS NOT NULL ORDER BY category_ref"))
            return [row.category_ref for row in result]

    def add_product(self, data: dict):
        # Calculate Total Unit COGS automatically
        mfg = data.get('mfg_cost', 0) or 0
        box = data.get('packaging_cost', 0) or 0
        labor = data.get('labeling_labor', 0) or 0
        transport = data.get('inbound_transport', 0) or 0
        data['total_unit_cogs'] = float(mfg) + float(box) + float(labor) + float(transport)
        
        self._insert('product_master', data)

    def add_pack(self, data: dict):
        self._insert('pack_master', data)

    def add_listing(self, data: dict):
        """Add or update a channel listing. Prevents duplicates via primary key constraint."""
        # Validate required fields
        if not data.get('channel_sku') or not data.get('marketplace'):
            raise ValueError("channel_sku and marketplace are required")
        
        # Warn if no price
        if not data.get('selling_price') or data.get('selling_price') <= 0:
            print(f"⚠️  Warning: Listing {data.get('channel_sku')} has no selling price. Profit calculations will be inaccurate.")
        
        try:
            self._insert('channel_listings', data)
        except Exception as e:
            if "UNIQUE constraint failed" in str(e) or "PRIMARY KEY" in str(e):
                raise ValueError(f"Listing already exists for {data['channel_sku']} on {data['marketplace']}. Use UPDATE to modify.")
            raise

    def _insert(self, table, data):
        """Generic insert helper"""
        cols = ', '.join(data.keys())
        params = ', '.join([':' + k for k in data.keys()])
        sql = text(f"INSERT OR REPLACE INTO {table} ({cols}) VALUES ({params})")
        
        with self.engine.connect() as conn:
            conn.execute(sql, data)
            conn.commit()