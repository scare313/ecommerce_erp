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

    def calculate_profitability(self, marketplace_filter=None, account_filter=None):
        """
        Calculate per-unit profitability for every listing.

        Accounting basis (corrected in E12 — see ACCOUNTING NOTES below):
          * Revenue is taken EXCLUDING output GST. GST collected from the buyer
            is a liability passed to the government, never income.
          * GST charged on marketplace fees is recoverable input tax credit, so
            it is reported separately and NOT treated as a cost.
          * TCS and TDS withheld by the marketplace are recoverable credits, not
            expenses. They reduce the cash settled, not the profit.
          * Shipping is modelled per fulfilment type: 'fba' uses the flat
            Amazon-fulfilled bands in fba_fee_rules, anything else uses the
            zone-based slabs in shipping_rules.

        Args:
            marketplace_filter: Optional marketplace name to filter results
            account_filter: Optional seller account_id to filter results

        Returns:
            DataFrame: Profitability data with columns:
                - account_id, marketplace, channel_sku, selling_price
                - revenue_ex_gst, gst_collected, total_cogs
                - ref_fee, closing_fee, shipping_fee, tech_fee
                - fees_ex_gst, gst_on_fees (ITC), total_platform_fees
                - tcs_fee, tds_fee, tcs_tds, settlement_value
                - net_profit, margin_pct

        Raises:
            ServiceException: If calculation fails
        """
        try:
            logger.info(
                f"Calculating profitability (market: {marketplace_filter or 'ALL'}, "
                f"account: {account_filter or 'ALL'})"
            )
            
            try:
                with self.engine.connect() as conn:
                    # 1. Fetch Core Data (Listings + Packs + Products)
                    logger.debug("Fetching core listings data from database...")

                    # The account and fulfilment columns arrived in E12. A
                    # database that has not run that migration yet still works —
                    # every listing is simply treated as account1 / easy_ship,
                    # which is exactly what it was before the column existed.
                    listing_cols = {
                        row[1]
                        for row in conn.exec_driver_sql(
                            "PRAGMA table_info(channel_listings)"
                        ).fetchall()
                    }
                    account_expr = (
                        "COALESCE(cl.account_id, 'account1')"
                        if "account_id" in listing_cols
                        else "'account1'"
                    )
                    fulfilment_expr = (
                        "COALESCE(cl.fulfilment_type, 'easy_ship')"
                        if "fulfilment_type" in listing_cols
                        else "'easy_ship'"
                    )

                    query = f"""
                SELECT
                    cl.marketplace, cl.channel_sku, cl.selling_price, cl.listing_status,
                    {account_expr}    AS account_id,
                    {fulfilment_expr} AS fulfilment_type,
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
                    if account_filter and "account_id" in listing_cols:
                        query += " AND COALESCE(cl.account_id, 'account1') = :account_id"
                        params['account_id'] = account_filter
                    elif account_filter:
                        logger.warning(
                            "Account filter ignored: channel_listings has no "
                            "account_id column. Restart the app to run the E12 migration."
                        )

                    df = pd.read_sql(text(query), conn, params=params)
                    logger.debug(f"Retrieved {len(df)} listings for analysis")

                    # Load fee rules from SQL (authoritative source after E9 migration)
                    logger.debug("Loading fee rules from SQL config tables...")
                    config_df = pd.read_sql("SELECT * FROM config", conn)
                    pricing_rules = pd.read_sql("SELECT * FROM pricing_rules", conn)
                    shipping_rules = pd.read_sql("SELECT * FROM shipping_rules", conn)
                    try:
                        fba_rules = pd.read_sql("SELECT * FROM fba_fee_rules", conn)
                    except Exception:
                        # Table absent on a database that predates E12
                        fba_rules = pd.DataFrame(
                            columns=["marketplace", "size_band",
                                     "weight_slab_max_kg", "fee_inr"]
                        )

                if df.empty:
                    logger.warning("No data found for profitability calculation")
                    return df

                # Join config_df into core df
                if not config_df.empty:
                    df = pd.merge(df, config_df, on="marketplace", how="left")
                
                # Add default columns if not exist
                for col in ["default_zone", "volumetric_divisor", "gst_on_fees",
                            "tech_fee_inr"]:
                    if col not in df.columns:
                        df[col] = None

                # Fill missing/NaN configs with sensible defaults
                df['default_zone'] = df['default_zone'].astype(object).fillna("national")
                df['volumetric_divisor'] = pd.to_numeric(
                    df['volumetric_divisor'], errors='coerce'
                ).fillna(5000).astype(int)
                df['gst_on_fees'] = pd.to_numeric(
                    df['gst_on_fees'], errors='coerce'
                ).fillna(0.18)
                df['tech_fee_inr'] = pd.to_numeric(
                    df['tech_fee_inr'], errors='coerce'
                ).fillna(0.0)

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
                    """Return the fee components for one listing, all per unit.

                    Returns, in order:
                        ref_fee, closing_fee, shipping_fee, tech_fee,
                        fees_ex_gst, gst_on_fees, tcs_fee, tds_fee
                    Every amount EXCLUDES GST except gst_on_fees itself.
                    """
                    try:
                        price = row['selling_price']
                        if price <= 0:
                            return pd.Series([0.0] * 8)

                        mkt = row['marketplace']
                        raw_cat = row['category']

                        fulfilment = str(row.get('fulfilment_type') or 'easy_ship').lower()

                        # A. Referral fee and fixed closing fee.
                        #    The closing fee differs by fulfilment channel, so a
                        #    rule scoped to this fulfilment wins over a shared one.
                        if 'fulfilment_type' in pricing_rules.columns:
                            scope = pricing_rules['fulfilment_type'].fillna('any')
                            in_scope = pricing_rules[scope.isin([fulfilment, 'any'])].copy()
                            in_scope['_specificity'] = (scope[in_scope.index] == fulfilment).astype(int)
                        else:
                            in_scope = pricing_rules.copy()
                            in_scope['_specificity'] = 0

                        def _match(category):
                            hit = in_scope[
                                (in_scope['marketplace'] == mkt) &
                                (in_scope['category_ref'] == category) &
                                (price >= in_scope['min_price']) &
                                (price <= in_scope['max_price'])
                            ]
                            return hit.sort_values('_specificity', ascending=False)

                        rules = _match(raw_cat)
                        if rules.empty:
                            # Fallback to fuzzy category mapping
                            cat = map_fuzzy_category(raw_cat)
                            rules = _match(cat)
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

                        # B. Fulfilment cost. Amazon-fulfilled orders are billed a
                        #    flat amount per weight band regardless of destination;
                        #    merchant-fulfilled orders are billed by weight AND zone.
                        billable_wt = row['billable_wt']
                        shipping_cost = 0.0

                        if fulfilment == 'fba' and not fba_rules.empty:
                            bands = fba_rules[
                                fba_rules['marketplace'] == mkt
                            ].sort_values('weight_slab_max_kg')
                            band = bands[bands['weight_slab_max_kg'] >= billable_wt]
                            if not band.empty:
                                shipping_cost = band.iloc[0]['fee_inr']
                            elif not bands.empty:
                                shipping_cost = bands.iloc[-1]['fee_inr']
                        else:
                            ship_rules = shipping_rules[
                                shipping_rules['marketplace'] == mkt
                            ].sort_values('weight_slab_max_kg')
                            slab = ship_rules[
                                ship_rules['weight_slab_max_kg'] >= billable_wt
                            ]
                            if not slab.empty:
                                zone_col = f"{str(row['default_zone']).lower()}_fee"
                                if zone_col in slab.columns:
                                    shipping_cost = slab.iloc[0][zone_col]
                                else:
                                    shipping_cost = slab.iloc[0]['national_fee']
                            elif not ship_rules.empty:
                                shipping_cost = ship_rules.iloc[-1]['national_fee']

                        # C. Technology fee — flat per order. Observed on
                        #    Amazon-fulfilled orders only: across 1,521 merchant
                        #    -fulfilled orders (May-Jul 2026) the only non-shipping
                        #    charge was the Rs 1 closing fee, whereas every FBA
                        #    order carried Rs 14 bundled into its fulfilment
                        #    charge (a cap billed Rs 76.70 = (51 + 14) x 1.18).
                        #    Re-check this if Easy Ship statements start showing it.
                        tech_fee = 0.0
                        if fulfilment == 'fba' and pd.notnull(row['tech_fee_inr']):
                            tech_fee = row['tech_fee_inr']

                        # D. GST on fees. This is INPUT TAX CREDIT, reclaimed in the
                        #    GST return, so it is reported but not charged to profit.
                        gst_pct = row['gst_on_fees'] if pd.notnull(row['gst_on_fees']) else 0.18
                        fees_ex_gst = ref_fee + closing_fee + shipping_cost + tech_fee
                        gst_on_fees = fees_ex_gst * gst_pct

                        # E. TCS and TDS withheld at source. Both are recoverable
                        #    credits against the seller's own liability, so they
                        #    reduce cash settled but never profit.
                        gst_rate_pct = row['gst_rate'] if pd.notnull(row['gst_rate']) else 18.0
                        taxable_val = price / (1 + (gst_rate_pct / 100.0))

                        tcs_amt = round(taxable_val * 0.005, 2)
                        tds_amt = round(price * 0.001, 2)

                        return pd.Series([
                            ref_fee, closing_fee, shipping_cost, tech_fee,
                            fees_ex_gst, gst_on_fees, tcs_amt, tds_amt,
                        ])
                    except Exception as e:
                        logger.error(f"Error calculating fees for row: {str(e)}")
                        return pd.Series([0.0] * 8)

                # Apply Logic
                df[[
                    'ref_fee', 'closing_fee', 'shipping_fee', 'tech_fee',
                    'fees_ex_gst', 'gst_on_fees', 'tcs_fee', 'tds_fee',
                ]] = df.apply(get_fees, axis=1)

                df['tcs_tds'] = df['tcs_fee'] + df['tds_fee']

                # Cash leaving the account on fees, GST included. Useful for
                # reconciling against a settlement report, which quotes gross.
                df['total_platform_fees'] = df['fees_ex_gst'] + df['gst_on_fees']

                # --- 5. FINAL PROFIT ---
                # ACCOUNTING NOTES
                # Revenue excludes output GST: the buyer's GST is collected on the
                # government's behalf and never belongs to the business. Costs
                # exclude input GST on fees for the mirror-image reason — that GST
                # is reclaimed, so the true cost of a fee is its base amount.
                # TCS and TDS are withheld from the payout but recovered later, so
                # they appear in settlement_value and not in net_profit.
                logger.debug("Calculating final profitability metrics...")
                gst_rate_frac = df['gst_rate'].fillna(18.0) / 100.0
                df['revenue_ex_gst'] = df['selling_price'] / (1 + gst_rate_frac)
                df['gst_collected'] = df['selling_price'] - df['revenue_ex_gst']

                # Cash actually settled by the marketplace, for bank reconciliation
                df['settlement_value'] = (
                    df['selling_price'] - df['total_platform_fees'] - df['tcs_tds']
                )

                # Economic profit per unit sold
                df['net_profit'] = (
                    df['revenue_ex_gst'] - df['fees_ex_gst'] - df['total_cogs']
                )
                df['margin_pct'] = np.where(
                    df['revenue_ex_gst'] > 0,
                    (df['net_profit'] / df['revenue_ex_gst']) * 100,
                    0,
                )

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