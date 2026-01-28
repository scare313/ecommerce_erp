import pandas as pd
import io
from sqlalchemy import text
from src.infrastructure.database import get_engine

class BulkService:
    def __init__(self):
        self.engine = get_engine()

    def download_full_catalog(self):
        """Generates a Multi-Tab Excel file with all master data."""
        output = io.BytesIO()
        writer = pd.ExcelWriter(output, engine='xlsxwriter')
        
        # 1. Products
        df_prod = pd.read_sql("SELECT * FROM product_master", self.engine)
        df_prod.to_excel(writer, sheet_name='Product_Master', index=False)
        
        # 2. Packs
        df_pack = pd.read_sql("SELECT * FROM pack_master", self.engine)
        df_pack.to_excel(writer, sheet_name='Pack_Master', index=False)
        
        # 3. Listings
        df_list = pd.read_sql("SELECT * FROM channel_listings", self.engine)
        df_list.to_excel(writer, sheet_name='Channel_Listings', index=False)
        
        # 4. Rules
        df_price = pd.read_sql("SELECT * FROM pricing_rules", self.engine)
        df_price.to_excel(writer, sheet_name='Pricing_Rules', index=False)
        
        df_ship = pd.read_sql("SELECT * FROM shipping_rules", self.engine)
        df_ship.to_excel(writer, sheet_name='Shipping_Rules', index=False)
        
        # 5. Config
        df_conf = pd.read_sql("SELECT * FROM config", self.engine)
        df_conf.to_excel(writer, sheet_name='Config', index=False)

        writer.close()
        return output.getvalue()

    def upload_full_catalog(self, file_buffer):
        """
        Reads a Multi-Tab Excel and updates tables in DEPENDENCY ORDER.
        1. Products (Parents)
        2. Packs (Children)
        3. Listings (Grandchildren)
        """
        try:
            xls = pd.ExcelFile(file_buffer)
            sheet_names = [s.lower() for s in xls.sheet_names]
            
            logs = []
            
            # Helper to map sheet name to DB table
            def update_table(sheet_keyword, table_name):
                # Find sheet
                target_sheet = next((s for s in xls.sheet_names if sheet_keyword.lower() in s.lower()), None)
                if not target_sheet:
                    logs.append(f"⚠️ Skipped {table_name}: Sheet not found.")
                    return

                # Read Data
                df = pd.read_excel(file_buffer, sheet_name=target_sheet)
                
                # Basic Cleaning
                df.columns = [str(c).strip().lower().replace(' ', '_') for c in df.columns]
                
                # Upsert to DB
                with self.engine.connect() as conn:
                    # We use 'replace' for MVP simplicity. 
                    # In a strict environment, we would use upsert to preserve relationships, 
                    # but 'replace' works here if the user uploads the FULL sheet.
                    df.to_sql(table_name, conn, if_exists='replace', index=False)
                
                logs.append(f"✅ Updated {table_name}: {len(df)} rows.")

            # EXECUTE IN ORDER
            update_table("Config", "config")
            update_table("Product_Master", "product_master")
            update_table("Pack_Master", "pack_master")
            update_table("Channel_Listings", "channel_listings")
            update_table("Pricing_Rules", "pricing_rules")
            update_table("Shipping_Rules", "shipping_rules")

            return True, "\n".join(logs)

        except Exception as e:
            return False, str(e)