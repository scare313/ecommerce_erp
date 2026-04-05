import pandas as pd
import numpy as np
from src.infrastructure.database import get_engine

class FinanceService:
    def __init__(self):
        self.engine = get_engine()

    def calculate_profitability(self, marketplace_filter=None):
        # 1. Fetch Core Data (Listings + Packs + Products + Config)
        from sqlalchemy import text
        conn = self.engine.connect()
        
        query = """
        SELECT 
            cl.marketplace, cl.channel_sku, cl.selling_price, cl.listing_status,
            pm.pack_sku, pm.packaging_cogs, pm.quantity,
            pm.final_l_cm, pm.final_w_cm, pm.final_h_cm, pm.final_wt_kg,
            p.name as product_name, p.category, p.gst_rate, p.total_unit_cogs,
            c.default_zone, c.volumetric_divisor, c.gst_on_fees
        FROM channel_listings cl
        JOIN pack_master pm ON cl.internal_sku = pm.pack_sku
        JOIN product_master p ON pm.master_sku = p.sku
        LEFT JOIN config c ON cl.marketplace = c.marketplace
        WHERE 1=1
        """
        params = {}
        if marketplace_filter:
            query += " AND cl.marketplace = :marketplace"
            params['marketplace'] = marketplace_filter
        
        df = pd.read_sql(text(query), conn, params=params)
        
        # Fetch Rules
        pricing_rules = pd.read_sql("SELECT * FROM pricing_rules", conn)
        shipping_rules = pd.read_sql("SELECT * FROM shipping_rules", conn)
        conn.close()

        if df.empty: return df

        # --- 2. CALCULATE WEIGHTS ---
        # Volumetric Weight = (L*W*H) / Divisor
        df['vol_wt'] = (df['final_l_cm'] * df['final_w_cm'] * df['final_h_cm']) / df['volumetric_divisor']
        # Billable Weight = Max(Physical, Volumetric)
        df['billable_wt'] = df[['final_wt_kg', 'vol_wt']].max(axis=1).fillna(0.5) # Default 0.5kg if missing

        # --- 3. CALCULATE COGS ---
        df['total_cogs'] = (df['total_unit_cogs'] * df['quantity']) + df['packaging_cogs']

        # --- 4. FEE CALCULATION ENGINE ---
        def get_fees(row):
            price = row['selling_price']
            if price <= 0: return pd.Series([0, 0, 0, 0, 0]) # No price, no profit

            mkt = row['marketplace']
            cat = row['category']
            
            # A. Referral Fee & Closing Fee
            # Filter rules for this Market & Category
            rules = pricing_rules[
                (pricing_rules['marketplace'] == mkt) & 
                (pricing_rules['category_ref'] == cat) &
                (price >= pricing_rules['min_price']) & 
                (price <= pricing_rules['max_price'])
            ]
            
            if not rules.empty:
                rule = rules.iloc[0]
                ref_fee = price * rule['referral_fee_pct']
                closing_fee = rule['closing_fee_inr']
            else:
                # Fallback if no rule matches
                ref_fee = price * 0.15 
                closing_fee = 20.0

            # B. Shipping Fee
            # Filter shipping rules for this Market
            ship_rules = shipping_rules[shipping_rules['marketplace'] == mkt].sort_values('weight_slab_max_kg')
            
            # Find the slab that fits the billable weight
            slab = ship_rules[ship_rules['weight_slab_max_kg'] >= row['billable_wt']]
            
            shipping_cost = 0.0
            if not slab.empty:
                # Use default zone from config (e.g. "National_Fee")
                zone_col = f"{str(row['default_zone']).lower()}_fee"
                # If column exists in rule table, use it, else default to national
                if zone_col in slab.columns:
                    shipping_cost = slab.iloc[0][zone_col]
                else:
                    shipping_cost = slab.iloc[0]['national_fee']
            else:
                # Weight exceeds all slabs, take max
                if not ship_rules.empty:
                    shipping_cost = ship_rules.iloc[-1]['national_fee']

            # C. GST on Fees
            gst_pct = row['gst_on_fees'] if pd.notnull(row['gst_on_fees']) else 0.18
            total_fees_excl_gst = ref_fee + closing_fee + shipping_cost
            gst_amt = total_fees_excl_gst * gst_pct
            
            total_deduction = total_fees_excl_gst + gst_amt
            
            return pd.Series([ref_fee, closing_fee, shipping_cost, gst_amt, total_deduction])

        # Apply Logic
        df[['ref_fee', 'closing_fee', 'shipping_fee', 'gst_fee', 'total_platform_fees']] = df.apply(get_fees, axis=1)

        # --- 5. FINAL PROFIT ---
        # Settlement = SP - Platform Fees
        df['settlement_value'] = df['selling_price'] - df['total_platform_fees']
        
        # Net Profit = Settlement - COGS
        df['net_profit'] = df['settlement_value'] - df['total_cogs']
        
        # Margin %
        df['margin_pct'] = np.where(df['selling_price'] > 0, (df['net_profit'] / df['selling_price']) * 100, 0)

        return df