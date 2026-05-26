"""Onboarding service for catalog bootstrapping from marketplace files.

Orchestrates the full onboarding flow:
    1. Parse uploaded files (Amazon/Flipkart/Meesho) via listing_parsers
    2. Auto-group rows by internal_sku across marketplaces
    3. Detect conflicts (different names/HSN/dims for same SKU)
    4. Compute diff against existing DB (for re-run idempotency)
    5. Commit via upsert (preserves user-edited fields like COGS/category)

Idempotency contract:
    - New SKUs are inserted
    - Existing SKUs get specific fields updated (price, dims, status)
    - User-curated fields (category, mfg_cost, total_unit_cogs, brand if set,
      gst_rate if set) are preserved via COALESCE
"""
from datetime import datetime
import pandas as pd
from sqlalchemy import text
from src.infrastructure.database import get_engine
from src.infrastructure.listing_parsers import parse_all_listings, UNIFIED_COLUMNS
from src.infrastructure.logger import (
    get_logger,
    DatabaseException,
    DataValidationException,
    ServiceException,
)
from src.core.cache import clear_all_caches

logger = get_logger(__name__)


# =============================================================================
# CONSTANTS
# =============================================================================

# Fields the wizard "owns" — these may be updated on re-run
PRODUCT_WIZARD_FIELDS = ['name', 'mrp', 'hsn']

# Fields preserved via COALESCE — only filled if currently NULL
PRODUCT_PROTECTED_FIELDS = ['gst_rate', 'brand', 'category']

# Pack fields the wizard owns (always with COALESCE — don't wipe with NULL)
PACK_WIZARD_FIELDS = ['final_l_cm', 'final_w_cm', 'final_h_cm', 'final_wt_kg']

# Listing fields the wizard always overwrites on re-run (price drift is real)
LISTING_WIZARD_FIELDS = ['selling_price', 'listing_status', 'internal_sku']


# =============================================================================
# SERVICE
# =============================================================================

