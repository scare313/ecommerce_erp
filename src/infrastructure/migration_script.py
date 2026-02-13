import pandas as pd
from sqlalchemy import create_engine, text
import os

# --- PATH CONFIGURATION ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))

DB_PATH = os.path.join(PROJECT_ROOT, "data", "db", "ecommerce.db")
RAW_DIR = os.path.join(PROJECT_ROOT, "data", "raw_reports")
DB_URL = f"sqlite:///{DB_PATH}"

def get_engine():
    return create_engine(DB_URL)

def clean_column_names(df):
    """Standardize headers to Snake Case"""
    df.columns = [str(c).strip().lower().replace(' ', '_').replace('-', '_').replace('/', '_') for c in df.columns]
    return df

def find_excel_file():
    if not os.path.exists(RAW_DIR): return None
    files = os.listdir(RAW_DIR)
    excel_file = next((f for f in files if f.endswith('.xlsx') and not f.startswith('~$')), None)
    return os.path.join(RAW_DIR, excel_file) if excel_file else None

def load_sheet(excel_path, sheet_keyword):
    try:
        xls = pd.ExcelFile(excel_path)
        sheet_names = xls.sheet_names
        # Fuzzy match sheet name
        target_sheet = next((s for s in sheet_names if sheet_keyword.lower() in s.lower()), None)
        if target_sheet:
            print(f"  📖 Found Sheet: '{target_sheet}'")
            return pd.read_excel(excel_path, sheet_name=target_sheet)
    except Exception:
        pass
    print(f"  ⚠️  Sheet not found for: '{sheet_keyword}'")
    return None

