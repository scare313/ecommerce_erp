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
            # Canonical SKU — uppercase, stripped of whitespace
            if data.get('sku'):
                data['sku'] = str(data['sku']).strip().upper()

            logger.info(f"Adding product: {data.get('sku', 'UNKNOWN')}")

            # Calculate Total Unit COGS automatically
            mfg = data.get('mfg_cost', 0) or 0
            box = data.get('packaging_cost', 0) or 0
            labor = data.get('labeling_labor', 0) or 0
            transport = data.get('inbound_transport', 0) or 0
            data['total_unit_cogs'] = float(mfg) + float(box) + float(labor) + float(transport)
            
            logger.debug(f"Calculated COGS: {data['total_unit_cogs']}")

            # Validate supplier_code against supplier_master if provided.
            # NULL / blank supplier_code is permitted (legacy and wizard-imported products).
            # product_master.supplier freetext is NOT updated here — it is a historical field.
            supplier_code = str(data.get('supplier_code') or '').strip().upper()
            if supplier_code:
                with self.engine.connect() as conn:
                    row = conn.execute(
                        text(
                            "SELECT name FROM supplier_master "
                            "WHERE supplier_code = :code AND is_active = 1"
                        ),
                        {"code": supplier_code},
                    ).fetchone()
                if row is None:
                    raise DataValidationException(
                        f"Supplier '{supplier_code}' does not exist in Supplier Master."
                    )
                data['supplier_code'] = supplier_code  # persist normalised value

            # Normalise supplier_product_code: strip whitespace, store NULL when blank.
            # Case is preserved — supplier codes are case-sensitive in external systems.
            spc = str(data.get('supplier_product_code') or '').strip()
            data['supplier_product_code'] = spc if spc else None

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
            # Canonical SKUs — uppercase, stripped
            if data.get('pack_sku'):
                data['pack_sku'] = str(data['pack_sku']).strip().upper()
            if data.get('master_sku'):
                data['master_sku'] = str(data['master_sku']).strip().upper()

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
            # Canonical SKUs — uppercase, stripped
            if data.get('channel_sku'):
                data['channel_sku'] = str(data['channel_sku']).strip().upper()
            if data.get('internal_sku'):
                data['internal_sku'] = str(data['internal_sku']).strip().upper()

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

            # A listing is keyed on (channel_sku, marketplace) only, so the same
            # SKU string on the same marketplace under the other seller account
            # would overwrite this one. Refuse instead of replacing it.
            if data.get('account_id'):
                owner = self.get_listing_owner(data['channel_sku'], data['marketplace'])
                if owner and owner != data['account_id']:
                    raise DataValidationException(
                        f"{channel_sku} on {data['marketplace']} already belongs to "
                        f"{owner}. The same SKU cannot be listed under two accounts "
                        "on one marketplace."
                    )

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

    def get_listing_owner(self, channel_sku: str, marketplace: str) -> str | None:
        """Return the account_id that owns a listing, or None if it does not exist."""
        with self.engine.connect() as conn:
            return conn.execute(
                text(
                    "SELECT COALESCE(account_id, 'account1') FROM channel_listings "
                    "WHERE channel_sku = :c AND marketplace = :m"
                ),
                {"c": channel_sku, "m": marketplace},
            ).scalar()

    def delete_listings_for_marketplace(self, marketplace: str) -> int:
        """
        Delete all channel listings for a marketplace. Used when a marketplace
        is removed with cascade delete enabled.

        Args:
            marketplace: Marketplace name to delete listings for

        Returns:
            int: Number of listing rows deleted

        Raises:
            DataValidationException: If marketplace is empty
            DatabaseException: If the delete fails
        """
        try:
            if not marketplace:
                raise ValueError("marketplace is required")

            with self.engine.begin() as conn:
                result = conn.execute(
                    text("DELETE FROM channel_listings WHERE marketplace = :m"),
                    {"m": marketplace},
                )
                deleted = result.rowcount

            logger.info(f"Deleted {deleted} listing(s) for marketplace: {marketplace}")
            return deleted

        except ValueError as e:
            logger.error(f"Validation error during listing delete: {str(e)}", exc_info=True)
            raise DataValidationException(str(e)) from e
        except Exception as e:
            logger.error(f"Error deleting listings for {marketplace}: {str(e)}", exc_info=True)
            raise DatabaseException(f"Failed to delete listings for {marketplace}: {str(e)}") from e

    # Tables editable as a grid in Catalog: key columns, SKU columns to
    # normalise, and the (table, column) pairs that reference this table's key.
    _GRID_TABLES = {
        "product_master": {
            "key": ["sku"],
            "sku_cols": ["sku"],
            "referenced_by": [("pack_master", "master_sku"), ("inventory_master", "sku")],
        },
        "pack_master": {
            "key": ["pack_sku"],
            "sku_cols": ["pack_sku", "master_sku"],
            "referenced_by": [("channel_listings", "internal_sku")],
        },
        "channel_listings": {
            "key": ["channel_sku", "marketplace"],
            "sku_cols": ["channel_sku", "internal_sku"],
            "referenced_by": [],
        },
    }

    def save_table_edits(self, table: str, df: pd.DataFrame) -> int:
        """Save a whole edited grid back to its table, keeping the table's schema.

        Replaces the rows (DELETE + INSERT in one transaction) rather than the
        table itself. pandas to_sql(if_exists='replace') drops and recreates
        the table from the DataFrame, which silently removes the primary key,
        foreign keys and column defaults — so it must never be used here.

        Refuses to save, changing nothing, if:
          - a row has a blank key, or two rows share a key
          - every row was removed
          - a removed or renamed key is still used by another table
            (e.g. a product that packs or stock still point to)

        Args:
            table: one of product_master, pack_master, channel_listings
            df: the full edited grid

        Returns:
            int: number of rows saved

        Raises:
            DataValidationException: if the edit would break the data
            DatabaseException: if the write fails (nothing is changed)
        """
        if table not in self._GRID_TABLES:
            raise DataValidationException(f"Table {table} cannot be edited as a grid")
        spec = self._GRID_TABLES[table]
        keys = spec["key"]

        df = df.dropna(how="all").copy()
        for col in spec["sku_cols"]:
            if col in df.columns:
                df[col] = df[col].where(
                    df[col].isna(), df[col].astype(str).str.strip().str.upper()
                )

        blank_key = pd.Series(False, index=df.index)
        for col in keys:
            blank_key |= df[col].isna() | (df[col].astype(str).str.strip() == "")
        if blank_key.any():
            raise DataValidationException(
                f"{int(blank_key.sum())} row(s) have no {' / '.join(keys)}. "
                "Fill it in or delete the row, then save again."
            )
        dupes = df[df.duplicated(subset=keys, keep=False)]
        if not dupes.empty:
            shown = sorted({" / ".join(str(v) for v in r) for r in dupes[keys].values})[:10]
            raise DataValidationException(
                f"Duplicate {' / '.join(keys)}: {', '.join(shown)}. Each must be unique."
            )
        if df.empty:
            raise DataValidationException(
                "Every row was removed. Refusing to empty the table — delete rows "
                "individually if that is really intended."
            )

        try:
            with self.engine.begin() as conn:
                table_cols = [r[1] for r in conn.execute(text(f"PRAGMA table_info({table})"))]
                old_keys = pd.read_sql(text(f"SELECT {', '.join(keys)} FROM {table}"), conn)

                removed = set(map(tuple, old_keys[keys].values)) - set(map(tuple, df[keys].values))
                if removed and spec["referenced_by"]:
                    removed_ids = {k[0] for k in removed}  # referenced tables have single-column keys
                    for dep_table, dep_col in spec["referenced_by"]:
                        used = {
                            r[0] for r in conn.execute(
                                text(f"SELECT DISTINCT {dep_col} FROM {dep_table}")
                            )
                        } & removed_ids
                        if used:
                            raise DataValidationException(
                                f"Cannot remove or rename {', '.join(sorted(used)[:10])}: "
                                f"still used in {dep_table}. Change those rows first."
                            )

                save_df = df[[c for c in df.columns if c in table_cols]]
                # Rows are deleted and re-inserted, so foreign-key checks (when
                # enabled) must wait until COMMIT, when every row is back.
                conn.execute(text("PRAGMA defer_foreign_keys = ON"))
                conn.execute(text(f"DELETE FROM {table}"))
                save_df.to_sql(table, conn, if_exists="append", index=False)

            logger.info(f"Saved {len(save_df)} row(s) to {table} (schema preserved)")
            return len(save_df)
        except DataValidationException:
            raise
        except Exception as e:
            logger.error(f"Error saving edits to {table}: {str(e)}", exc_info=True)
            raise DatabaseException(f"Failed to save {table}: {str(e)}") from e

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