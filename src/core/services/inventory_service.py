import pandas as pd
from src.infrastructure.database import get_engine
from src.infrastructure.parsers import parse_amazon_sales, parse_flipkart_sales, parse_meesho_sales, parse_stock_file

class InventoryService:
    def __init__(self):
        self.engine = get_engine()

    def get_master_mapping(self):
        """
        Fetches the complete mapping from DB:
        Channel Listing -> Pack -> Base Product
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
        """
        Main logic to calculate procurement needs.
        files_dict: {'amazon': file, 'flipkart': file, 'meesho': file, 'stock': file}
        params: {'sales_days': 30, 'lead_time': 10, ...}
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