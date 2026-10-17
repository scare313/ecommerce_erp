import pandas as pd
import io
from sqlalchemy import text
from src.infrastructure.database import get_engine
from src.infrastructure.logger import get_logger, DatabaseException, ImportException

logger = get_logger(__name__)

class BulkService:
    """Service for bulk data operations including export and import."""
    
    def __init__(self):
        """Initialize bulk service with database engine."""
        try:
            self.engine = get_engine()
            logger.debug("BulkService initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize BulkService: {str(e)}", exc_info=True)
            raise

    def download_full_catalog(self):
        """
        Generates a Multi-Tab Excel file with all master data.
        
        Returns:
            bytes: Excel file content in binary format
            
        Raises:
            DatabaseException: If database queries fail
            ImportException: If Excel generation fails
        """
        try:
            logger.info("Starting full catalog export to Excel...")
            output = io.BytesIO()
            
            try:
                writer = pd.ExcelWriter(output, engine='xlsxwriter')
                
                # 1. Products
                logger.debug("Exporting products...")
                df_prod = pd.read_sql("SELECT * FROM product_master", self.engine)
                df_prod.to_excel(writer, sheet_name='Product_Master', index=False)
                logger.info(f"Exported {len(df_prod)} products")
                
                # 2. Packs
                logger.debug("Exporting packs...")
                df_pack = pd.read_sql("SELECT * FROM pack_master", self.engine)
                df_pack.to_excel(writer, sheet_name='Pack_Master', index=False)
                logger.info(f"Exported {len(df_pack)} packs")
                
                # 3. Listings
                logger.debug("Exporting channel listings...")
                df_list = pd.read_sql("SELECT * FROM channel_listings", self.engine)
                df_list.to_excel(writer, sheet_name='Channel_Listings', index=False)
                logger.info(f"Exported {len(df_list)} listings")
                
                # 4. Pricing Rules
                logger.debug("Exporting pricing rules...")
                from src.infrastructure.config_rules import load_excel_sheet
                df_price = load_excel_sheet("Pricing_Rules")
                df_price.to_excel(writer, sheet_name='Pricing_Rules', index=False)
                logger.info(f"Exported {len(df_price)} pricing rules")
                
                # 5. Shipping Rules
                logger.debug("Exporting shipping rules...")
                df_ship = load_excel_sheet("Shipping_Rules")
                df_ship.to_excel(writer, sheet_name='Shipping_Rules', index=False)
                logger.info(f"Exported {len(df_ship)} shipping rules")
                
                # 6. Config
                logger.debug("Exporting config...")
                df_conf = load_excel_sheet("Config")
                df_conf.to_excel(writer, sheet_name='Config', index=False)
                logger.info(f"Exported {len(df_conf)} config entries")

                writer.close()
                logger.info("✅ Full catalog export completed successfully")
                return output.getvalue()
                
            except Exception as e:
                logger.error(f"Error during Excel generation: {str(e)}", exc_info=True)
                raise ImportException(f"Failed to generate Excel export: {str(e)}") from e
                
        except ImportException:
            raise
        except Exception as e:
            logger.error(f"Unexpected error during catalog export: {str(e)}", exc_info=True)
            raise

    # SKU columns stored in canonical uppercase (E11)
    _SKU_COLS = {
        "product_master": ["sku"],
        "pack_master": ["pack_sku", "master_sku"],
        "channel_listings": ["channel_sku", "internal_sku"],
    }

    @classmethod
    def _read_catalog_sheet(cls, xls, file_buffer, sheet_keyword, table_name):
        """Read one catalog sheet with the same cleaning the import applies.

        Returns None when the sheet is absent or empty.
        """
        target = next((s for s in xls.sheet_names if sheet_keyword.lower() in s.lower()), None)
        if not target:
            return None
        df = pd.read_excel(file_buffer, sheet_name=target)
        df.columns = [str(c).strip().lower().replace(' ', '_') for c in df.columns]
        if df.empty:
            return None
        for col in cls._SKU_COLS.get(table_name, []):
            if col in df.columns:
                df[col] = df[col].where(df[col].isna(), df[col].astype(str).str.strip().str.upper())
        return df

    def _check_catalog_links(self, xls, file_buffer):
        """Problems the catalog would have after this import; empty list if clean."""
        from src.core.services.catalog_service import CatalogService

        products = self._read_catalog_sheet(xls, file_buffer, "Product_Master", "product_master")
        packs = self._read_catalog_sheet(xls, file_buffer, "Pack_Master", "pack_master")
        listings = self._read_catalog_sheet(xls, file_buffer, "Channel_Listings", "channel_listings")

        required = {
            "Product_Master": (products, ["sku"]),
            "Pack_Master": (packs, ["pack_sku", "master_sku"]),
            "Channel_Listings": (listings, ["channel_sku", "marketplace", "internal_sku"]),
        }
        missing = [
            f"Sheet {sheet} is missing column(s): {', '.join(c for c in cols if c not in df.columns)}"
            for sheet, (df, cols) in required.items()
            if df is not None and any(c not in df.columns for c in cols)
        ]
        if missing:
            return missing

        with self.engine.connect() as conn:
            return CatalogService().find_catalog_link_problems(
                conn, products=products, packs=packs, listings=listings
            )

    def upload_full_catalog(self, file_buffer):
        """
        Reads a Multi-Tab Excel and updates tables in DEPENDENCY ORDER.
        Uses CLEAR + INSERT (not replace) to preserve referential integrity.
        1. Products (Parents)
        2. Packs (Children)
        3. Listings (Grandchildren)
        
        WARNING: This function clears each table before inserting new data.
        Only use if uploading a COMPLETE export from download_full_catalog()
        
        Args:
            file_buffer: File-like object containing Excel data
            
        Returns:
            tuple: (success: bool, message: str with details)
        """
        try:
            logger.info("Starting full catalog import from Excel...")
            xls = pd.ExcelFile(file_buffer)
            sheet_names = [s.lower() for s in xls.sheet_names]
            
            logger.debug(f"Available sheets: {sheet_names}")
            logs = []

            # Check product -> pack -> listing links across the whole file
            # BEFORE writing anything. Sheets are replaced one at a time, so a
            # broken link found halfway would otherwise leave a half-imported
            # catalog. A missing sheet means "keep the current table".
            problems = self._check_catalog_links(xls, file_buffer)
            if problems:
                msg = "Nothing was imported. Fix these in the file and upload again:\n- " + "\n- ".join(problems)
                logger.warning(msg)
                return False, msg

            # Helper to map sheet name to DB table
            def update_table(sheet_keyword, table_name):
                try:
                    logger.debug(f"Processing table: {table_name}")
                    
                    # Find sheet
                    target_sheet = next((s for s in xls.sheet_names if sheet_keyword.lower() in s.lower()), None)
                    if not target_sheet:
                        msg = f"Skipped {table_name}: Sheet not found."
                        logger.warning(msg)
                        logs.append(f"⚠️ {msg}")
                        return

                    # Read Data
                    logger.debug(f"Reading data from sheet: {target_sheet}")
                    df = pd.read_excel(file_buffer, sheet_name=target_sheet)
                    
                    # Basic Cleaning
                    df.columns = [str(c).strip().lower().replace(' ', '_') for c in df.columns]
                    
                    # Validate not empty
                    if df.empty:
                        msg = f"⚠️ Skipped {table_name}: No data in sheet."
                        logger.warning(msg)
                        logs.append(msg)
                        return
                    
                    # Normalize SKU columns to canonical uppercase before insert (E11)
                    for col in self._SKU_COLS.get(table_name, []):
                        if col in df.columns:
                            df[col] = df[col].astype(str).str.strip().str.upper()

                    if table_name in ["config", "pricing_rules", "shipping_rules"]:
                        try:
                            from src.infrastructure.config_rules import save_excel_sheet
                            sheet_map = {
                                "config": "Config",
                                "pricing_rules": "Pricing_Rules",
                                "shipping_rules": "Shipping_Rules"
                            }
                            save_excel_sheet(sheet_map[table_name], df)
                            msg = f"✅ Updated Excel sheet {sheet_map[table_name]}: {len(df)} rows."
                            logger.info(msg)
                            logs.append(msg)
                            return
                        except Exception as e:
                            msg = f"❌ Error updating Excel sheet for {table_name}: {str(e)}"
                            logger.error(msg, exc_info=True)
                            logs.append(msg)
                            return

                    try:
                        with self.engine.connect() as conn:
                            # SAFE APPROACH: Clear then insert
                            logger.debug(f"Clearing table: {table_name}")
                            conn.execute(text(f"DELETE FROM {table_name}"))
                            
                            logger.debug(f"Inserting {len(df)} rows into {table_name}")
                            df.to_sql(table_name, conn, if_exists='append', index=False)
                            conn.commit()
                        
                        msg = f"✅ Updated {table_name}: {len(df)} rows."
                        logger.info(msg)
                        logs.append(msg)
                    except Exception as e:
                        msg = f"❌ Error updating {table_name}: {str(e)}"
                        logger.error(msg, exc_info=True)
                        logs.append(msg)
                        
                except Exception as e:
                    msg = f"❌ Unexpected error processing {table_name}: {str(e)}"
                    logger.error(msg, exc_info=True)
                    logs.append(msg)

            # EXECUTE IN DEPENDENCY ORDER
            logger.info("Starting table updates in dependency order...")
            update_table("Config", "config")
            update_table("Product_Master", "product_master")
            update_table("Pack_Master", "pack_master")
            update_table("Channel_Listings", "channel_listings")
            update_table("Pricing_Rules", "pricing_rules")
            update_table("Shipping_Rules", "shipping_rules")

            success_msg = "\n".join(logs)
            logger.info(f"✅ Import completed\n{success_msg}")
            return True, success_msg

        except Exception as e:
            error_msg = f"Upload failed: {str(e)}"
            logger.error(error_msg, exc_info=True)
            return False, error_msg