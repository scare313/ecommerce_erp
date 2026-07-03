"""Inventory management and purchase planning service.

Handles inventory tracking, purchase planning based on marketplace demand,
and manages procurement calculations across suppliers.
"""
import pandas as pd
from src.infrastructure.database import get_engine
from src.infrastructure.parsers import parse_amazon_sales, parse_flipkart_sales, parse_meesho_sales, parse_stock_file
from src.infrastructure.logger import get_logger, DatabaseException, DataValidationException, ServiceException
from sqlalchemy import text

logger = get_logger(__name__)

class InventoryService:
    """Service for managing inventory and purchase planning across all locations."""
    
    def __init__(self):
        """Initialize inventory service with database engine."""
        try:
            self.engine = get_engine()
            logger.debug("InventoryService initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize InventoryService: {str(e)}", exc_info=True)
            raise

    @staticmethod
    def _godown_balance(conn, sku):
        """Return the current godown_stock_packs for a SKU on an open connection.

        Used to capture stock_ledger.running_balance immediately after a write,
        inside the same transaction so the value reflects the just-applied change.
        """
        row = conn.execute(
            text("SELECT godown_stock_packs FROM inventory_master WHERE sku = :sku"),
            {"sku": sku},
        ).fetchone()
        return int(row[0]) if (row and row[0] is not None) else 0

    def get_master_mapping(self):
        """Fetch channel listing to base product hierarchy mapping.
        
        Returns:
            DataFrame: Complete mapping with columns:
                - marketplace, channel_sku, pack_sku, pack_qty
                - base_sku, product_name, supplier, category
        """
        query = """
        SELECT
            cl.marketplace,
            cl.channel_sku,
            pm.pack_sku,
            pm.quantity as pack_qty,
            pm.master_sku as base_sku,
            p.name as product_name,
            p.supplier,
            p.supplier_code,
            p.supplier_product_code,
            p.category
        FROM channel_listings cl
        JOIN pack_master pm ON cl.internal_sku = pm.pack_sku
        JOIN product_master p ON pm.master_sku = p.sku
        """
        return pd.read_sql(query, self.engine)

    def generate_purchase_plan(self, files_dict, params):
        """Calculate procurement needs based on marketplace sales demand.
        
        Aggregates sales from all marketplaces, factors in inventory levels and lead times,
        and calculates required purchase quantities per supplier.
        
        Args:
            files_dict: Dictionary of uploaded files
                - 'amazon': Amazon sales report file
                - 'flipkart': Flipkart sales report file  
                - 'meesho': Meesho sales report file
                - 'stock': Current stock file
            params: Planning parameters dict
                - 'sales_days': Historical days to analyze
                - 'lead_time': Supplier lead time in days
                - 'safety_stock': Safety stock multiplier
                - 'min_order_qty': Minimum order quantity per supplier
                
        Returns:
            tuple: (success_bool, report_message)
        """
        # 1. Parse Sales Files
        sales_dfs = []

        # Dynamic parser dispatch
        parser_map = {
            'amazon': parse_amazon_sales,
            'flipkart': parse_flipkart_sales,
            'meesho': parse_meesho_sales,
            # Future: 'myntra': parse_myntra_sales,
        }
        
        for mkt_key, file_obj in files_dict.items():
            if not file_obj:
                continue
            parser = parser_map.get(mkt_key.lower())
            if not parser:
                logger.warning(f"No parser available for marketplace: {mkt_key}")
                continue
            df, err = parser(file_obj)
            if df is not None:
                sales_dfs.append(df)
            elif err:
                logger.warning(f"{mkt_key} parse error: {err}")

        if not sales_dfs:
            return None, "No valid sales files provided."

        # Combine all sales
        total_sales = pd.concat(sales_dfs, ignore_index=True)
        
        # 2. Get DB Mapping
        mapping_df = self.get_master_mapping()
        
        # Normalize for merging (UPPERCASE)
        total_sales['sku'] = total_sales['sku'].str.upper().str.strip()
        mapping_df['channel_sku'] = mapping_df['channel_sku'].str.upper().str.strip()
        
        # 3. Merge Sales with Mapping
        # We merge on 'channel_sku' AND 'marketplace' for precision
        merged = pd.merge(
            total_sales,
            mapping_df,
            left_on=['sku', 'marketplace'],
            right_on=['channel_sku', 'marketplace'],
            how='left'
        )
        
        # Identify Orphans (Sales with no DB mapping)
        orphans = merged[merged['base_sku'].isna()][['sku', 'marketplace', 'qty']].groupby(['sku', 'marketplace']).sum().reset_index()
        
        # --- THE FIX IS HERE ---
        # Filter valid rows AND create a copy to avoid SettingWithCopyWarning
        valid_sales = merged.dropna(subset=['base_sku']).copy()
        
        # 4. Calculate BASE UNIT Demand
        # If I sell 2 packs of 5, I sold 10 Base Units.
        valid_sales['base_units_sold'] = valid_sales['qty'] * valid_sales['pack_qty']
        
        # 5. Group by BASE PRODUCT (Supplier View)
        plan = valid_sales.groupby(
            ['base_sku', 'product_name', 'supplier', 'supplier_code',
             'supplier_product_code', 'category'],
            dropna=False,
        ).agg(total_base_sold=('base_units_sold', 'sum')).reset_index()

        # 5.5 Merge supplier lead times and names from supplier_master
        with self.engine.connect() as conn:
            lead_time_df = pd.read_sql(
                text("SELECT supplier_code, lead_time_days FROM supplier_master WHERE is_active = 1"),
                conn,
            )
            names_df = pd.read_sql(
                text("SELECT supplier_code, name AS supplier_name FROM supplier_master WHERE is_active = 1"),
                conn,
            )

        plan = pd.merge(plan, lead_time_df, on='supplier_code', how='left')
        plan['lead_time_source'] = plan['lead_time_days'].apply(
            lambda x: "Supplier Master" if pd.notna(x) else "Default (sidebar)"
        )
        plan = pd.merge(plan, names_df, on='supplier_code', how='left')
        plan['supplier_name'] = plan['supplier_name'].fillna(plan['supplier'])

        # 6. Inventory Math
        sales_days = params.get('sales_days', 30)
        lead_time = params.get('lead_time', 10)
        safety_days = params.get('safety_stock', 7)
        buffer_days = params.get('purchase_period', 15)

        plan['lead_time_days'] = plan['lead_time_days'].fillna(lead_time)

        # Average Daily Sales (ADS)
        plan['ads'] = plan['total_base_sold'] / sales_days

        # Requirements
        plan['lead_time_demand'] = plan['ads'] * plan['lead_time_days']
        plan['safety_stock'] = plan['ads'] * safety_days
        plan['cycle_stock'] = plan['ads'] * buffer_days
        
        plan['gross_requirement'] = plan['lead_time_demand'] + plan['safety_stock'] + plan['cycle_stock']
        
        # 7. Subtract Current Stock
        # Priority: uploaded stock file > live inventory_master > 0 (last resort)
        if files_dict.get('stock'):
            stock_df, stock_err = parse_stock_file(files_dict['stock'])
            if stock_df is not None:
                logger.info("Using uploaded stock file for purchase planning")
                plan = pd.merge(plan, stock_df, left_on='base_sku', right_on='sku', how='left')
                plan['stock_qty'] = plan['stock_qty'].fillna(0)
            else:
                logger.warning(
                    f"Stock file parse failed ({stock_err}); "
                    "falling back to live inventory_master"
                )
                live_stock = self.get_stock_for_planning()
                plan = pd.merge(plan, live_stock, left_on='base_sku', right_on='sku', how='left')
                plan['stock_qty'] = plan['stock_qty'].fillna(0)
        else:
            logger.info("No stock file uploaded — using live inventory_master for purchase planning")
            live_stock = self.get_stock_for_planning()
            plan = pd.merge(plan, live_stock, left_on='base_sku', right_on='sku', how='left')
            plan['stock_qty'] = plan['stock_qty'].fillna(0)
            
        # 8. Net Requirement
        plan['to_purchase'] = (plan['gross_requirement'] - plan['stock_qty']).clip(lower=0)
        
        # Rounding
        plan['to_purchase'] = plan['to_purchase'].round().astype(int)
        plan['ads'] = plan['ads'].round(2)
        
        return plan, orphans
    
    def get_inventory_status(self):
        """Joins Product Master with the dedicated Inventory table.

        Uses inventory_master.pack_multiplier as the authoritative multiplier
        for each SKU. pack_master is excluded from this query: it has a
        one-to-many relationship with product_master, which previously caused
        a fan-out that made GROUP BY return an arbitrary multiplier value for
        products with more than one pack configuration.
        """
        query = """
        SELECT
            p.sku,
            p.category,
            COALESCE(i.godown_stock_packs, 0)  AS godown_stock_packs,
            COALESCE(i.shop_stock_pieces, 0)   AS shop_stock_pieces,
            COALESCE(i.pack_multiplier, 1)     AS multiplier
        FROM product_master p
        LEFT JOIN inventory_master i ON p.sku = i.sku
        """
        df = pd.read_sql(query, self.engine)
        df['multiplier'] = df['multiplier'].fillna(1)
        # Total Pieces = (Godown Packs * Multiplier) + Shop Pieces
        df['total_pieces'] = (df['godown_stock_packs'] * df['multiplier']) + df['shop_stock_pieces']
        return df

    def add_stock(self, sku, qty, location, unit_type, reason,
                  reason_code="ADJUSTMENT", updated_by="system"):
        """Updates inventory_master and logs to ledger.

        Args:
            sku: Product SKU
            qty: Quantity to add (packs for GODOWN, pieces for SHOP)
            location: 'GODOWN' or 'SHOP' — determines which column is updated
            unit_type: 'PACK' or 'PIECE' — recorded in the reason string for audit context
            reason: Human-readable description of the adjustment
            reason_code: Structured reason code for the ledger (default 'ADJUSTMENT')
            updated_by: Username for audit trail (default 'system')

        The inventory update and ledger insert are wrapped in a single transaction
        via engine.begin(). If the ledger insert fails, the inventory update is
        rolled back so stock totals and the audit trail never diverge.
        """
        sku = sku.strip().upper()
        col = "godown_stock_packs" if location == "GODOWN" else "shop_stock_pieces"
        # Encode location and unit_type into reason — these columns no longer exist in
        # stock_ledger after the schema refactor that introduced packs/multiplier columns.
        ledger_reason = f"[{location}/{unit_type}] {reason}".strip()

        with self.engine.begin() as conn:
            # INSERT OR IGNORE ensures the row exists in inventory_master first
            conn.execute(
                text("INSERT OR IGNORE INTO inventory_master (sku) VALUES (:sku)"),
                {"sku": sku}
            )
            conn.execute(
                text(f"UPDATE inventory_master SET {col} = {col} + :qty, last_updated = CURRENT_TIMESTAMP WHERE sku = :sku"),
                {"qty": qty, "sku": sku}
            )
            conn.execute(
                text("""
                    INSERT INTO stock_ledger
                        (sku, transaction_type, packs, multiplier, total_pieces_affected,
                         reason_code, reason, updated_by, running_balance)
                    VALUES (:sku, 'ADJUSTMENT', :packs, 1, :total, :reason_code, :reason, :updated_by, :rb)
                """),
                {
                    "sku": sku,
                    "packs": qty,
                    "total": qty,
                    "reason_code": reason_code,
                    "reason": ledger_reason,
                    "updated_by": updated_by,
                    "rb": self._godown_balance(conn, sku),
                }
            )
            # engine.begin() auto-commits on clean exit and rolls back on exception

    def transfer_to_shop(self, sku, packs, multiplier, updated_by="system"):
        """Moves packs from Godown and converts to pieces in Shop.

        Args:
            sku: Product SKU
            packs: Number of packs to move out of godown
            multiplier: Pieces per pack — used to calculate pieces added to shop
            updated_by: Username for audit trail (default 'system')

        A guard prevents transferring more packs than currently in godown.
        Two inventory updates and two ledger rows are wrapped in a single transaction
        via engine.begin(). All operations succeed together or none are applied.
        """
        sku = sku.strip().upper()
        pieces = packs * multiplier

        with self.engine.begin() as conn:
            # Guard: prevent transferring more packs than available in godown
            result = conn.execute(
                text("SELECT godown_stock_packs FROM inventory_master WHERE sku = :sku"),
                {"sku": sku}
            )
            row = result.fetchone()
            current_godown = row[0] if (row and row[0] is not None) else 0
            if current_godown < packs:
                raise DataValidationException(
                    f"Cannot transfer {packs} packs from godown for {sku}: "
                    f"only {current_godown} available. Operation cancelled."
                )

            # Deduct packs from Godown
            conn.execute(
                text("UPDATE inventory_master SET godown_stock_packs = godown_stock_packs - :p, last_updated = CURRENT_TIMESTAMP WHERE sku = :sku"),
                {"p": packs, "sku": sku}
            )
            # Add pieces to Shop
            conn.execute(
                text("UPDATE inventory_master SET shop_stock_pieces = shop_stock_pieces + :pc, last_updated = CURRENT_TIMESTAMP WHERE sku = :sku"),
                {"pc": pieces, "sku": sku}
            )
            # Godown balance after the deduction — same value for both ledger rows
            # (the shop update does not change godown_stock_packs).
            godown_after = self._godown_balance(conn, sku)
            # Ledger: REMOVE from godown
            conn.execute(
                text("""
                    INSERT INTO stock_ledger
                        (sku, transaction_type, packs, multiplier, total_pieces_affected,
                         reason_code, reason, updated_by, running_balance)
                    VALUES (:sku, 'REMOVE', :p, :m, :tp, 'TRANSFER', 'Moved to Shop', :updated_by, :rb)
                """),
                {"sku": sku, "p": packs, "m": multiplier, "tp": pieces,
                 "updated_by": updated_by, "rb": godown_after}
            )
            # Ledger: ADD to shop (packs converted to pieces)
            conn.execute(
                text("""
                    INSERT INTO stock_ledger
                        (sku, transaction_type, packs, multiplier, total_pieces_affected,
                         reason_code, reason, updated_by, running_balance)
                    VALUES (:sku, 'ADD', :p, :m, :tp, 'TRANSFER', 'Received from Godown', :updated_by, :rb)
                """),
                {"sku": sku, "p": packs, "m": multiplier, "tp": pieces,
                 "updated_by": updated_by, "rb": godown_after}
            )
            # engine.begin() auto-commits on clean exit and rolls back on exception

    def get_godown_inventory(self):
        """Fetches SKU, Category, Current Packs, Multiplier, and reorder levels."""
        query = """
        SELECT
            p.sku, p.category,
            COALESCE(i.godown_stock_packs, 0) as godown_stock_packs,
            COALESCE(i.pack_multiplier, 1) as pack_multiplier,
            COALESCE(i.reorder_point, 0) as reorder_point,
            COALESCE(i.reorder_qty, 0) as reorder_qty
        FROM product_master p
        LEFT JOIN inventory_master i ON p.sku = i.sku
        ORDER BY p.sku
        """
        return pd.read_sql(query, self.engine)

    def set_reorder_levels(self, sku, reorder_point, reorder_qty, updated_by="system"):
        """Set the per-SKU reorder point and reorder quantity (in packs).

        Args:
            sku: Product SKU
            reorder_point: Low-stock threshold in packs (0 = not configured)
            reorder_qty: Suggested reorder quantity in packs
            updated_by: Reserved for future audit use

        Raises:
            DataValidationException: If either value is negative.

        Reorder levels are configuration, not stock movements — no ledger entry
        is written. The inventory_master row is created if it does not exist.
        """
        sku = sku.strip().upper()
        rp = int(reorder_point)
        rq = int(reorder_qty)
        if rp < 0 or rq < 0:
            raise DataValidationException(
                "Reorder point and reorder quantity cannot be negative."
            )

        with self.engine.begin() as conn:
            conn.execute(
                text("INSERT OR IGNORE INTO inventory_master (sku) VALUES (:sku)"),
                {"sku": sku},
            )
            conn.execute(
                text("""
                    UPDATE inventory_master
                    SET reorder_point = :rp,
                        reorder_qty   = :rq,
                        last_updated  = CURRENT_TIMESTAMP
                    WHERE sku = :sku
                """),
                {"rp": rp, "rq": rq, "sku": sku},
            )
        logger.info(
            f"Reorder levels set for {sku}: point={rp}, qty={rq} (by {updated_by})"
        )

    def reconcile_godown_balances(self):
        """Integrity check: latest ledger running_balance vs live godown stock.

        For each SKU, compares the running_balance on its most recent ledger row
        (that has one) against inventory_master.godown_stock_packs. A mismatch
        means a ledger row was edited/deleted directly, or a write bypassed the
        ledger. Rows predating the running_balance column (NULL) are ignored.

        Returns:
            DataFrame with columns: sku, ledger_balance, actual_balance, diff.
            Empty DataFrame means everything reconciles.
        """
        query = """
        WITH latest AS (
            SELECT sku, running_balance,
                   ROW_NUMBER() OVER (
                       PARTITION BY sku ORDER BY id DESC
                   ) AS rn
            FROM stock_ledger
            WHERE running_balance IS NOT NULL
        )
        SELECT
            l.sku                              AS sku,
            l.running_balance                  AS ledger_balance,
            COALESCE(i.godown_stock_packs, 0)  AS actual_balance,
            l.running_balance - COALESCE(i.godown_stock_packs, 0) AS diff
        FROM latest l
        LEFT JOIN inventory_master i ON i.sku = l.sku
        WHERE l.rn = 1
          AND l.running_balance <> COALESCE(i.godown_stock_packs, 0)
        ORDER BY ABS(l.running_balance - COALESCE(i.godown_stock_packs, 0)) DESC
        """
        return pd.read_sql(query, self.engine)

    def get_stock_for_planning(self):
        """Returns current on-hand stock from inventory_master for purchase planning.

        Converts godown packs to base units using pack_multiplier, then adds
        shop_stock_pieces. This produces a total expressed in the same base-unit
        denomination that generate_purchase_plan() uses for gross_requirement.

        Only SKUs with positive stock are returned; SKUs with zero stock are
        omitted — they contribute nothing to the deduction and a LEFT JOIN in
        generate_purchase_plan() will fill their stock_qty with 0 via fillna().

        Returns:
            DataFrame with columns: sku (str, uppercased), stock_qty (int)
        """
        query = """
        SELECT
            sku,
            (COALESCE(godown_stock_packs, 0) * COALESCE(pack_multiplier, 1))
                + COALESCE(shop_stock_pieces, 0) AS stock_qty
        FROM inventory_master
        WHERE (COALESCE(godown_stock_packs, 0) * COALESCE(pack_multiplier, 1))
                + COALESCE(shop_stock_pieces, 0) > 0
        """
        df = pd.read_sql(query, self.engine)
        df['sku'] = df['sku'].str.upper().str.strip()
        return df

    def get_movement_report(self, sku: str, start_date: str, end_date: str) -> dict:
        """Return opening balance, movements, and closing balance for a SKU over a date range.

        Args:
            sku: Product SKU (case-insensitive; normalised to upper internally)
            start_date: ISO date string 'YYYY-MM-DD' (inclusive)
            end_date: ISO date string 'YYYY-MM-DD' (inclusive)

        Returns:
            dict with keys:
                opening_balance (int) — godown packs at start of period (None if unknown)
                movements (DataFrame) — ledger rows in [start_date, end_date], ASC
                closing_balance (int | None) — running_balance of last row in range
        """
        sku = sku.strip().upper()

        with self.engine.connect() as conn:
            # Opening balance: running_balance of the most recent row BEFORE start_date.
            # Uses running_balance so it's a single-row lookup rather than a full sum.
            ob_row = conn.execute(
                text("""
                    SELECT running_balance
                    FROM stock_ledger
                    WHERE sku = :sku
                      AND date(timestamp) < :start_date
                      AND running_balance IS NOT NULL
                    ORDER BY id DESC
                    LIMIT 1
                """),
                {"sku": sku, "start_date": start_date},
            ).fetchone()
            opening_balance = int(ob_row[0]) if ob_row else None

        movements = pd.read_sql(
            text("""
                SELECT id, transaction_type, packs, multiplier, total_pieces_affected,
                       reason_code, reason, updated_by, running_balance, timestamp
                FROM stock_ledger
                WHERE sku = :sku
                  AND date(timestamp) BETWEEN :start_date AND :end_date
                ORDER BY id ASC
            """),
            self.engine,
            params={"sku": sku, "start_date": start_date, "end_date": end_date},
        )

        closing_balance = None
        if not movements.empty and movements['running_balance'].notna().any():
            # Last non-null running_balance in the range
            non_null = movements[movements['running_balance'].notna()]
            if not non_null.empty:
                closing_balance = int(non_null.iloc[-1]['running_balance'])

        return {
            "opening_balance": opening_balance,
            "movements": movements,
            "closing_balance": closing_balance,
        }

    def get_low_stock_alerts(self) -> pd.DataFrame:
        """Return SKUs where godown stock is at or below their configured reorder point.

        Only SKUs with reorder_point > 0 are included (0 means 'not configured').
        Out-of-stock SKUs (godown_stock_packs <= 0) are included — they need ordering too.

        Returns:
            DataFrame: sku, category, godown_stock_packs, reorder_point, reorder_qty,
                       gap (reorder_point - godown_stock_packs)
        """
        query = """
        SELECT
            p.sku,
            p.category,
            COALESCE(i.godown_stock_packs, 0)  AS godown_stock_packs,
            COALESCE(i.reorder_point, 0)        AS reorder_point,
            COALESCE(i.reorder_qty, 0)          AS reorder_qty,
            COALESCE(i.reorder_point, 0) - COALESCE(i.godown_stock_packs, 0) AS gap
        FROM product_master p
        JOIN inventory_master i ON i.sku = p.sku
        WHERE i.reorder_point > 0
          AND COALESCE(i.godown_stock_packs, 0) <= i.reorder_point
        ORDER BY gap DESC, p.sku
        """
        return pd.read_sql(query, self.engine)

    def manage_godown_stock(self, sku, packs, multiplier, action_type,
                            reason="", reason_code="ADJUSTMENT", updated_by="system"):
        """Handles adding or removing packs with a specific multiplier."""
        sku = sku.strip().upper()
        # Calculate total pieces for the ledger record
        total_pieces = packs * multiplier
        
        # Adjust packs based on action
        pack_change = packs if action_type == "ADD" else -packs
        
        with self.engine.begin() as conn:
            # 1. Ensure the SKU row exists in inventory_master
            conn.execute(text("""
                INSERT OR IGNORE INTO inventory_master (sku)
                VALUES (:sku)
            """), {"sku": sku})

            # 2. Guard: prevent negative stock on REMOVE.
            #    Check is inside the transaction so the read and write are atomic —
            #    no concurrent modification can pass a stale balance between them.
            if action_type == "REMOVE":
                result = conn.execute(
                    text("SELECT godown_stock_packs FROM inventory_master WHERE sku = :sku"),
                    {"sku": sku}
                )
                row = result.fetchone()
                current_packs = row[0] if (row and row[0] is not None) else 0
                if current_packs < packs:
                    raise DataValidationException(
                        f"Cannot remove {packs} packs from {sku}: "
                        f"only {current_packs} in stock. Operation cancelled."
                    )

            # 3. Apply the stock change
            conn.execute(text("""
                UPDATE inventory_master
                SET godown_stock_packs = godown_stock_packs + :packs,
                    pack_multiplier = :mult,
                    last_updated = CURRENT_TIMESTAMP
                WHERE sku = :sku
            """), {"sku": sku, "packs": pack_change, "mult": multiplier})

            # 4. Log to Ledger for audit trail
            conn.execute(text("""
                INSERT INTO stock_ledger
                    (sku, transaction_type, packs, multiplier, total_pieces_affected,
                     reason_code, reason, updated_by, running_balance)
                VALUES (:sku, :action, :p, :m, :tp, :reason_code, :r, :updated_by, :rb)
            """), {
                "sku": sku, "action": action_type, "p": packs,
                "m": multiplier, "tp": total_pieces,
                "reason_code": reason_code, "r": reason, "updated_by": updated_by,
                "rb": self._godown_balance(conn, sku),
            })
            # engine.begin() auto-commits on clean exit and rolls back on exception

    def manage_shop_stock(self, sku, pieces, action_type,
                          reason_code="ADJUSTMENT", reason="", updated_by="system"):
        """Adds or removes pieces from shop_stock_pieces with a ledger entry.

        Args:
            sku: Product SKU
            pieces: Number of pieces to add or remove
            action_type: 'ADD' or 'REMOVE'
            reason_code: Structured reason code (SALE, DAMAGED, COUNT_CORRECTION, etc.)
            reason: Optional freetext notes
            updated_by: Username for audit trail (default 'system')

        A guard prevents REMOVE operations from driving shop_stock_pieces below zero.
        The inventory update and ledger insert are wrapped in a single engine.begin()
        transaction — both commit together or neither is applied.

        Note: shop stock is tracked in pieces, not packs. The ledger records
        multiplier=1 and total_pieces_affected=pieces to reflect this.
        """
        sku = sku.strip().upper()
        piece_change = pieces if action_type == "ADD" else -pieces

        with self.engine.begin() as conn:
            # 1. Ensure the SKU row exists
            conn.execute(
                text("INSERT OR IGNORE INTO inventory_master (sku) VALUES (:sku)"),
                {"sku": sku}
            )

            # 2. Guard: prevent negative shop stock on REMOVE
            if action_type == "REMOVE":
                result = conn.execute(
                    text("SELECT shop_stock_pieces FROM inventory_master WHERE sku = :sku"),
                    {"sku": sku}
                )
                row = result.fetchone()
                current_pieces = row[0] if (row and row[0] is not None) else 0
                if current_pieces < pieces:
                    raise DataValidationException(
                        f"Cannot remove {pieces} pieces from shop for {sku}: "
                        f"only {current_pieces} in shop. Operation cancelled."
                    )

            # 3. Apply the change to shop_stock_pieces
            conn.execute(
                text("""
                    UPDATE inventory_master
                    SET shop_stock_pieces = shop_stock_pieces + :pieces,
                        last_updated = CURRENT_TIMESTAMP
                    WHERE sku = :sku
                """),
                {"pieces": piece_change, "sku": sku}
            )

            # 4. Log to Ledger (multiplier=1: shop stock is in pieces, not packs).
            #    running_balance records the godown pack balance (unchanged by a
            #    shop-only movement) so the column is never NULL going forward.
            conn.execute(
                text("""
                    INSERT INTO stock_ledger
                        (sku, transaction_type, packs, multiplier, total_pieces_affected,
                         reason_code, reason, updated_by, running_balance)
                    VALUES (:sku, :action, :pieces, 1, :pieces, :reason_code, :reason, :updated_by, :rb)
                """),
                {
                    "sku": sku, "action": action_type, "pieces": pieces,
                    "reason_code": reason_code, "reason": reason, "updated_by": updated_by,
                    "rb": self._godown_balance(conn, sku),
                }
            )
            # engine.begin() auto-commits on clean exit and rolls back on exception