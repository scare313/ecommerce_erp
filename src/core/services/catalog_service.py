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
        Categories that have fee rules, read from the SQL pricing_rules table —
        the same table the profit calculation uses (E9: single source of truth).

        Returns:
            list: Available product categories
        """
        fallback = ["Apparel", "Accessories", "Footwear", "Home", "Grocery",
                    "Beauty", "Toys", "Electronics", "Other"]
        try:
            with self.engine.connect() as conn:
                categories = [
                    r[0] for r in conn.execute(text(
                        "SELECT DISTINCT category_ref FROM pricing_rules "
                        "WHERE category_ref IS NOT NULL ORDER BY category_ref"
                    ))
                ]
            return categories or fallback
        except Exception as e:
            logger.error(f"Error fetching categories from pricing rules: {str(e)}", exc_info=True)
            return fallback

    def get_master_sku_list(self) -> list[str]:
        """All Master SKUs (product_master.sku), sorted — for grid dropdowns."""
        with self.engine.connect() as conn:
            return [r[0] for r in conn.execute(text("SELECT sku FROM product_master ORDER BY sku"))]

    # ------------------------------------------------------------------
    # Catalog link integrity
    #
    #   Product (Master SKU) <- Pack (Pack SKU, n units of one Master SKU)
    #                        <- Listing (Listing SKU on a marketplace, sells one Pack SKU)
    #
    # Every pack must point to an existing product and every listing to an
    # existing pack, otherwise COGS, profit and stock cannot be worked out.
    # ------------------------------------------------------------------

    @staticmethod
    def _sample(values, limit=10) -> str:
        values = sorted({str(v) for v in values})
        shown = ", ".join(values[:limit])
        return shown + (f" and {len(values) - limit} more" if len(values) > limit else "")

    def find_catalog_link_problems(self, conn, products=None, packs=None, listings=None) -> list[str]:
        """Check the catalog as it WOULD be if the given tables were replaced.

        Any table not passed is read from the database, so this answers "is
        it safe to save this edit / import this file?" before writing.

        Args:
            conn: open connection
            products: DataFrame with a 'sku' column, or None for the DB table
            packs: DataFrame with 'pack_sku', 'master_sku' (and optionally
                   'quantity'), or None for the DB table
            listings: DataFrame with 'channel_sku', 'marketplace', 'internal_sku'
                      (and optionally account_id / fulfilment_type), or None

        Returns:
            list[str]: one plain-English message per problem; empty if clean
        """
        def _blank(series):
            return series.isna() | (series.astype(str).str.strip() == "")

        if products is None:
            products = pd.read_sql(text("SELECT sku FROM product_master"), conn)
        if packs is None:
            packs = pd.read_sql(text("SELECT pack_sku, master_sku, quantity FROM pack_master"), conn)
        if listings is None:
            listings = pd.read_sql(text(
                "SELECT channel_sku, marketplace, internal_sku FROM channel_listings"
            ), conn)

        master_skus = set(products["sku"].dropna())
        pack_skus = set(packs["pack_sku"].dropna())
        problems = []

        # Packs -> products
        no_master = packs[_blank(packs["master_sku"])]
        if not no_master.empty:
            problems.append(
                f"Pack SKU {self._sample(no_master['pack_sku'])} has no Master SKU. "
                "Every pack must belong to a product."
            )
        bad_master = packs[~_blank(packs["master_sku"]) & ~packs["master_sku"].isin(master_skus)]
        if not bad_master.empty:
            problems.append(
                f"Master SKU {self._sample(bad_master['master_sku'])} (used by Pack SKU "
                f"{self._sample(bad_master['pack_sku'])}) does not exist. Add the product "
                "in the Products tab first, or pick an existing Master SKU."
            )
        if "quantity" in packs.columns:
            qty = pd.to_numeric(packs["quantity"], errors="coerce")
            bad_qty = packs[qty.isna() | (qty < 1) | (qty != qty.round())]
            if not bad_qty.empty:
                problems.append(
                    f"Pack SKU {self._sample(bad_qty['pack_sku'])}: units in pack must be a "
                    "whole number of 1 or more."
                )

        # Listings -> packs
        listing_label = listings["channel_sku"].astype(str) + " (" + listings["marketplace"].astype(str) + ")"
        no_pack = listings[_blank(listings["internal_sku"])]
        if not no_pack.empty:
            problems.append(
                f"Listing SKU {self._sample(listing_label[no_pack.index])} has no Pack SKU. "
                "Every listing must sell a pack."
            )
        bad_pack = listings[~_blank(listings["internal_sku"]) & ~listings["internal_sku"].isin(pack_skus)]
        if not bad_pack.empty:
            problems.append(
                f"Pack SKU {self._sample(bad_pack['internal_sku'])} (used by Listing SKU "
                f"{self._sample(listing_label[bad_pack.index])}) does not exist. Create the "
                "pack in the Packs tab first, or pick an existing Pack SKU."
            )

        # Listing fields that must match configured values
        known_markets = {r[0] for r in conn.execute(text("SELECT marketplace FROM config"))}
        if known_markets:
            bad_mkt = listings[~listings["marketplace"].isin(known_markets)]
            if not bad_mkt.empty:
                problems.append(
                    f"Marketplace {self._sample(bad_mkt['marketplace'])} is not set up "
                    f"(known: {', '.join(sorted(known_markets))})."
                )
        if "account_id" in listings.columns:
            known_accounts = {r[0] for r in conn.execute(text("SELECT account_id FROM seller_accounts"))}
            bad_acct = listings[listings["account_id"].notna() & ~listings["account_id"].isin(known_accounts)]
            if known_accounts and not bad_acct.empty:
                problems.append(f"Seller account {self._sample(bad_acct['account_id'])} does not exist.")
        if "fulfilment_type" in listings.columns:
            bad_ful = listings[listings["fulfilment_type"].notna()
                               & ~listings["fulfilment_type"].isin(["easy_ship", "fba"])]
            if not bad_ful.empty:
                problems.append(
                    f"Fulfilment {self._sample(bad_ful['fulfilment_type'])} is not valid "
                    "(use easy_ship or fba)."
                )

        # Stock rows must keep their product
        stock = {r[0] for r in conn.execute(text("SELECT sku FROM inventory_master"))}
        orphan_stock = stock - master_skus
        if orphan_stock:
            problems.append(
                f"Master SKU {self._sample(orphan_stock)} still has stock records, so it "
                "cannot be removed or renamed."
            )
        return problems

    def add_product(self, data: dict, create_single_pack: bool = False):
        """
        Add a new product to the product master.
        Automatically calculates total unit COGS.

        Args:
            data: Dictionary containing product fields
            create_single_pack: also create a 1-unit pack whose Pack SKU equals
                the Master SKU, so the product can be listed straight away
                (the same shape the Onboarding Wizard creates)

        Raises:
            DataValidationException: If the Master SKU is blank or already exists
            DatabaseException: If database insert fails
        """
        try:
            # Canonical SKU — uppercase, stripped of whitespace
            data['sku'] = str(data.get('sku') or '').strip().upper()
            if not data['sku']:
                raise DataValidationException("Master SKU is required.")

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

            with self.engine.begin() as conn:
                if self._exists(conn, 'product_master', 'sku', data['sku']):
                    raise DataValidationException(
                        f"Master SKU {data['sku']} already exists. Edit it in the table below."
                    )
                self._insert_new(conn, 'product_master', data)
                if create_single_pack and not self._exists(conn, 'pack_master', 'pack_sku', data['sku']):
                    self._insert_new(conn, 'pack_master', {
                        'pack_sku': data['sku'], 'master_sku': data['sku'],
                        'quantity': 1, 'packaging_cogs': 0,
                    })
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
            data['pack_sku'] = str(data.get('pack_sku') or '').strip().upper()
            data['master_sku'] = str(data.get('master_sku') or '').strip().upper()
            if not data['pack_sku']:
                raise DataValidationException("Pack SKU is required.")
            if not data['master_sku']:
                raise DataValidationException("Choose the Master SKU (product) this pack contains.")
            quantity = data.get('quantity', 1)
            if quantity is None or int(quantity) != quantity or quantity < 1:
                raise DataValidationException("Units in pack must be a whole number of 1 or more.")

            logger.info(f"Adding pack: {data.get('pack_sku', 'UNKNOWN')}")
            with self.engine.begin() as conn:
                if not self._exists(conn, 'product_master', 'sku', data['master_sku']):
                    raise DataValidationException(
                        f"Master SKU {data['master_sku']} does not exist. Add the product "
                        "in the Products tab first."
                    )
                if self._exists(conn, 'pack_master', 'pack_sku', data['pack_sku']):
                    raise DataValidationException(
                        f"Pack SKU {data['pack_sku']} already exists. Edit it in the table below."
                    )
                self._insert_new(conn, 'pack_master', data)
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
                raise DataValidationException("Listing SKU and marketplace are required.")
            if not data.get('internal_sku'):
                raise DataValidationException("Choose the Pack SKU this listing sells.")
            with self.engine.connect() as conn:
                if not self._exists(conn, 'pack_master', 'pack_sku', data['internal_sku']):
                    raise DataValidationException(
                        f"Pack SKU {data['internal_sku']} does not exist. Create the pack "
                        "in the Packs tab first."
                    )
            
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
        "product_master": {"key": ["sku"], "sku_cols": ["sku"],
                           "check_as": "products", "key_label": "Master SKU"},
        "pack_master": {"key": ["pack_sku"], "sku_cols": ["pack_sku", "master_sku"],
                        "check_as": "packs", "key_label": "Pack SKU"},
        "channel_listings": {"key": ["channel_sku", "marketplace"],
                             "sku_cols": ["channel_sku", "internal_sku"],
                             "check_as": "listings", "key_label": "Listing SKU / Marketplace"},
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
          - the edit would break a link between products, packs and listings
            (see find_catalog_link_problems). Problems that already existed
            before the edit do not block it — they are shown on the page.

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
                f"{int(blank_key.sum())} row(s) have no {spec['key_label']}. "
                "Fill it in or delete the row, then save again."
            )
        dupes = df[df.duplicated(subset=keys, keep=False)]
        if not dupes.empty:
            shown = sorted({" / ".join(str(v) for v in r) for r in dupes[keys].values})[:10]
            raise DataValidationException(
                f"Duplicate {spec['key_label']}: {', '.join(shown)}. Each must be unique."
            )
        if df.empty:
            raise DataValidationException(
                "Every row was removed. Refusing to empty the table — delete rows "
                "individually if that is really intended."
            )

        try:
            with self.engine.begin() as conn:
                table_cols = [r[1] for r in conn.execute(text(f"PRAGMA table_info({table})"))]
                before = set(self.find_catalog_link_problems(conn))
                after = self.find_catalog_link_problems(conn, **{spec["check_as"]: df})
                new_problems = [p for p in after if p not in before]
                if new_problems:
                    raise DataValidationException(" ".join(new_problems))

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

    @staticmethod
    def _exists(conn, table, column, value) -> bool:
        return conn.execute(
            text(f"SELECT 1 FROM {table} WHERE {column} = :v LIMIT 1"), {"v": value}
        ).first() is not None

    @staticmethod
    def _insert_new(conn, table, data):
        """Plain INSERT (never replaces an existing row) inside the caller's transaction."""
        cols = ", ".join(data.keys())
        params = ", ".join(f":{k}" for k in data.keys())
        conn.execute(text(f"INSERT INTO {table} ({cols}) VALUES ({params})"), data)

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