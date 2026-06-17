"""Data migration and import utilities for database initialization.

Loads initial data from Excel files and populates database tables in dependency order.
"""
import pandas as pd
from sqlalchemy import text
import os
from src.infrastructure.database import get_engine
from src.infrastructure.logger import get_logger, DatabaseException, DataValidationException

logger = get_logger(__name__)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))

RAW_DIR = os.path.join(PROJECT_ROOT, "data", "raw_reports")

def clean_column_names(df):
    """Standardize DataFrame column names to snake_case.
    
    Args:
        df: DataFrame with original column names
        
    Returns:
        DataFrame: With cleaned column names
        
    Raises:
        DataValidationException: If cleaning fails
    """
    try:
        df.columns = [str(c).strip().lower().replace(' ', '_').replace('-', '_').replace('/', '_') for c in df.columns]
        logger.debug(f"Cleaned column names: {list(df.columns)}")
        return df
    except Exception as e:
        logger.error(f"Error cleaning column names: {str(e)}", exc_info=True)
        raise DataValidationException(f"Column name cleaning failed: {str(e)}") from e

def find_excel_file():
    """
    Find Excel file in raw reports directory.
    
    Returns:
        str: Full path to Excel file, or None if not found
    """
    try:
        if not os.path.exists(RAW_DIR):
            logger.warning(f"Raw reports directory not found: {RAW_DIR}")
            return None
            
        files = os.listdir(RAW_DIR)
        excel_file = next((f for f in files if f.endswith('.xlsx') and not f.startswith('~$')), None)
        
        if excel_file:
            full_path = os.path.join(RAW_DIR, excel_file)
            logger.info(f"Found Excel file: {full_path}")
            return full_path
        else:
            logger.warning(f"No Excel file found in {RAW_DIR}")
            return None
    except Exception as e:
        logger.error(f"Error finding Excel file: {str(e)}", exc_info=True)
        return None

def load_sheet(excel_path, sheet_keyword):
    """
    Load data from a specific sheet in an Excel file.
    
    Args:
        excel_path: Path to Excel file
        sheet_keyword: Keyword to match sheet name (case-insensitive)
        
    Returns:
        DataFrame if found, None otherwise
    """
    try:
        xls = pd.ExcelFile(excel_path)
        sheet_names = xls.sheet_names
        
        # Fuzzy match sheet name
        target_sheet = next((s for s in sheet_names if sheet_keyword.lower() in s.lower()), None)
        
        if target_sheet:
            logger.debug(f"Loading sheet: '{target_sheet}' for keyword: '{sheet_keyword}'")
            df = pd.read_excel(excel_path, sheet_name=target_sheet)
            logger.info(f"✅ Loaded sheet '{target_sheet}': {len(df)} rows")
            return df
        else:
            logger.warning(f"Sheet not found for keyword: '{sheet_keyword}'")
            return None
    except Exception as e:
        logger.error(f"Error loading sheet '{sheet_keyword}': {str(e)}", exc_info=True)
        return None

