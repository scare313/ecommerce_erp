import pandas as pd
from src.infrastructure.database import get_engine

class GapService:
    def __init__(self):
        self.engine = get_engine()

    def get_gap_matrix(self):
        # 1. Get all Sellable Packs (Rows)
        packs = pd.read_sql("SELECT pack_sku, master_sku FROM pack_master", self.engine)

        # 2. Get all Active Marketplaces (Columns)
        markets = pd.read_sql("SELECT marketplace FROM config", self.engine)
        if markets.empty:
            market_list = ["Amazon", "Flipkart", "Meesho"]
        else:
            market_list = markets['marketplace'].tolist()

        # 3. Create the "Ideal State" (Cartesian Product)
        packs['key'] = 1
        markets_df = pd.DataFrame({'marketplace': market_list, 'key': 1})
        ideal_catalog = pd.merge(packs, markets_df, on='key').drop('key', axis=1)

        # 4. Get Actual Listings
        listings = pd.read_sql("SELECT internal_sku, marketplace, listing_status FROM channel_listings", self.engine)

        # --- AGGREGATION (The Fix) ---
        # Instead of taking the raw rows, we count how many listings exist per Pack+Market
        grouped = listings.groupby(['internal_sku', 'marketplace']).agg(
            listing_count=('internal_sku', 'count')
        ).reset_index()

        # 5. Merge Ideal vs Actual
        merged = pd.merge(
            ideal_catalog, 
            grouped, 
            left_on=['pack_sku', 'marketplace'], 
            right_on=['internal_sku', 'marketplace'], 
            how='left'
        )
        
        # 6. Fill Gaps
        # If no match found, count is 0
        merged['listing_count'] = merged['listing_count'].fillna(0).astype(int)
        
        return merged