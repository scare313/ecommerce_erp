import pandas as pd
from sqlalchemy import text
from src.infrastructure.database import get_engine
from src.infrastructure.logger import get_logger, DatabaseException, DataValidationException

logger = get_logger(__name__)

class CatalogService:
    """Service for managing product catalog, packs, and listings."""
    
    def __init__(self):
        """Initialize catalog service with database engine."""
        try:
            self.engine = get_engine()
            logger.debug("CatalogService initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize CatalogService: {str(e)}", exc_info=True)
            raise

    def get_product_dropdown(self):
        """
        Returns list of Master SKUs for dropdown selection.
        Cleans up display if Name == SKU.
        
        Returns:
            list: Product options formatted as "SKU" or "SKU | Name"
            
        Raises:
            DatabaseException: If database query fails
        """
        try:
            logger.debug("Fetching product dropdown options...")
            with self.engine.connect() as conn:
                result = conn.execute(text("SELECT sku, name FROM product_master ORDER BY sku"))
                options = []
                for row in result:
                    if row.name and row.name == row.sku:
                        options.append(row.sku)
                    else:
                        options.append(f"{row.sku} | {row.name}")
                logger.debug(f"Retrieved {len(options)} product options")
                return options
        except Exception as e:
            logger.error(f"Error fetching product dropdown: {str(e)}", exc_info=True)
            raise DatabaseException(f"Failed to fetch product list: {str(e)}") from e

    def get_pack_dropdown(self):
        """
        Returns list of Pack SKUs for dropdowns.
        
        Returns:
            list: Pack SKU options
            
        Raises:
            DatabaseException: If database query fails
        """
        try:
            logger.debug("Fetching pack dropdown options...")
            with self.engine.connect() as conn:
                result = conn.execute(text("SELECT pack_sku FROM pack_master ORDER BY pack_sku"))
                packs = [row.pack_sku for row in result]
                logger.debug(f"Retrieved {len(packs)} pack options")
                return packs
        except Exception as e:
            logger.error(f"Error fetching pack dropdown: {str(e)}", exc_info=True)
            raise DatabaseException(f"Failed to fetch pack list: {str(e)}") from e

    def get_category_dropdown(self):
        """
        Fetches unique categories from Excel Pricing Rules to ensure consistency.
        
        Returns:
            list: Available product categories
        """
        try:
            logger.debug("Fetching category options from Excel pricing rules...")
            from src.infrastructure.config_rules import load_excel_sheet
            df = load_excel_sheet("Pricing_Rules")
            if not df.empty and 'category_ref' in df.columns:
                categories = sorted(df['category_ref'].dropna().unique().tolist())
            else:
                categories = ["Apparel", "Accessories", "Footwear", "Home", "Grocery", "Beauty", "Toys", "Electronics", "Other"]
            logger.debug(f"Retrieved {len(categories)} categories from Excel rules")
            return categories
        except Exception as e:
            logger.error(f"Error fetching categories from Excel: {str(e)}", exc_info=True)
            return ["Apparel", "Accessories", "Footwear", "Home", "Grocery", "Beauty", "Toys", "Electronics", "Other"]

    def add_product(self, data: dict):
        """
        Add a new product to the product master.
        Automatically calculates total unit COGS.
        
        Args:
            data: Dictionary containing product fields
            
        Raises:
            DataValidationException: If required fields are missing
            DatabaseException: If database insert fails
        """
        try:
            logger.info(f"Adding product: {data.get('sku', 'UNKNOWN')}")
            
            # Calculate Total Unit COGS automatically
            mfg = data.get('mfg_cost', 0) or 0
            box = data.get('packaging_cost', 0) or 0
            labor = data.get('labeling_labor', 0) or 0
            transport = data.get('inbound_transport', 0) or 0
            data['total_unit_cogs'] = float(mfg) + float(box) + float(labor) + float(transport)
            
            logger.debug(f"Calculated COGS: {data['total_unit_cogs']}")
            self._insert('product_master', data)
            logger.info(f"✅ Product created: {data.get('sku')}")
            
        except DataValidationException:
            raise
        except DatabaseException:
            raise
        except Exception as e:
            logger.error(f"Error adding product {data.get('sku', 'UNKNOWN')}: {str(e)}", exc_info=True)
            raise

    def add_pack(self, data: dict):
        """
        Add a new pack to the pack master.
        
        Args:
            data: Dictionary containing pack fields
            
        Raises:
            DataValidationException: If required fields are missing
            DatabaseException: If database insert fails
        """
        try:
            logger.info(f"Adding pack: {data.get('pack_sku', 'UNKNOWN')}")
            self._insert('pack_master', data)
            logger.info(f"✅ Pack created: {data.get('pack_sku')}")
        except (DataValidationException, DatabaseException):
            raise
        except Exception as e:
            logger.error(f"Error adding pack {data.get('pack_sku', 'UNKNOWN')}: {str(e)}", exc_info=True)
            raise

    def add_listing(self, data: dict):
        """
        Add or update a channel listing. Prevents duplicates via primary key constraint.
        
        Args:
            data: Dictionary containing listing fields
            
        Raises:
            DataValidationException: If required fields missing or duplicate detected
            DatabaseException: If database insert fails
        """
        try:
            channel_sku = data.get('channel_sku', 'UNKNOWN')
            logger.info(f"Adding listing: {channel_sku}")
            
            # Validate required fields
            if not data.get('channel_sku') or not data.get('marketplace'):
                error_msg = "channel_sku and marketplace are required"
                logger.error(f"Validation error: {error_msg}")
                raise DataValidationException(error_msg)
            
            # Warn if no price
            if not data.get('selling_price') or data.get('selling_price') <= 0:
                logger.warning(f"Listing {channel_sku} has no selling price. Profit calculations may be inaccurate.")
            
            try:
                self._insert('channel_listings', data)
                logger.info(f"✅ Listing created: {channel_sku} on {data.get('marketplace')}")
            except Exception as e:
                if "UNIQUE constraint failed" in str(e) or "PRIMARY KEY" in str(e):
                    error_msg = f"Listing already exists for {channel_sku} on {data['marketplace']}"
                    logger.warning(error_msg)
                    raise DataValidationException(f"{error_msg}. Use UPDATE to modify.") from e
                raise
                
        except (DataValidationException, DatabaseException):
            raise
        except Exception as e:
            logger.error(f"Error adding listing {data.get('channel_sku', 'UNKNOWN')}: {str(e)}", exc_info=True)
            raise

    def _insert(self, table, data):
        """
        Generic insert helper for all catalog operations.
        
        Args:
            table: Table name
            data: Dictionary of column-value pairs
            
        Raises:
            ValueError: If data is empty
            DatabaseException: If insert fails
        """
        try:
            if not data:
                raise ValueError("Cannot insert empty data")
            
            cols = ', '.join(data.keys())
            params = ', '.join([':' + k for k in data.keys()])
            sql = text(f"INSERT OR REPLACE INTO {table} ({cols}) VALUES ({params})")
            
            logger.debug(f"Executing INSERT OR REPLACE on {table}")
            
            with self.engine.connect() as conn:
                conn.execute(sql, data)
                conn.commit()
                logger.debug(f"Successfully inserted data into {table}")
                
        except ValueError as e:
            logger.error(f"Validation error during insert: {str(e)}", exc_info=True)
            raise DataValidationException(str(e)) from e
        except Exception as e:
            logger.error(f"Error inserting into {table}: {str(e)}", exc_info=True)
            raise DatabaseException(f"Database insert failed for {table}: {str(e)}") from e