class OnboardingService:
    """Service for first-time catalog bootstrapping from marketplace files."""

    def __init__(self):
        """Initialize onboarding service with database engine."""
        try:
            self.engine = get_engine()
            logger.debug("OnboardingService initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize OnboardingService: {str(e)}", exc_info=True)
            raise

    # =========================================================================
    # STEP 1+2: PARSE & STAGE
    # =========================================================================

    def parse_and_stage(self, files_dict):
        """
        Parse all uploaded files and produce unified staging DataFrame.

        Args:
            files_dict: {'amazon': file_buf | None,
                         'flipkart': file_buf | None,
                         'meesho': file_buf | None}

        Returns:
            tuple: (staging_df, parse_errors)
                - staging_df: unified DataFrame ready for grouping
                - parse_errors: list[str] of per-file error messages
        """
        try:
            logger.info("Starting parse_and_stage...")

            if not any(files_dict.values()):
                raise DataValidationException(
                    "No files provided. Upload at least one marketplace file."
                )

            staging_df, errors = parse_all_listings(files_dict)

            if staging_df.empty and errors:
                logger.error(f"All file parses failed: {errors}")
                return staging_df, errors

            logger.info(
                f"✅ Parse complete: {len(staging_df)} rows staged, "
                f"{len(errors)} file(s) had errors"
            )
            return staging_df, errors

        except DataValidationException:
            raise
        except Exception as e:
            logger.error(f"Error in parse_and_stage: {str(e)}", exc_info=True)
            raise ServiceException(f"Parse and stage failed: {str(e)}") from e

    # =========================================================================
    # STEP 3: AUTO-GROUP & CONFLICT DETECTION
    # =========================================================================

    def auto_group(self, staging_df):
        """
        Group staging rows by internal_sku across marketplaces.

        Auto-grouping rule (per user spec):
            - Same internal_sku across files → auto-merge into 1 product/pack
            - Single-marketplace SKUs → still auto-create (no conflict possible)
            - Conflicts (same SKU, different names/HSN) → flagged for review

        Args:
            staging_df: Unified staging DataFrame from parse_and_stage()

        Returns:
            tuple: (grouped_df, conflicts)
                - grouped_df: One row per unique internal_sku with consolidated fields
                - conflicts: list of dicts {sku, field, options} for manual review
        """
        try:
            logger.info(f"Auto-grouping {len(staging_df)} staging rows...")

            if staging_df.empty:
                return pd.DataFrame(), []

            grouped_rows = []
            conflicts = []

            for sku, group in staging_df.groupby('internal_sku'):
                marketplaces = sorted(group['marketplace'].unique().tolist())

                # --- Resolve name (longest wins, conflicts flagged if differ) ---
                names = [n for n in group['name'].dropna().unique() if n]
                if len(names) > 1:
                    longest = max(names, key=len)
                    conflicts.append({
                        'sku': sku,
                        'field': 'name',
                        'options': names,
                        'default': longest,
                        'sources': {
                            n: group.loc[group['name'] == n, 'marketplace'].iloc[0]
                            for n in names
                        },
                    })
                    chosen_name = longest
                elif len(names) == 1:
                    chosen_name = names[0]
                else:
                    chosen_name = sku  # fallback to SKU as name

                # --- Resolve HSN (any non-null wins; conflicts flagged) ---
                hsns = [h for h in group['hsn'].dropna().unique() if h]
                if len(hsns) > 1:
                    conflicts.append({
                        'sku': sku,
                        'field': 'hsn',
                        'options': hsns,
                        'default': hsns[0],
                        'sources': {
                            h: group.loc[group['hsn'] == h, 'marketplace'].iloc[0]
                            for h in hsns
                        },
                    })
                    chosen_hsn = hsns[0]
                elif len(hsns) == 1:
                    chosen_hsn = hsns[0]
                else:
                    chosen_hsn = None

                # --- Resolve GST rate (first non-null) ---
                gst_rates = [g for g in group['gst_rate'].dropna().unique()]
                chosen_gst = float(gst_rates[0]) if gst_rates else None

                # --- Resolve MRP (max value across marketplaces) ---
                mrps = group['mrp'].dropna()
                chosen_mrp = float(mrps.max()) if not mrps.empty else None

                # --- Resolve brand (first non-null) ---
                brands = [b for b in group['brand'].dropna().unique() if b]
                chosen_brand = brands[0] if brands else None

                # --- Resolve category (first non-null) ---
                cats = [c for c in group['category'].dropna().unique() if c]
                chosen_category = cats[0] if cats else None

                # --- Resolve dimensions (max non-null per axis) ---
                # Rationale: if Flipkart says 20cm and Meesho is NULL, use 20cm.
                # If both have values, use larger (safer for shipping calc).
                def _max_or_none(series):
                    vals = series.dropna()
                    return float(vals.max()) if not vals.empty else None

                chosen_l = _max_or_none(group['length_cm'])
                chosen_w = _max_or_none(group['width_cm'])
                chosen_h = _max_or_none(group['height_cm'])
                chosen_wt = _max_or_none(group['weight_kg'])

                grouped_rows.append({
                    'internal_sku': sku,
                    'name': chosen_name,
                    'brand': chosen_brand,
                    'category': chosen_category,
                    'mrp': chosen_mrp,
                    'hsn': chosen_hsn,
                    'gst_rate': chosen_gst,
                    'length_cm': chosen_l,
                    'width_cm': chosen_w,
                    'height_cm': chosen_h,
                    'weight_kg': chosen_wt,
                    'marketplaces': marketplaces,
                    'listing_count': len(group),
                })

            grouped_df = pd.DataFrame(grouped_rows)

            logger.info(
                f"✅ Auto-grouping complete: {len(grouped_df)} unique SKUs, "
                f"{len(conflicts)} conflict(s) flagged for review"
            )
            return grouped_df, conflicts

        except Exception as e:
            logger.error(f"Error in auto_group: {str(e)}", exc_info=True)
            raise ServiceException(f"Auto-grouping failed: {str(e)}") from e

    # =========================================================================
    # STEP 4: DIFF AGAINST EXISTING DB (for re-run idempotency)
    # =========================================================================

    def compute_diff(self, staging_df, grouped_df):
        """
        Compare staging data against existing DB to produce a diff report.

        Used in the wizard's "Diff Report" step on re-run, so the user can see
        what will change before committing.

        Args:
            staging_df: Original unified staging DataFrame (per-listing rows)
            grouped_df: Grouped product rows from auto_group()

        Returns:
            dict: {
                'new_products': int,
                'updated_products': int,
                'new_packs': int,
                'updated_packs': int,
                'new_listings': int,
                'price_changes': list[dict],  # detailed list for UI display
                'updated_listings': int,
                'unchanged_listings': int,
            }
        """
        try:
            logger.info("Computing diff against existing database...")

            with self.engine.connect() as conn:
                existing_products = pd.read_sql(
                    "SELECT sku, name, mrp, hsn FROM product_master", conn
                )
                existing_packs = pd.read_sql(
                    "SELECT pack_sku, final_l_cm, final_w_cm, final_h_cm, final_wt_kg "
                    "FROM pack_master",
                    conn,
                )
                existing_listings = pd.read_sql(
                    "SELECT channel_sku, marketplace, internal_sku, "
                    "selling_price, listing_status FROM channel_listings",
                    conn,
                )

            # --- Product diff ---
            existing_skus = set(existing_products['sku']) if not existing_products.empty else set()
            staging_skus = set(grouped_df['internal_sku']) if not grouped_df.empty else set()

            new_products = staging_skus - existing_skus
            updated_products = staging_skus & existing_skus

            # --- Pack diff (1:1 with products in our wizard model) ---
            existing_pack_skus = set(existing_packs['pack_sku']) if not existing_packs.empty else set()
            new_packs = staging_skus - existing_pack_skus
            updated_packs = staging_skus & existing_pack_skus

            # --- Listing diff (with price-change detection) ---
            existing_listings_keys = set()
            existing_listings_lookup = {}
            if not existing_listings.empty:
                for _, row in existing_listings.iterrows():
                    key = (row['channel_sku'], row['marketplace'])
                    existing_listings_keys.add(key)
                    existing_listings_lookup[key] = {
                        'selling_price': float(row['selling_price']) if pd.notna(row['selling_price']) else 0.0,
                        'listing_status': row['listing_status'],
                        'internal_sku': row['internal_sku'],
                    }

            new_listings = []
            price_changes = []
            updated_listings_count = 0
            unchanged_listings = 0

            for _, row in staging_df.iterrows():
                key = (row['channel_sku'], row['marketplace'])
                staging_price = float(row['selling_price']) if pd.notna(row['selling_price']) else 0.0

                if key not in existing_listings_keys:
                    new_listings.append({
                        'channel_sku': row['channel_sku'],
                        'marketplace': row['marketplace'],
                        'internal_sku': row['internal_sku'],
                        'selling_price': staging_price,
                    })
                else:
                    existing = existing_listings_lookup[key]
                    if abs(existing['selling_price'] - staging_price) > 0.01:
                        price_changes.append({
                            'channel_sku': row['channel_sku'],
                            'marketplace': row['marketplace'],
                            'internal_sku': row['internal_sku'],
                            'old_price': existing['selling_price'],
                            'new_price': staging_price,
                            'delta': round(staging_price - existing['selling_price'], 2),
                        })
                        updated_listings_count += 1
                    else:
                        unchanged_listings += 1

            # --- Pack dimension diff (only flag if dimensions changed) ---
            existing_pack_dims = {}
            if not existing_packs.empty:
                for _, row in existing_packs.iterrows():
                    existing_pack_dims[row['pack_sku']] = {
                        'l': row['final_l_cm'], 'w': row['final_w_cm'],
                        'h': row['final_h_cm'], 'wt': row['final_wt_kg'],
                    }

            dim_updates = []
            for _, row in grouped_df.iterrows():
                sku = row['internal_sku']
                if sku in existing_pack_dims:
                    old = existing_pack_dims[sku]
                    new_l, new_w, new_h, new_wt = (
                        row['length_cm'], row['width_cm'],
                        row['height_cm'], row['weight_kg'],
                    )
                    # Only flag if staging has a value AND it differs from existing
                    changes = []
                    if pd.notna(new_l) and (pd.isna(old['l']) or abs(float(old['l'] or 0) - new_l) > 0.01):
                        changes.append(f"L: {old['l']} → {new_l}")
                    if pd.notna(new_w) and (pd.isna(old['w']) or abs(float(old['w'] or 0) - new_w) > 0.01):
                        changes.append(f"W: {old['w']} → {new_w}")
                    if pd.notna(new_h) and (pd.isna(old['h']) or abs(float(old['h'] or 0) - new_h) > 0.01):
                        changes.append(f"H: {old['h']} → {new_h}")
                    if pd.notna(new_wt) and (pd.isna(old['wt']) or abs(float(old['wt'] or 0) - new_wt) > 0.001):
                        changes.append(f"Wt: {old['wt']} → {new_wt}")
                    if changes:
                        dim_updates.append({
                            'pack_sku': sku,
                            'changes': "; ".join(changes),
                        })

            diff = {
                'new_products': len(new_products),
                'updated_products': len(updated_products),
                'new_packs': len(new_packs),
                'updated_packs': len(updated_packs),
                'new_listings': len(new_listings),
                'updated_listings': updated_listings_count,
                'unchanged_listings': unchanged_listings,
                'price_changes': price_changes,
                'dim_updates': dim_updates,
                'new_product_skus': sorted(list(new_products)),
                'new_listing_keys': new_listings,
            }

            logger.info(
                f"✅ Diff computed: "
                f"{diff['new_products']} new products, "
                f"{diff['updated_products']} existing products, "
                f"{diff['new_listings']} new listings, "
                f"{len(price_changes)} price changes, "
                f"{len(dim_updates)} dimension updates"
            )
            return diff

        except Exception as e:
            logger.error(f"Error in compute_diff: {str(e)}", exc_info=True)
            raise ServiceException(f"Diff computation failed: {str(e)}") from e

    # =========================================================================
    # STEP 5: COMMIT (UPSERT)
    # =========================================================================

    def commit_catalog(self, staging_df, grouped_df, name_resolutions=None):
        """
        Commit catalog data via upsert.

        Idempotency rules:
            - product_master: name/mrp/hsn always updated; gst_rate/brand/category
              only filled if currently NULL (preserves user edits)
            - pack_master: dimensions only updated when staging has non-NULL values
              (COALESCE prevents wiping with NULL); quantity/packaging_cogs untouched
            - channel_listings: selling_price/listing_status always updated (price
              drift is real); internal_sku updated on remap

        Insert order matches bulk_service [4]:
            Products → Packs → Listings (FK dependency order)

        Args:
            staging_df: Original unified staging DataFrame (per-listing rows)
            grouped_df: Grouped product rows from auto_group()
            name_resolutions: Optional dict {sku: chosen_name} from conflict UI

        Returns:
            dict: Summary of commit operation
                  {products_upserted, packs_upserted, listings_upserted}
        """
        try:
            logger.info("Starting catalog commit (upsert)...")

            if grouped_df.empty:
                raise DataValidationException("No data to commit")

            name_resolutions = name_resolutions or {}
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            products_upserted = 0
            packs_upserted = 0
            listings_upserted = 0

            with self.engine.begin() as conn:
                # ---------- 1. PRODUCT_MASTER UPSERT ----------
                logger.debug("Upserting product_master rows...")
                for _, row in grouped_df.iterrows():
                    sku = row['internal_sku']
                    # Apply user's manual name resolution if provided
                    chosen_name = name_resolutions.get(sku, row['name']) or sku

                    conn.execute(text("""
                        INSERT INTO product_master (
                            sku, name, category, brand, lifecycle_status,
                            supplier, supplier_code,
                            mfg_cost, packaging_cost, labeling_labor,
                            inbound_transport, total_unit_cogs,
                            hsn, gst_rate, mrp
                        ) VALUES (
                            :sku, :name, :category, :brand, NULL,
                            NULL, NULL,
                            0, 0, 0,
                            0, 0,
                            :hsn, :gst_rate, :mrp
                        )
                        ON CONFLICT(sku) DO UPDATE SET
                            name = excluded.name,
                            mrp = excluded.mrp,
                            hsn = excluded.hsn,
                            -- Preserve user-edited fields via COALESCE
                            gst_rate = COALESCE(product_master.gst_rate, excluded.gst_rate),
                            brand = COALESCE(product_master.brand, excluded.brand),
                            category = COALESCE(product_master.category, excluded.category)
                    """), {
                        'sku': sku,
                        'name': chosen_name,
                        'category': row['category'] if pd.notna(row['category']) else None,
                        'brand': row['brand'] if pd.notna(row['brand']) else None,
                        'hsn': row['hsn'] if pd.notna(row['hsn']) else None,
                        'gst_rate': float(row['gst_rate']) if pd.notna(row['gst_rate']) else None,
                        'mrp': float(row['mrp']) if pd.notna(row['mrp']) else None,
                    })
                    products_upserted += 1

                logger.info(f"  ✔ product_master: {products_upserted} rows upserted")

                # ---------- 2. PACK_MASTER UPSERT ----------
                logger.debug("Upserting pack_master rows...")
                for _, row in grouped_df.iterrows():
                    sku = row['internal_sku']
                    conn.execute(text("""
                        INSERT INTO pack_master (
                            pack_sku, master_sku, quantity, packaging_cogs,
                            final_l_cm, final_w_cm, final_h_cm, final_wt_kg
                        ) VALUES (
                            :pack_sku, :master_sku, 1, 0,
                            :l, :w, :h, :wt
                        )
                        ON CONFLICT(pack_sku) DO UPDATE SET
                            -- Only update dims when staging has a value
                            -- (COALESCE prevents wiping existing values with NULL)
                            final_l_cm = COALESCE(excluded.final_l_cm, pack_master.final_l_cm),
                            final_w_cm = COALESCE(excluded.final_w_cm, pack_master.final_w_cm),
                            final_h_cm = COALESCE(excluded.final_h_cm, pack_master.final_h_cm),
                            final_wt_kg = COALESCE(excluded.final_wt_kg, pack_master.final_wt_kg)
                            -- master_sku, quantity, packaging_cogs NEVER touched on re-run
                    """), {
                        'pack_sku': sku,
                        'master_sku': sku,  # Wizard model: pack_sku == master_sku (1:1)
                        'l': float(row['length_cm']) if pd.notna(row['length_cm']) else None,
                        'w': float(row['width_cm']) if pd.notna(row['width_cm']) else None,
                        'h': float(row['height_cm']) if pd.notna(row['height_cm']) else None,
                        'wt': float(row['weight_kg']) if pd.notna(row['weight_kg']) else None,
                    })
                    packs_upserted += 1

                logger.info(f"  ✔ pack_master: {packs_upserted} rows upserted")

                # ---------- 3. CHANNEL_LISTINGS UPSERT ----------
                logger.debug("Upserting channel_listings rows...")
                for _, row in staging_df.iterrows():
                    selling_price = (
                        float(row['selling_price'])
                        if pd.notna(row['selling_price']) else 0.0
                    )
                    listing_status = (row['listing_status'] or 'ACTIVE').upper()

                    conn.execute(text("""
                        INSERT INTO channel_listings (
                            channel_sku, marketplace, internal_sku,
                            listing_status, selling_price,
                            channel_id, listing_url, last_updated, comment
                        ) VALUES (
                            :channel_sku, :marketplace, :internal_sku,
                            :listing_status, :selling_price,
                            '', '', :last_updated, ''
                        )
                        ON CONFLICT(channel_sku, marketplace) DO UPDATE SET
                            -- Always update price (price drift is real)
                            selling_price = excluded.selling_price,
                            listing_status = excluded.listing_status,
                            internal_sku = excluded.internal_sku,
                            last_updated = excluded.last_updated
                            -- channel_id, listing_url, comment NEVER touched
                            -- (preserves user-entered notes/URLs)
                    """), {
                        'channel_sku': row['channel_sku'],
                        'marketplace': row['marketplace'],
                        'internal_sku': row['internal_sku'],
                        'listing_status': listing_status,
                        'selling_price': selling_price,
                        'last_updated': now_str,
                    })
                    listings_upserted += 1

                logger.info(f"  ✔ channel_listings: {listings_upserted} rows upserted")

            # Transaction committed automatically via engine.begin() context manager

            # ---------- 4. INVALIDATE CACHES ----------
            # Critical: clear caches so dashboards see new data immediately [10]
            try:
                clear_all_caches()
                logger.debug("Caches cleared after commit")
            except Exception as e:
                # Cache failure should not fail the commit
                logger.warning(f"Cache clear failed (non-fatal): {str(e)}")

            summary = {
                'products_upserted': products_upserted,
                'packs_upserted': packs_upserted,
                'listings_upserted': listings_upserted,
            }
            logger.info(f"✅ Catalog commit complete: {summary}")
            return summary

        except DataValidationException:
            raise
        except Exception as e:
            logger.error(f"Error in commit_catalog: {str(e)}", exc_info=True)
            raise DatabaseException(f"Catalog commit failed: {str(e)}") from e

    # =========================================================================
    # CONVENIENCE: FULL FLOW IN ONE CALL (used by tests / CLI)
    # =========================================================================

    def run_full_onboarding(self, files_dict, name_resolutions=None):
        """
        Execute the complete onboarding flow end-to-end.

        Convenience wrapper used by tests or programmatic invocation. The UI
        wizard calls the individual steps separately to allow user interaction
        between steps (conflict resolution, diff review).

        Args:
            files_dict: {'amazon': file | None, 'flipkart': file | None,
                         'meesho': file | None}
            name_resolutions: Optional dict {sku: chosen_name}

        Returns:
            dict: {
                'staging_rows': int,
                'unique_skus': int,
                'conflicts': list,
                'diff': dict,
                'commit_summary': dict,
                'parse_errors': list[str],
            }
        """
        try:
            logger.info("Running full onboarding flow...")

            # Step 1+2: Parse
            staging_df, parse_errors = self.parse_and_stage(files_dict)
            if staging_df.empty:
                raise DataValidationException(
                    f"No data to onboard. Parse errors: {parse_errors}"
                )

            # Step 3: Auto-group
            grouped_df, conflicts = self.auto_group(staging_df)

            # Step 4: Diff (informational)
            diff = self.compute_diff(staging_df, grouped_df)

            # Step 5: Commit
            commit_summary = self.commit_catalog(
                staging_df, grouped_df, name_resolutions=name_resolutions
            )

            result = {
                'staging_rows': len(staging_df),
                'unique_skus': len(grouped_df),
                'conflicts': conflicts,
                'diff': diff,
                'commit_summary': commit_summary,
                'parse_errors': parse_errors,
            }
            logger.info(f"✅ Full onboarding complete: {result}")
            return result

        except (DataValidationException, DatabaseException, ServiceException):
            raise
        except Exception as e:
            logger.error(f"Error in run_full_onboarding: {str(e)}", exc_info=True)
            raise ServiceException(f"Full onboarding failed: {str(e)}") from e