def run_migration():
    """
    Execute database migration by reading data from Excel file and loading into database.
    
    Raises:
        DatabaseException: If database operations fail
        DataValidationException: If data validation fails
    """
    try:
        engine = get_engine()
        excel_path = find_excel_file()
        
        if not excel_path:
            error_msg = "No Excel (.xlsx) file found in data/raw_reports/"
            logger.error(error_msg)
            raise DataValidationException(error_msg)

        logger.info(f"Starting migration from: {excel_path}")

        # 1. CLEANUP
        logger.info("Clearing database tables...")
        try:
            with engine.connect() as conn:
                conn.execute(text("PRAGMA foreign_keys = OFF;"))
                
                tables = ['channel_listings', 'pack_master', 'product_master', 'inventory_master']
                
                for table in tables:
                    try:
                        conn.execute(text(f"DELETE FROM {table};"))
                        logger.debug(f"Cleared table: {table}")
                    except Exception as e:
                        logger.debug(f"Skipped clearing {table} (might not exist): {str(e)}")
                
                conn.execute(text("PRAGMA foreign_keys = ON;"))
                conn.commit()
                logger.info("✅ Database cleanup completed")
        except Exception as e:
            logger.error(f"Error clearing database: {str(e)}", exc_info=True)
            raise DatabaseException(f"Failed to clear database: {str(e)}") from e

        logger.info("Starting data import...")
        
        try:
            # --- 1. CONFIG ---
            logger.info("Importing CONFIG to Excel...")
            df = load_sheet(excel_path, "Config")
            if df is not None:
                df = clean_column_names(df)
                from src.infrastructure.config_rules import save_excel_sheet
                save_excel_sheet("Config", df)
                logger.info(f"✅ CONFIG imported to Excel: {len(df)} rows")
            else:
                logger.warning("CONFIG sheet not found, skipping")

            # --- 2. PRODUCT MASTER ---
            logger.info("Importing PRODUCT_MASTER table...")
            df = load_sheet(excel_path, "Product_Master")
            if df is not None:
                df = clean_column_names(df)
                rename_map = {
                    'master_sku': 'sku',
                    'unit_box_cost': 'packaging_cost', 'mfg_cost': 'mfg_cost',
                    'labeling_labor': 'labeling_labor', 'inbound_transport': 'inbound_transport',
                    'total_unit_cogs': 'total_unit_cogs'
                }
                df = df.rename(columns=rename_map)
                df = df.dropna(subset=['sku'])
                df = df.drop_duplicates(subset=['sku'], keep='first')

                # Fill Defaults
                if 'name' not in df.columns: 
                    df['name'] = df['sku']
                df['name'] = df['name'].fillna(df['sku'])
                
                db_cols = ['sku', 'name', 'category', 'brand', 'lifecycle_status', 'supplier', 'supplier_code',
                           'supplier_product_code',
                           'mfg_cost', 'packaging_cost', 'labeling_labor', 'inbound_transport', 'total_unit_cogs',
                           'hsn', 'gst_rate', 'mrp']

                for col in db_cols:
                    if col not in df.columns:
                        df[col] = None
                    if col in ['mfg_cost', 'gst_rate']:
                        df[col] = df[col].fillna(0.0)

                df[db_cols].to_sql('product_master', engine, if_exists='append', index=False)
                logger.info(f"✅ PRODUCT_MASTER imported: {len(df)} rows")
                
                # --- AUTO-CREATE INVENTORY RECORDS ---
                logger.info("Creating inventory master records for products...")
                inventory_records = df[['sku']].copy()
                inventory_records['godown_stock_packs'] = 0
                inventory_records['shop_stock_pieces'] = 0
                inventory_records['pack_multiplier'] = 1
                
                try:
                    inventory_records.to_sql('inventory_master', engine, if_exists='append', index=False)
                    logger.info(f"✅ INVENTORY_MASTER created: {len(inventory_records)} records")
                except Exception as e:
                    logger.warning(f"Could not create inventory records: {str(e)}")
            else:
                logger.warning("PRODUCT_MASTER sheet not found, skipping")

            # --- 3. PACK MASTER ---
            logger.info("Importing PACK_MASTER table...")
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
                    if col not in df.columns: 
                        df[col] = None

                df[db_cols].to_sql('pack_master', engine, if_exists='append', index=False)
                logger.info(f"✅ PACK_MASTER imported: {len(df)} rows")
            else:
                logger.warning("PACK_MASTER sheet not found, skipping")

            # --- 4. CHANNEL LISTINGS (WITH PRICE MERGE) ---
            logger.info("Importing CHANNEL_LISTINGS table...")
            df_list = load_sheet(excel_path, "Channel_Listings")
            
            if df_list is not None:
                df_list = clean_column_names(df_list)
                rename_map = {'pack_sku': 'internal_sku', 'asin_fsn_id': 'channel_id'}
                df_list = df_list.rename(columns=rename_map)

                # --- MERGE PRICE LOGIC ---
                logger.debug("Attempting to merge selling prices from Profit sheet...")
                df_prices = load_sheet(excel_path, "Profit")
                
                if df_prices is not None:
                    df_prices = clean_column_names(df_prices)
                    if 'channel_sku' in df_prices.columns and 'selling_price' in df_prices.columns:
                        price_lookup = df_prices[['channel_sku', 'selling_price']].dropna().drop_duplicates(subset=['channel_sku'])
                        df_list = pd.merge(df_list, price_lookup, on='channel_sku', how='left')
                        logger.info(f"✅ Merged {len(price_lookup)} selling prices from Profit sheet")
                
                # If selling_price column doesn't exist, create it
                if 'selling_price' not in df_list.columns:
                    df_list['selling_price'] = 0.0
                    logger.warning("Selling prices not found, defaulting to 0.0")
                else:
                    df_list['selling_price'] = df_list['selling_price'].fillna(0.0)

                # Deduplicate
                df_list = df_list.dropna(subset=['channel_sku', 'marketplace'])
                df_list = df_list.drop_duplicates(subset=['channel_sku', 'marketplace'], keep='first')
                
                db_cols = ['channel_sku', 'marketplace', 'internal_sku', 'listing_status', 
                           'selling_price', 'channel_id', 'listing_url', 'last_updated', 'comment']
                
                for col in db_cols: 
                    if col not in df_list.columns: 
                        df_list[col] = None
                        
                df_list[db_cols].to_sql('channel_listings', engine, if_exists='append', index=False)
                logger.info(f"✅ CHANNEL_LISTINGS imported: {len(df_list)} rows")
            else:
                logger.warning("CHANNEL_LISTINGS sheet not found, skipping")

            # --- 5. PRICING RULES ---
            logger.info("Importing PRICING_RULES to Excel...")
            df = load_sheet(excel_path, "Pricing_Rules")
            if df is not None:
                df = clean_column_names(df)
                from src.infrastructure.config_rules import save_excel_sheet
                save_excel_sheet("Pricing_Rules", df)
                logger.info(f"✅ PRICING_RULES imported to Excel: {len(df)} rows")
            else:
                logger.warning("PRICING_RULES sheet not found, skipping")

            # --- 6. SHIPPING RULES ---
            logger.info("Importing SHIPPING_RULES to Excel...")
            df = load_sheet(excel_path, "Shipping")
            if df is not None:
                df = clean_column_names(df)
                from src.infrastructure.config_rules import save_excel_sheet
                save_excel_sheet("Shipping_Rules", df)
                logger.info(f"✅ SHIPPING_RULES imported to Excel: {len(df)} rows")
            else:
                logger.warning("SHIPPING_RULES sheet not found, skipping")

            logger.info("✨ Migration completed successfully!")
            
        except Exception as e:
            logger.error(f"Error during data import: {str(e)}", exc_info=True)
            raise DatabaseException(f"Data import failed: {str(e)}") from e
        
    except DatabaseException as e:
        logger.error(f"Database migration failed: {str(e)}", exc_info=True)
        raise
    except DataValidationException as e:
        logger.error(f"Data validation during migration failed: {str(e)}", exc_info=True)
        raise
    except Exception as e:
        logger.critical(f"Unexpected error during migration: {str(e)}", exc_info=True)
        raise

