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
        if files_dict.get('amazon'):
            df, _ = parse_amazon_sales(files_dict['amazon'])
            if df is not None: sales_dfs.append(df)
            
        if files_dict.get('flipkart'):
            df, _ = parse_flipkart_sales(files_dict['flipkart'])
            if df is not None: sales_dfs.append(df)
            
        if files_dict.get('meesho'):
            df, _ = parse_meesho_sales(files_dict['meesho'])
            if df is not None: sales_dfs.append(df)

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
        plan = valid_sales.groupby(['base_sku', 'product_name', 'supplier', 'category']).agg(
            total_base_sold=('base_units_sold', 'sum')
        ).reset_index()
        
        # 6. Inventory Math
        sales_days = params.get('sales_days', 30)
        lead_time = params.get('lead_time', 10)
        safety_days = params.get('safety_stock', 7)
        buffer_days = params.get('purchase_period', 15)
        
        # Average Daily Sales (ADS)
        plan['ads'] = plan['total_base_sold'] / sales_days
        
        # Requirements
        plan['lead_time_demand'] = plan['ads'] * lead_time
        plan['safety_stock'] = plan['ads'] * safety_days
        plan['cycle_stock'] = plan['ads'] * buffer_days
        
        plan['gross_requirement'] = plan['lead_time_demand'] + plan['safety_stock'] + plan['cycle_stock']
        
        # 7. Subtract Current Stock (if provided)
        if files_dict.get('stock'):
            stock_df, _ = parse_stock_file(files_dict['stock'])
            if stock_df is not None:
                # Merge stock onto plan
                plan = pd.merge(plan, stock_df, left_on='base_sku', right_on='sku', how='left')
                plan['stock_qty'] = plan['stock_qty'].fillna(0)
            else:
                plan['stock_qty'] = 0
        else:
            plan['stock_qty'] = 0
            
        # 8. Net Requirement
        plan['to_purchase'] = (plan['gross_requirement'] - plan['stock_qty']).clip(lower=0)
        
        # Rounding
        plan['to_purchase'] = plan['to_purchase'].round().astype(int)
        plan['ads'] = plan['ads'].round(2)
        
        return plan, orphans
    
    def get_inventory_status(self):
        """Joins Product Master with the dedicated Inventory table."""
        query = """
        SELECT 
            p.sku, 
            p.category, 
            COALESCE(i.godown_stock_packs, 0) as godown_stock_packs, 
            COALESCE(i.shop_stock_pieces, 0) as shop_stock_pieces,
            pm.quantity as multiplier
        FROM product_master p
        LEFT JOIN inventory_master i ON p.sku = i.sku
        LEFT JOIN pack_master pm ON p.sku = pm.master_sku
        GROUP BY p.sku
        """
        df = pd.read_sql(query, self.engine)
        df['multiplier'] = df['multiplier'].fillna(1)
        # Total Pieces = (Godown Packs * Multiplier) + Shop Pieces
        df['total_pieces'] = (df['godown_stock_packs'] * df['multiplier']) + df['shop_stock_pieces']
        return df

    def add_stock(self, sku, qty, location, unit_type, reason):
        """Updates inventory_master and logs to ledger."""
        col = "godown_stock_packs" if location == "GODOWN" else "shop_stock_pieces"
        with self.engine.connect() as conn:
            # INSERT OR IGNORE ensures the row exists in inventory_master first
            conn.execute(text("INSERT OR IGNORE INTO inventory_master (sku) VALUES (:sku)"), {"sku": sku})
            conn.execute(text(f"UPDATE inventory_master SET {col} = {col} + :qty, last_updated = CURRENT_TIMESTAMP WHERE sku = :sku"), 
                         {"qty": qty, "sku": sku})
            
            conn.execute(text("""INSERT INTO stock_ledger (sku, transaction_type, location, qty_change, unit_type, reason) 
                                VALUES (:sku, 'ADJUSTMENT', :loc, :qty, :unit, :reason)"""),
                         {"sku": sku, "loc": location, "qty": qty, "unit": unit_type, "reason": reason})
            conn.commit()

    def transfer_to_shop(self, sku, packs, multiplier):
        """Moves packs from Godown and converts to pieces in Shop."""
        with self.engine.connect() as conn:
            # Deduct Packs from Godown
            conn.execute(text("UPDATE inventory_master SET godown_stock_packs = godown_stock_packs - :p WHERE sku = :sku"),
                         {"p": packs, "sku": sku})
            # Add Pieces to Shop
            pieces = packs * multiplier
            conn.execute(text("UPDATE inventory_master SET shop_stock_pieces = shop_stock_pieces + :pc WHERE sku = :sku"),
                         {"pc": pieces, "sku": sku})
            # Ledger entries
            conn.execute(text("INSERT INTO stock_ledger (sku, transaction_type, location, qty_change, unit_type, reason) VALUES (:sku, 'TRANSFER', 'GODOWN', :q, 'PACK', 'Moved to Shop')"),
                         {"sku": sku, "q": -packs})
            conn.execute(text("INSERT INTO stock_ledger (sku, transaction_type, location, qty_change, unit_type, reason) VALUES (:sku, 'TRANSFER', 'SHOP', :q, 'PIECE', 'Received from Godown')"),
                         {"sku": sku, "q": pieces})
            conn.commit()

    def get_godown_inventory(self):
        """Fetches SKU, Category, Current Packs, and Multiplier."""
        query = """
        SELECT 
            p.sku, p.category, 
            COALESCE(i.godown_stock_packs, 0) as godown_stock_packs,
            COALESCE(i.pack_multiplier, 1) as pack_multiplier
        FROM product_master p
        LEFT JOIN inventory_master i ON p.sku = i.sku
        ORDER BY p.sku
        """
        return pd.read_sql(query, self.engine)

    def manage_godown_stock(self, sku, packs, multiplier, action_type, reason=""):
        """Handles adding or removing packs with a specific multiplier."""
        # Calculate total pieces for the ledger record
        total_pieces = packs * multiplier
        
        # Adjust packs based on action
        pack_change = packs if action_type == "ADD" else -packs
        
        with self.engine.connect() as conn:
            # 1. First, ensure the SKU exists in inventory_master
            conn.execute(text("""
                INSERT OR IGNORE INTO inventory_master (sku)
                VALUES (:sku)
            """), {"sku": sku})
            
            # 2. Update the inventory with the change (add/remove packs)
            conn.execute(text("""
                UPDATE inventory_master 
                SET godown_stock_packs = godown_stock_packs + :packs,
                    pack_multiplier = :mult,
                    last_updated = CURRENT_TIMESTAMP
                WHERE sku = :sku
            """), {"sku": sku, "packs": pack_change, "mult": multiplier})

            # 3. Log to Ledger for audit trail
            conn.execute(text("""
                INSERT INTO stock_ledger (sku, transaction_type, packs, multiplier, total_pieces_affected, reason)
                VALUES (:sku, :action, :p, :m, :tp, :r)
            """), {
                "sku": sku, "action": action_type, "p": packs, 
                "m": multiplier, "tp": total_pieces, "r": reason
            })
            conn.commit()