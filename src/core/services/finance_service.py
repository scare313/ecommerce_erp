import os
import pandas as pd
import numpy as np
from src.infrastructure.database import get_engine
from src.infrastructure.logger import get_logger, DatabaseException, ServiceException
from sqlalchemy import text

logger = get_logger(__name__)

def map_fuzzy_category(category_name):
    """
    Fuzzily maps dynamic listings categories (like 'baseball-cap') to target 
    rules categories (like 'Accessories') using simple keyword checking.
    """
    if not category_name or not isinstance(category_name, str):
        return "Other"
    
    cat_lower = category_name.lower().strip()
    
    # 1. Accessories check
    if any(kw in cat_lower for kw in ["cap", "beanie", "hat", "accessory", "accessories"]):
        return "Accessories"
        
    # 2. Apparel check
    if any(kw in cat_lower for kw in ["shirt", "clothing", "apparel", "tshirt", "t-shirt", "top", "pant", "jeans"]):
        return "Apparel"
        
    # 3. Direct direct match
    standard_categories = ["Apparel", "Accessories", "Footwear", "Home", "Grocery", "Beauty", "Toys", "Electronics", "Other"]
    for std_cat in standard_categories:
        if std_cat.lower() == cat_lower:
            return std_cat
            
    return "Other"


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
                - total_platform_fees, tcs_tds, settlement_value, net_profit, margin_pct
                
        Raises:
            ServiceException: If calculation fails
        """
        try:
            logger.info(f"Calculating profitability (market filter: {marketplace_filter or 'ALL'})")
            
            try:
                with self.engine.connect() as conn:
                    # 1. Fetch Core Data (Listings + Packs + Products)
                    logger.debug("Fetching core listings data from database...")
                    query = """
                SELECT
                    cl.marketplace, cl.channel_sku, cl.selling_price, cl.listing_status,
                    pm.pack_sku, pm.packaging_cogs, pm.quantity,
                    pm.final_l_cm, pm.final_w_cm, pm.final_h_cm, pm.final_wt_kg,
                    p.name as product_name, p.category, p.gst_rate, p.total_unit_cogs
                FROM channel_listings cl
                JOIN pack_master pm ON cl.internal_sku = pm.pack_sku
                JOIN product_master p ON pm.master_sku = p.sku
                WHERE 1=1
                """
                    params = {}
                    if marketplace_filter:
                        query += " AND cl.marketplace = :marketplace"
                        params['marketplace'] = marketplace_filter

                    df = pd.read_sql(text(query), conn, params=params)
                    logger.debug(f"Retrieved {len(df)} listings for analysis")

                    # Load fee rules from SQL (authoritative source after E9 migration)
                    logger.debug("Loading fee rules from SQL config tables...")
                    config_df = pd.read_sql("SELECT * FROM config", conn)
                    pricing_rules = pd.read_sql("SELECT * FROM pricing_rules", conn)
                    shipping_rules = pd.read_sql("SELECT * FROM shipping_rules", conn)

                if df.empty:
                    logger.warning("No data found for profitability calculation")
                    return df

                # Join config_df into core df
                if not config_df.empty:
                    df = pd.merge(df, config_df, on="marketplace", how="left")
                
                # Add default columns if not exist
                for col in ["default_zone", "volumetric_divisor", "gst_on_fees"]:
                    if col not in df.columns:
                        df[col] = None
                
                # Fill missing/NaN configs with sensible defaults
                df['default_zone'] = df['default_zone'].fillna("national")
                df['volumetric_divisor'] = df['volumetric_divisor'].fillna(5000).astype(int)
                df['gst_on_fees'] = df['gst_on_fees'].fillna(0.18)

                # --- 2. CALCULATE WEIGHTS ---
                logger.debug("Calculating volumetric weights...")
                df['vol_wt'] = (df['final_l_cm'] * df['final_w_cm'] * df['final_h_cm']) / df['volumetric_divisor']
                df['billable_wt'] = df[['final_wt_kg', 'vol_wt']].max(axis=1).fillna(0.5)

                # --- 3. CALCULATE COGS ---
                logger.debug("Calculating COGS...")
                df['total_cogs'] = (df['total_unit_cogs'] * df['quantity']) + df['packaging_cogs']

                # --- 4. FEE CALCULATION ENGINE ---
                logger.debug("Calculating platform fees and statutory taxes...")
                
                def get_fees(row):
                    """Calculate platform fees for a single listing."""
                    try:
                        price = row['selling_price']
                        if price <= 0:
                            return pd.Series([0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])

                        mkt = row['marketplace']
                        raw_cat = row['category']
                        
                        # A. Referral Fee & Closing Fee
                        # Try exact match first
                        rules = pricing_rules[
                            (pricing_rules['marketplace'] == mkt) & 
                            (pricing_rules['category_ref'] == raw_cat) &
                            (price >= pricing_rules['min_price']) & 
                            (price <= pricing_rules['max_price'])
                        ]
                        
                        if rules.empty:
                            # Fallback to fuzzy category mapping
                            cat = map_fuzzy_category(raw_cat)
                            rules = pricing_rules[
                                (pricing_rules['marketplace'] == mkt) & 
                                (pricing_rules['category_ref'] == cat) &
                                (price >= pricing_rules['min_price']) & 
                                (price <= pricing_rules['max_price'])
                            ]
                        else:
                            cat = raw_cat
                        
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
                        
                        # D. TCS & TDS
                        # TCS rate: 0.5% (or 0.005) of the taxable value (excl GST)
                        # TDS rate: 0.1% (or 0.001) of the gross selling price
                        gst_rate_pct = row['gst_rate'] if pd.notnull(row['gst_rate']) else 18.0
                        taxable_val = price / (1 + (gst_rate_pct / 100.0))
                        
                        tcs_amt = round(taxable_val * 0.005, 2)
                        tds_amt = round(price * 0.001, 2)
                        tcs_tds = tcs_amt + tds_amt
                        
                        return pd.Series([ref_fee, closing_fee, shipping_cost, gst_amt, total_deduction, tcs_tds, tcs_amt, tds_amt])
                    except Exception as e:
                        logger.error(f"Error calculating fees for row: {str(e)}")
                        return pd.Series([0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])

                # Apply Logic
                df[['ref_fee', 'closing_fee', 'shipping_fee', 'gst_fee', 'total_platform_fees', 'tcs_tds', 'tcs_fee', 'tds_fee']] = df.apply(get_fees, axis=1)

                # --- 5. FINAL PROFIT ---
                logger.debug("Calculating final profitability metrics...")
                df['settlement_value'] = df['selling_price'] - df['total_platform_fees'] - df['tcs_tds']
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