def run_migration():
    engine = get_engine()
    excel_path = find_excel_file()
    
    if not excel_path:
        print("❌ No Excel (.xlsx) file found!")
        return

    print(f"📂 Processing: {excel_path}")

    # 1. CLEANUP
    print("🧹 Clearing Database...")
    with engine.connect() as conn:
        conn.execute(text("PRAGMA foreign_keys = OFF;"))
        for table in ['channel_listings', 'pack_master', 'product_master', 'inventory_master', 'pricing_rules', 'shipping_rules', 'config']:
            try:
                conn.execute(text(f"DELETE FROM {table};"))
            except:
                pass  # Table might not exist yet
        conn.execute(text("PRAGMA foreign_keys = ON;"))
        conn.commit()

    print("🚀 Importing Data...")
    
    try:
        # --- 1. CONFIG ---
        df = load_sheet(excel_path, "Config")
        if df is not None:
            df = clean_column_names(df)
            df.to_sql('config', engine, if_exists='append', index=False)
            print(f"  ✔ Config: {len(df)}")

        # --- 2. PRODUCT MASTER ---
        df = load_sheet(excel_path, "Product_Master")
        if df is not None:
            df = clean_column_names(df)
            rename_map = {
                'master_sku': 'sku', 'suppier_code': 'supplier_code', 
                'unit_box_cost': 'packaging_cost', 'mfg_cost': 'mfg_cost',
                'labeling_labor': 'labeling_labor', 'inbound_transport': 'inbound_transport',
                'total_unit_cogs': 'total_unit_cogs'
            }
            df = df.rename(columns=rename_map)
            df = df.dropna(subset=['sku'])
            df = df.drop_duplicates(subset=['sku'], keep='first')

            # Fill Defaults
            if 'name' not in df.columns: df['name'] = df['sku']
            df['name'] = df['name'].fillna(df['sku'])
            
            db_cols = ['sku', 'name', 'category', 'brand', 'lifecycle_status', 'supplier', 'supplier_code',
                       'mfg_cost', 'packaging_cost', 'labeling_labor', 'inbound_transport', 'total_unit_cogs',
                       'hsn', 'gst_rate', 'mrp']
            
            for col in db_cols: 
                if col not in df.columns: df[col] = None
                if col in ['mfg_cost', 'gst_rate']: df[col] = df[col].fillna(0.0)

            df[db_cols].to_sql('product_master', engine, if_exists='append', index=False)
            print(f"  ✔ Products: {len(df)}")
            
            # --- AUTO-CREATE INVENTORY RECORDS ---
            # For each product, create an inventory_master record
            inventory_records = df[['sku']].copy()
            inventory_records['godown_stock_packs'] = 0
            inventory_records['shop_stock_pieces'] = 0
            inventory_records['pack_multiplier'] = 1
            
            try:
                inventory_records.to_sql('inventory_master', engine, if_exists='append', index=False)
                print(f"  ✔ Inventory Records: {len(inventory_records)}")
            except Exception as e:
                print(f"  ⚠️  Could not create inventory records: {e}")

        # --- 3. PACK MASTER ---
        df = load_sheet(excel_path, "Pack_Master")
        if df is not None:
            df = clean_column_names(df)
            rename_map = {
                'pack_qty': 'quantity', 'packaging_cogs': 'packaging_cogs',
                'final_l': 'final_l_cm', 'final_w': 'final_w_cm',
                'final_h': 'final_h_cm', 'final_wt_kg': 'final_wt_kg'
            }
            df = df.rename(columns=rename_map)
            df = df.dropna(subset=['pack_sku'])
            df = df.drop_duplicates(subset=['pack_sku'], keep='first')

            db_cols = ['pack_sku', 'master_sku', 'quantity', 'packaging_cogs', 
                       'final_l_cm', 'final_w_cm', 'final_h_cm', 'final_wt_kg']
            for col in db_cols: 
                if col not in df.columns: df[col] = None

            df[db_cols].to_sql('pack_master', engine, if_exists='append', index=False)
            print(f"  ✔ Packs: {len(df)}")

        # --- 4. CHANNEL LISTINGS (WITH PRICE MERGE) ---
        df_list = load_sheet(excel_path, "Channel_Listings")
        # Load Profit Sheet to get prices
        df_prices = load_sheet(excel_path, "Profit") # Matches 'Pricing_&_Profit_Control'
        
        if df_list is not None:
            df_list = clean_column_names(df_list)
            rename_map = {'pack_sku': 'internal_sku', 'asin_fsn_id': 'channel_id'}
            df_list = df_list.rename(columns=rename_map)

            # --- MERGE PRICE LOGIC ---
            if df_prices is not None:
                df_prices = clean_column_names(df_prices)
                # Ensure we have the linking key and the price
                if 'channel_sku' in df_prices.columns and 'selling_price' in df_prices.columns:
                    # Clean up price table
                    price_lookup = df_prices[['channel_sku', 'selling_price']].dropna().drop_duplicates(subset=['channel_sku'])
                    # Merge into listings
                    df_list = pd.merge(df_list, price_lookup, on='channel_sku', how='left')
                    print(f"    ℹ️  Mapped Selling Prices from Profit Sheet")
            
            # If selling_price column doesn't exist (no merge happened), create it
            if 'selling_price' not in df_list.columns:
                df_list['selling_price'] = 0.0
            else:
                df_list['selling_price'] = df_list['selling_price'].fillna(0.0)

            # Deduplicate
            df_list = df_list.dropna(subset=['channel_sku', 'marketplace'])
            df_list = df_list.drop_duplicates(subset=['channel_sku', 'marketplace'], keep='first')
            
            db_cols = ['channel_sku', 'marketplace', 'internal_sku', 'listing_status', 
                       'selling_price', 'channel_id', 'listing_url', 'last_updated', 'comment']
            
            for col in db_cols: 
                if col not in df_list.columns: df_list[col] = None
                    
            df_list[db_cols].to_sql('channel_listings', engine, if_exists='append', index=False)
            print(f"  ✔ Listings: {len(df_list)}")

        # --- 5. RULES ---
        df = load_sheet(excel_path, "Pricing_Rules")
        if df is not None:
            df = clean_column_names(df)
            df.to_sql('pricing_rules', engine, if_exists='append', index=False)
            print(f"  ✔ Pricing Rules: {len(df)}")

        df = load_sheet(excel_path, "Shipping")
        if df is not None:
            df = clean_column_names(df)
            df.to_sql('shipping_rules', engine, if_exists='append', index=False)
            print(f"  ✔ Shipping Rules: {len(df)}")

        print("\n✨ Migration Complete!")
        
    except Exception as e:
        print(f"\n❌ ERROR: {e}")

if __name__ == "__main__":
    run_migration()