if __name__ == "__main__":
    try:
        run_migration()
        logger.info("Migration script completed successfully")
    except Exception as e:
        logger.critical(f"Migration script failed: {str(e)}", exc_info=True)
        exit(1)
    
    try:
        # --- 1. CONFIG ---
        df = load_sheet(excel_path, "Config")
        if df is not None:
            df = clean_column_names(df)
            from src.infrastructure.config_rules import save_excel_sheet
            save_excel_sheet("Config", df)
            print(f"  ✔ Config (Excel): {len(df)}")

        # --- 2. PRODUCT MASTER ---
        df = load_sheet(excel_path, "Product_Master")
        if df is not None:
            df = clean_column_names(df)
            rename_map = {
                'master_sku': 'sku',
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
                       'supplier_product_code',
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
            from src.infrastructure.config_rules import save_excel_sheet
            save_excel_sheet("Pricing_Rules", df)
            print(f"  ✔ Pricing Rules (Excel): {len(df)}")

        df = load_sheet(excel_path, "Shipping")
        if df is not None:
            df = clean_column_names(df)
            from src.infrastructure.config_rules import save_excel_sheet
            save_excel_sheet("Shipping_Rules", df)
            print(f"  ✔ Shipping Rules (Excel): {len(df)}")

        print("\n✨ Migration Complete!")
        
    except Exception as e:
        print(f"\n❌ ERROR: {e}")

if __name__ == "__main__":
    run_migration()