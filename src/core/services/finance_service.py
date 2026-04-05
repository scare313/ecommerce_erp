import pandas as pd
import numpy as np
from src.infrastructure.database import get_engine
from src.infrastructure.logger import get_logger, DatabaseException, ServiceException
from sqlalchemy import text

logger = get_logger(__name__)

class FinanceService:
    """Service for financial calculations including profitability analysis."""
    
    def __init__(self):
        """Initialize finance service with database engine."""
        try:
            self.engine = get_engine()
            logger.debug("FinanceService initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize FinanceService: {str(e)}", exc_info=True)
            raise

    def calculate_profitability(self, marketplace_filter=None):
        """
        Calculate profitability metrics for all listings broken down by marketplace.
        
        Args:
            marketplace_filter: Optional marketplace name to filter results
            
        Returns:
            DataFrame: Profitability data with columns:
                - marketplace, channel_sku, selling_price
                - ref_fee, closing_fee, shipping_fee, gst_fee
                - total_platform_fees, settlement_value, net_profit, margin_pct
                
        Raises:
            ServiceException: If calculation fails
        """
        try:
            logger.info(f"Calculating profitability (market filter: {marketplace_filter or 'ALL'})")
            
            try:
                conn = self.engine.connect()
                
                # 1. Fetch Core Data (Listings + Packs + Products + Config)
                logger.debug("Fetching core data from database...")
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
                    logger.debug(f"Filtering by marketplace: {marketplace_filter}")
                
                df = pd.read_sql(text(query), conn, params=params)
                logger.debug(f"Retrieved {len(df)} listings for analysis")
                
                # Fetch Rules
                logger.debug("Fetching pricing and shipping rules...")
                pricing_rules = pd.read_sql("SELECT * FROM pricing_rules", conn)
                shipping_rules = pd.read_sql("SELECT * FROM shipping_rules", conn)
                logger.debug(f"Loaded {len(pricing_rules)} pricing rules, {len(shipping_rules)} shipping rules")
                
                conn.close()

                if df.empty:
                    logger.warning("No data found for profitability calculation")
                    return df

                # --- 2. CALCULATE WEIGHTS ---
                logger.debug("Calculating volumetric weights...")
                df['vol_wt'] = (df['final_l_cm'] * df['final_w_cm'] * df['final_h_cm']) / df['volumetric_divisor']
                df['billable_wt'] = df[['final_wt_kg', 'vol_wt']].max(axis=1).fillna(0.5)

                # --- 3. CALCULATE COGS ---
                logger.debug("Calculating COGS...")
                df['total_cogs'] = (df['total_unit_cogs'] * df['quantity']) + df['packaging_cogs']

                # --- 4. FEE CALCULATION ENGINE ---
                logger.debug("Calculating platform fees...")
                
                def get_fees(row):
                    """Calculate platform fees for a single listing."""
                    try:
                        price = row['selling_price']
                        if price <= 0:
                            return pd.Series([0, 0, 0, 0, 0])

                        mkt = row['marketplace']
                        cat = row['category']
                        
                        # A. Referral Fee & Closing Fee
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
                            logger.debug(f"No pricing rule for {mkt}/{cat}/{price}, using fallback")
                            ref_fee = price * 0.15 
                            closing_fee = 20.0

                        # B. Shipping Fee
                        ship_rules = shipping_rules[shipping_rules['marketplace'] == mkt].sort_values('weight_slab_max_kg')
                        
                        slab = ship_rules[ship_rules['weight_slab_max_kg'] >= row['billable_wt']]
                        
                        shipping_cost = 0.0
                        if not slab.empty:
                            zone_col = f"{str(row['default_zone']).lower()}_fee"
                            if zone_col in slab.columns:
                                shipping_cost = slab.iloc[0][zone_col]
                            else:
                                shipping_cost = slab.iloc[0]['national_fee']
                        else:
                            if not ship_rules.empty:
                                shipping_cost = ship_rules.iloc[-1]['national_fee']

                        # C. GST on Fees
                        gst_pct = row['gst_on_fees'] if pd.notnull(row['gst_on_fees']) else 0.18
                        total_fees_excl_gst = ref_fee + closing_fee + shipping_cost
                        gst_amt = total_fees_excl_gst * gst_pct
                        
                        total_deduction = total_fees_excl_gst + gst_amt
                        
                        return pd.Series([ref_fee, closing_fee, shipping_cost, gst_amt, total_deduction])
                    except Exception as e:
                        logger.error(f"Error calculating fees for row: {str(e)}")
                        return pd.Series([0, 0, 0, 0, 0])

                # Apply Logic
                df[['ref_fee', 'closing_fee', 'shipping_fee', 'gst_fee', 'total_platform_fees']] = df.apply(get_fees, axis=1)

                # --- 5. FINAL PROFIT ---
                logger.debug("Calculating final profitability metrics...")
                df['settlement_value'] = df['selling_price'] - df['total_platform_fees']
                df['net_profit'] = df['settlement_value'] - df['total_cogs']
                df['margin_pct'] = np.where(df['selling_price'] > 0, (df['net_profit'] / df['selling_price']) * 100, 0)

                logger.info(f"✅ Profitability calculated for {len(df)} listings")
                
                # Log summary metrics
                avg_margin = df['margin_pct'].mean()
                logger.info(f"Summary: Average margin = {avg_margin:.2f}%, Total net profit = ₹{df['net_profit'].sum():.2f}")
                
                return df
                
            except Exception as e:
                logger.error(f"Error during profitability calculations: {str(e)}", exc_info=True)
                raise ServiceException(f"Profitability calculation failed: {str(e)}") from e
                
        except ServiceException:
            raise
        except Exception as e:
            logger.error(f"Unexpected error in profitability calculation: {str(e)}", exc_info=True)
            raise