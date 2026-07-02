"""First-Time Onboarding Wizard UI Page.

Bootstraps the catalog from marketplace listing/inventory files for new users
who don't have a Master Catalog Excel to migrate from.

Flow:
    Step 1: Upload Amazon / Flipkart / Meesho files
    Step 2: Parse & auto-group preview
    Step 3: Conflict resolution (only if conflicts detected)
    Step 4: Diff report (always shown — informational on first run, action-gating on re-run)
    Step 5: Commit + success summary

Idempotency:
    - First run: inserts everything
    - Re-run: upserts (new SKUs added, existing SKUs get price/dim updates,
      user-curated fields like COGS/category preserved)
"""
import streamlit as st
import pandas as pd
from src.core.services.onboarding_service import OnboardingService
from src.infrastructure.database import get_engine
from src.infrastructure.logger import get_logger
from src.ui.components.errors import show_error
from src.ui.cache_adapter import clear_all_caches
from sqlalchemy import text

logger = get_logger(__name__)


# =============================================================================
# SESSION STATE KEYS
# =============================================================================

SS_STEP = "onboarding_step"
SS_RAW_STAGING_DF = "onboarding_raw_staging_df"
SS_SIMILARITY_GROUPS = "onboarding_similarity_groups"
SS_SKU_MERGES = "onboarding_sku_merges"
SS_STAGING_DF = "onboarding_staging_df"
SS_GROUPED_DF = "onboarding_grouped_df"
SS_CONFLICTS = "onboarding_conflicts"
SS_RESOLUTIONS = "onboarding_resolutions"
SS_DIFF = "onboarding_diff"
SS_PARSE_ERRORS = "onboarding_parse_errors"
SS_COMMIT_SUMMARY = "onboarding_commit_summary"
SS_CAT_MAPPINGS = "onboarding_category_mappings"


def _init_state():
    """Initialize session state defaults for the wizard."""
    defaults = {
        SS_STEP: 1,
        SS_RAW_STAGING_DF: None,
        SS_SIMILARITY_GROUPS: [],
        SS_SKU_MERGES: {},
        SS_STAGING_DF: None,
        SS_GROUPED_DF: None,
        SS_CONFLICTS: [],
        SS_RESOLUTIONS: {},
        SS_DIFF: None,
        SS_PARSE_ERRORS: [],
        SS_COMMIT_SUMMARY: None,
        SS_CAT_MAPPINGS: {},
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def _reset_state():
    """Reset all wizard state — used on 'Start Over' button."""
    for k in [SS_STEP, SS_RAW_STAGING_DF, SS_SIMILARITY_GROUPS, SS_SKU_MERGES,
              SS_STAGING_DF, SS_GROUPED_DF, SS_CONFLICTS,
              SS_RESOLUTIONS, SS_DIFF, SS_PARSE_ERRORS, SS_COMMIT_SUMMARY, SS_CAT_MAPPINGS]:
        if k in st.session_state:
            del st.session_state[k]
    _init_state()


def _is_first_run():
    """Check if database is empty (true first-time onboarding)."""
    try:
        engine = get_engine()
        with engine.connect() as conn:
            count = conn.execute(text("SELECT COUNT(*) FROM product_master")).scalar() or 0
        return count == 0
    except Exception as e:
        logger.warning(f"Could not check first-run state: {e}")
        return False


# =============================================================================
# MAIN RENDER
# =============================================================================

def render():
    """Render the Onboarding Wizard page."""
    try:
        logger.info("Rendering Onboarding Wizard...")
        st.title("🪄 Onboarding Wizard")
        st.markdown(
            "Bootstrap your catalog by uploading marketplace listing files. "
            "We'll auto-group SKUs across marketplaces and let you confirm any conflicts."
        )

        _init_state()

        # Show first-run banner
        if _is_first_run():
            st.info(
                "👋 **Welcome!** Your database is empty. Upload your marketplace files "
                "below to get started. You can re-run this wizard later to add new SKUs "
                "or update prices."
            )
        else:
            st.success(
                "✅ Catalog already exists. Re-running the wizard will **add new SKUs** "
                "and **update changed fields** (like prices). Your manually-edited data "
                "(COGS, category, custom GST rates) will be preserved."
            )

        # Progress indicator
        _render_progress_bar()
        st.divider()

        # Route to current step
        step = st.session_state[SS_STEP]
        if step == 1:
            _render_step_1_upload()
        elif step == 2:
            _render_step_2_preview()
        elif step == 3:
            _render_step_3_category_mapping()
        elif step == 4:
            _render_step_4_merging()
        elif step == 5:
            _render_step_5_conflicts()
        elif step == 6:
            _render_step_6_diff()
        elif step == 7:
            _render_step_7_success()
        else:
            st.error(f"Unknown step: {step}")

        # Reset button always visible at bottom
        st.divider()
        col_a, col_b = st.columns([6, 1])
        with col_b:
            if st.button("🔄 Start Over", key="onboard_reset"):
                logger.info("User reset onboarding wizard")
                _reset_state()
                st.rerun()

    except Exception as e:
        show_error(logger, "Critical error in Onboarding Wizard", e)


# =============================================================================
# PROGRESS BAR
# =============================================================================

def _render_progress_bar():
    """Visual step indicator."""
    step = st.session_state[SS_STEP]
    steps = [
        (1, "📤 Upload"),
        (2, "🔍 Preview"),
        (3, "📂 Category Mapping"),
        (4, "🔗 SKU Merging"),
        (5, "⚖️ Conflicts"),
        (6, "📊 Diff Report"),
        (7, "✅ Done"),
    ]
    cols = st.columns(len(steps))
    for i, (num, label) in enumerate(steps):
        with cols[i]:
            if num < step:
                st.markdown(f"✅ **{label}**")
            elif num == step:
                st.markdown(f"🔵 **{label}**")
            else:
                st.markdown(f"⚪ {label}")


# =============================================================================
# STEP 1: UPLOAD
# =============================================================================

def _render_step_1_upload():
    """Step 1 — Upload marketplace files."""
    st.header("Step 1 — Upload Marketplace Files")
    st.markdown(
        "Upload listing/inventory exports from one or more marketplaces. "
        "All files are optional — upload whichever you have."
    )

    col1, col2, col3 = st.columns(3)
    with col1:
        st.markdown("**🟠 Amazon**")
        st.caption("Category Listings Report (.xlsx)")
        f_amazon = st.file_uploader(
            "Amazon file", type=['xlsx', 'xls'], key="onb_amazon",
            label_visibility="collapsed"
        )
        if f_amazon:
            st.success(f"📎 {f_amazon.name}")

    with col2:
        st.markdown("**🔵 Flipkart**")
        st.caption("Listing export (.xls)")
        f_flipkart = st.file_uploader(
            "Flipkart file", type=['xlsx', 'xls'], key="onb_flipkart",
            label_visibility="collapsed"
        )
        if f_flipkart:
            st.success(f"📎 {f_flipkart.name}")

    with col3:
        st.markdown("**🟣 Meesho**")
        st.caption("Inventory Update File (.xlsx)")
        f_meesho = st.file_uploader(
            "Meesho file", type=['xlsx', 'xls'], key="onb_meesho",
            label_visibility="collapsed"
        )
        if f_meesho:
            st.success(f"📎 {f_meesho.name}")

    st.divider()

    # Action button
    if st.button("🚀 Parse Files & Continue", type="primary", key="onb_parse_btn"):
        if not (f_amazon or f_flipkart or f_meesho):
            logger.warning("User clicked parse without uploading any files")
            st.error("Please upload at least one marketplace file.")
            return

        files_uploaded = []
        if f_amazon:
            files_uploaded.append("Amazon")
        if f_flipkart:
            files_uploaded.append("Flipkart")
        if f_meesho:
            files_uploaded.append("Meesho")
        logger.info(f"Onboarding parse triggered. Files: {', '.join(files_uploaded)}")

        with st.spinner("Parsing files and grouping SKUs..."):
            try:
                service = OnboardingService()
                files_dict = {
                    'amazon': f_amazon,
                    'flipkart': f_flipkart,
                    'meesho': f_meesho,
                }
                staging_df, parse_errors = service.parse_and_stage(files_dict)

                if staging_df.empty:
                    logger.error(f"Parse produced no rows. Errors: {parse_errors}")
                    st.error("❌ No data could be parsed from the uploaded files.")
                    if parse_errors:
                        for err in parse_errors:
                            st.error(f"• {err}")
                    return

                # Auto-group (draft, before similarity merges)
                grouped_df, conflicts = service.auto_group(staging_df)

                # Detect similarity groups
                similarity_groups = service.detect_similarity_groups(staging_df)

                # Stash in session state
                st.session_state[SS_RAW_STAGING_DF] = staging_df.copy()
                st.session_state[SS_SIMILARITY_GROUPS] = similarity_groups
                st.session_state[SS_SKU_MERGES] = {i: True for i in range(len(similarity_groups))}
                st.session_state[SS_STAGING_DF] = staging_df
                st.session_state[SS_GROUPED_DF] = grouped_df
                st.session_state[SS_CONFLICTS] = conflicts
                st.session_state[SS_PARSE_ERRORS] = parse_errors

                # Pre-seed resolutions with defaults (longest name)
                st.session_state[SS_RESOLUTIONS] = {
                    c['sku']: c['default']
                    for c in conflicts if c['field'] == 'name'
                }

                # Advance to step 2
                st.session_state[SS_STEP] = 2
                logger.info(
                    f"✅ Parse complete: {len(staging_df)} listings, "
                    f"{len(grouped_df)} unique SKUs, {len(conflicts)} conflicts, "
                    f"{len(similarity_groups)} similarity groups detected"
                )
                st.rerun()

            except Exception as e:
                show_error(logger, "Parsing failed", e)


# =============================================================================
# STEP 2: PREVIEW
# =============================================================================

def _render_step_2_preview():
    """Step 2 — Preview parsed data and auto-grouping summary."""
    st.header("Step 2 — Parse & Auto-Group Preview")

    staging_df = st.session_state[SS_STAGING_DF]
    grouped_df = st.session_state[SS_GROUPED_DF]
    conflicts = st.session_state[SS_CONFLICTS]
    parse_errors = st.session_state[SS_PARSE_ERRORS]

    if staging_df is None or staging_df.empty:
        st.error("No staging data found. Please go back and re-upload.")
        if st.button("⬅️ Back to Upload"):
            st.session_state[SS_STEP] = 1
            st.rerun()
        return

    # Show parse errors as warnings (non-fatal)
    if parse_errors:
        with st.expander(f"⚠️ {len(parse_errors)} file(s) had parse warnings", expanded=False):
            for err in parse_errors:
                st.warning(err)

    # Summary metrics
    # Summary metrics
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total Listings Parsed", len(staging_df))
    col2.metric("Unique SKUs", len(grouped_df))
    col3.metric("Marketplaces", staging_df['marketplace'].nunique())
    col4.metric("⚠️ Conflicts", len(conflicts), delta_color="inverse")

    st.divider()

    # Per-marketplace breakdown
    st.subheader("📊 Per-Marketplace Breakdown")
    mkt_summary = (
        staging_df.groupby('marketplace')
        .agg(
            listings=('internal_sku', 'count'),
            unique_skus=('internal_sku', 'nunique'),
            avg_price=('selling_price', 'mean'),
        )
        .reset_index()
    )
    mkt_summary['avg_price'] = mkt_summary['avg_price'].round(2)
    st.dataframe(mkt_summary, hide_index=True, width='stretch')

    st.divider()

    # Cross-marketplace coverage analysis
    st.subheader("🔗 Cross-Marketplace Coverage")
    coverage_df = grouped_df.copy()
    coverage_df['marketplace_count'] = coverage_df['marketplaces'].apply(len)
    coverage_df['marketplaces_str'] = coverage_df['marketplaces'].apply(lambda x: ", ".join(x))

    cov_summary = (
        coverage_df.groupby('marketplace_count')
        .size()
        .reset_index(name='sku_count')
        .rename(columns={'marketplace_count': 'Listed On (# Marketplaces)'})
    )
    st.dataframe(cov_summary, hide_index=True, width='stretch')

    with st.expander("🔍 View grouped SKU details", expanded=False):
        display_df = coverage_df[[
            'internal_sku', 'name', 'marketplaces_str', 'listing_count',
            'mrp', 'gst_rate', 'weight_kg'
        ]].copy()
        display_df.columns = [
            'Internal SKU', 'Product Name', 'Marketplaces', 'Listings',
            'MRP', 'GST %', 'Weight (kg)'
        ]
        st.dataframe(display_df, hide_index=True, width='stretch', height=400)

    st.divider()

    # Navigation buttons
    col_back, col_spacer, col_next = st.columns([1, 4, 1])
    with col_back:
        if st.button("⬅️ Back", key="onb_step2_back"):
            st.session_state[SS_STEP] = 1
            st.rerun()
    with col_next:
        if st.button("📂 Category Mapping ➡️", type="primary", key="onb_step2_next"):
            st.session_state[SS_STEP] = 3
            logger.info("Advancing to step 3 (Category Mapping)")
            st.rerun()


# =============================================================================
# STEP 3: CONFLICT RESOLUTION
# =============================================================================

# =============================================================================
# STEP 3: SKU SIMILARITY MERGING (NEW)
# =============================================================================

def _render_step_4_merging():
    """Step 4 — SKU Similarity Merging."""
    st.header("Step 4 — SKU Similarity Merging")
    st.markdown(
        "We compared SKU strings across platforms and identified potential duplicates. "
        "Choose which groups of similar SKUs you want to merge into a single base product. "
        "If merged, all listings will map to the **shortest SKU** string, which resolves duplicates."
    )

    similarity_groups = st.session_state.get(SS_SIMILARITY_GROUPS, [])
    staging_df_base = st.session_state[SS_STAGING_DF]
    raw_staging_df = st.session_state[SS_RAW_STAGING_DF]

    if not similarity_groups:
        st.success("🎉 No highly similar SKUs detected across platforms. You can proceed directly!")
        if st.button("Continue to Conflicts/Diff ➡️", type="primary", key="onb_step4_no_similar"):
            # If no similarity groups, we just use the raw staging df as is
            st.session_state[SS_STAGING_DF] = staging_df_base.copy()
            # Run auto_group and compute_diff
            with st.spinner("Analyzing products..."):
                service = OnboardingService()
                grouped_df, conflicts = service.auto_group(staging_df_base)
                diff = service.compute_diff(staging_df_base, grouped_df)

                st.session_state[SS_GROUPED_DF] = grouped_df
                st.session_state[SS_CONFLICTS] = conflicts
                st.session_state[SS_DIFF] = diff

                # Pre-seed resolutions with defaults (longest name)
                st.session_state[SS_RESOLUTIONS] = {
                    c['sku']: c['default']
                    for c in conflicts if c['field'] == 'name'
                }

                st.session_state[SS_STEP] = 5 if conflicts else 6
                st.rerun()
        return

    st.markdown(f"Found **{len(similarity_groups)}** group(s) of similar SKUs. Select which ones to merge:")

    sku_merges = st.session_state.get(SS_SKU_MERGES, {})

    for idx, group in enumerate(similarity_groups):
        shortest = group['shortest']
        all_skus = group['all_skus']
        marketplaces = group['marketplaces']

        with st.container(border=True):
            st.markdown(f"### 🔗 Group {idx + 1}: Base Product SKU `{shortest}`")
            st.markdown(f"**Marketplaces involved:** {', '.join(marketplaces)}")
            
            # Show a nice list of matching SKUs
            st.markdown("**SKU variations in this group:**")
            for sku in all_skus:
                mkt_for_sku = sorted(raw_staging_df[raw_staging_df['internal_sku'] == sku]['marketplace'].unique().tolist())
                st.markdown(f"- `{sku}` (from {', '.join(mkt_for_sku)})")

            # Checkbox to merge or keep separate (checked by default)
            sku_merges[idx] = st.checkbox(
                f"Merge all these into `{shortest}` (recommended)",
                value=sku_merges.get(idx, True),
                key=f"merge_group_{idx}"
            )

    st.session_state[SS_SKU_MERGES] = sku_merges

    st.divider()

    col_back, col_spacer, col_next = st.columns([1, 4, 1])
    with col_back:
        if st.button("⬅️ Back", key="onb_step4_back"):
            st.session_state[SS_STEP] = 3
            st.rerun()
    with col_next:
        if st.button("Continue ➡️", type="primary", key="onb_step4_next"):
            # Apply merges to staging_df
            staging_df = staging_df_base.copy()
            merged_count = 0
            
            for idx, group in enumerate(similarity_groups):
                if sku_merges.get(idx, True):
                    shortest = group['shortest']
                    all_skus = group['all_skus']
                    # Overwrite internal_sku for all skus in this group to be the shortest one
                    staging_df.loc[staging_df['internal_sku'].isin(all_skus), 'internal_sku'] = shortest
                    merged_count += len(all_skus) - 1

            st.session_state[SS_STAGING_DF] = staging_df
            logger.info(f"SKU Similarity Merging complete: mapped {merged_count} duplicates to base SKUs")

            # Re-run auto_group, conflicts and diff computation on the merged staging_df
            with st.spinner("Analyzing merged products and conflicts..."):
                service = OnboardingService()
                grouped_df, conflicts = service.auto_group(staging_df)
                diff = service.compute_diff(staging_df, grouped_df)

                st.session_state[SS_GROUPED_DF] = grouped_df
                st.session_state[SS_CONFLICTS] = conflicts
                st.session_state[SS_DIFF] = diff

                # Pre-seed resolutions with defaults (longest name)
                st.session_state[SS_RESOLUTIONS] = {
                    c['sku']: c['default']
                    for c in conflicts if c['field'] == 'name'
                }

                # Advance to Step 5 (Conflicts) or Step 6 (Diff Report)
                st.session_state[SS_STEP] = 5 if conflicts else 6
                st.rerun()


# =============================================================================
# STEP 3: CATEGORY MAPPING
# =============================================================================

def _render_step_3_category_mapping():
    """Step 3 — Interactive Category Mapping."""
    st.header("Step 3 — Category Mapping")
    st.markdown(
        "Map raw categories from your marketplace files to your configured rules. "
        "This ensures proper commission and closing fee calculations for your products."
    )

    staging_df = st.session_state[SS_STAGING_DF]
    if staging_df is None or staging_df.empty:
        st.error("No staging data found. Please go back and re-upload.")
        if st.button("⬅️ Back to Upload"):
            st.session_state[SS_STEP] = 1
            st.rerun()
        return

    # Load pricing rules to get available categories per marketplace
    from src.infrastructure.config_rules import load_excel_sheet, save_excel_sheet
    pricing_rules = load_excel_sheet("Pricing_Rules")

    # Clean categories (strip & fillna)
    staging_df['category'] = staging_df['category'].fillna("Other").astype(str).str.strip()
    
    # Get unique (marketplace, category) pairs
    unique_pairs = sorted(staging_df[['marketplace', 'category']].drop_duplicates().values.tolist())

    if not unique_pairs:
        st.success("🎉 No categories found in your uploads. Proceeding...")
        if st.button("Continue ➡️", type="primary", key="onb_step3_cat_no_pairs"):
            st.session_state[SS_STEP] = 4  # Advance to SKU Merging
            st.rerun()
        return

    st.subheader("Discovered Categories & Mappings")
    
    # Display each pair inside a container
    for mkt, raw_cat in unique_pairs:
        with st.container(border=True):
            col_info, col_select = st.columns([1, 1])
            with col_info:
                st.markdown(f"**Marketplace:** {mkt}")
                st.markdown(f"**Raw Category:** `{raw_cat}`")
                # Count of listings affected
                count = len(staging_df[(staging_df['marketplace'] == mkt) & (staging_df['category'] == raw_cat)])
                st.caption(f"Affects {count} listing(s)")

            # Get available categories for this marketplace in configured rules
            mkt_rules = pricing_rules[pricing_rules['marketplace'] == mkt]
            available_cats = sorted(mkt_rules['category_ref'].unique().tolist())
            if "Other" not in available_cats:
                available_cats.append("Other")

            # Determine best fuzzy default mapping
            from src.core.services.finance_service import map_fuzzy_category
            fuzzy_default = map_fuzzy_category(raw_cat)
            
            # Find default index
            default_idx = 0
            if fuzzy_default in available_cats:
                default_idx = available_cats.index(fuzzy_default)
            elif "Other" in available_cats:
                default_idx = available_cats.index("Other")

            options = available_cats + ["[+] Create New Category Config..."]
            
            with col_select:
                selected_opt = st.selectbox(
                    f"Select configured category for `{raw_cat}` ({mkt}):",
                    options=options,
                    index=default_idx,
                    key=f"cat_map_{mkt}_{raw_cat}"
                )

            # If user wants to create a new category config on the fly
            if selected_opt == "[+] Create New Category Config...":
                with st.expander("➕ Create New Pricing Category Rule", expanded=True):
                    new_cat_name = st.text_input(
                        "Category Name",
                        value=raw_cat,
                        key=f"new_name_{mkt}_{raw_cat}"
                    )
                    col_ref, col_close = st.columns(2)
                    with col_ref:
                        referral_fee_pct = st.number_input(
                            "Referral Fee %",
                            min_value=0.0,
                            max_value=100.0,
                            value=15.0,
                            step=0.5,
                            key=f"new_ref_{mkt}_{raw_cat}"
                        )
                    with col_close:
                        closing_fee = st.number_input(
                            "Closing Fee (₹)",
                            min_value=0.0,
                            max_value=1000.0,
                            value=0.0,
                            step=1.0,
                            key=f"new_close_{mkt}_{raw_cat}"
                        )

                    if st.button("➕ Add Category Rule", key=f"add_rule_btn_{mkt}_{raw_cat}"):
                        new_cat_name = new_cat_name.strip()
                        if not new_cat_name:
                            st.error("Category name cannot be empty.")
                        elif new_cat_name in available_cats:
                            st.error(f"Category '{new_cat_name}' already exists in config for {mkt}.")
                        else:
                            with st.spinner("Appending new rule to Excel..."):
                                new_rule = {
                                    "marketplace": mkt,
                                    "category_ref": new_cat_name,
                                    "min_price": 0.0,
                                    "max_price": 99999.0,
                                    "referral_fee_pct": referral_fee_pct / 100.0,
                                    "closing_fee_inr": closing_fee
                                }
                                pricing_rules = pd.concat([pricing_rules, pd.DataFrame([new_rule])], ignore_index=True)
                                save_excel_sheet("Pricing_Rules", pricing_rules)
                                st.success(f"Category '{new_cat_name}' successfully added to {mkt} pricing rules!")
                                # Pre-select the newly added category by setting session state key directly before rerun
                                st.session_state[f"cat_map_{mkt}_{raw_cat}"] = new_cat_name
                                st.rerun()

    st.divider()

    # Back / Continue buttons
    col_back, col_spacer, col_next = st.columns([1, 4, 1])
    with col_back:
        if st.button("⬅️ Back", key="onb_step3_cat_back"):
            st.session_state[SS_STEP] = 2
            st.rerun()
    with col_next:
        if st.button("Continue ➡️", type="primary", key="onb_step3_cat_next"):
            # Gather mappings
            final_mappings = {}
            for mkt, raw_cat in unique_pairs:
                val = st.session_state.get(f"cat_map_{mkt}_{raw_cat}")
                if val and val != "[+] Create New Category Config...":
                    final_mappings[(mkt, raw_cat)] = val
                else:
                    final_mappings[(mkt, raw_cat)] = "Other"

            # Apply mapping to staging_df
            mapped_staging_df = staging_df.copy()
            for (mkt, raw_cat), mapped_cat in final_mappings.items():
                mapped_staging_df.loc[
                    (mapped_staging_df['marketplace'] == mkt) & (mapped_staging_df['category'] == raw_cat),
                    'category'
                ] = mapped_cat

            st.session_state[SS_STAGING_DF] = mapped_staging_df
            logger.info(f"Category mapping completed for {len(final_mappings)} categories")

            # Advance to Step 4 (SKU Merging)
            st.session_state[SS_STEP] = 4
            st.rerun()


# =============================================================================
# STEP 5: CONFLICT RESOLUTION
# =============================================================================

def _render_step_5_conflicts():
    """Step 5 — Resolve name/HSN conflicts via radio selection."""
    st.header("Step 5 — Resolve Conflicts")

    conflicts = st.session_state[SS_CONFLICTS]

    if not conflicts:
        st.success("✅ No conflicts detected! Proceeding to diff report...")
        st.session_state[SS_STEP] = 6
        st.rerun()
        return

    st.markdown(
        f"Found **{len(conflicts)}** SKU(s) with mismatched data across marketplaces. "
        "The longest name is pre-selected as default — click to switch if needed."
    )

    # Group conflicts by SKU for clean UI
    conflicts_by_sku = {}
    for c in conflicts:
        conflicts_by_sku.setdefault(c['sku'], []).append(c)

    resolutions = st.session_state.get(SS_RESOLUTIONS, {})

    for sku, sku_conflicts in conflicts_by_sku.items():
        with st.container(border=True):
            st.markdown(f"### 🔖 SKU: `{sku}`")

            for conflict in sku_conflicts:
                field = conflict['field']
                options = conflict['options']
                default = conflict['default']
                sources = conflict.get('sources', {})

                st.markdown(f"**Field:** `{field}`")

                # Build radio options with source annotation
                option_labels = []
                for opt in options:
                    src = sources.get(opt, "?")
                    char_count = len(str(opt))
                    if field == 'name':
                        label = f"[{src} | {char_count} chars] {opt}"
                    else:
                        label = f"[{src}] {opt}"
                    option_labels.append(label)

                # Find default index
                try:
                    default_idx = options.index(default)
                except ValueError:
                    default_idx = 0

                chosen_label = st.radio(
                     f"Select value for {field}:",
                     options=option_labels,
                     index=default_idx,
                     key=f"conflict_{sku}_{field}",
                     label_visibility="collapsed",
                )

                # Map label back to actual value
                chosen_idx = option_labels.index(chosen_label)
                chosen_value = options[chosen_idx]

                # Only store name resolutions (other conflicts auto-resolved silently)
                if field == 'name':
                    resolutions[sku] = chosen_value

    st.session_state[SS_RESOLUTIONS] = resolutions

    st.divider()

    col_back, col_spacer, col_next = st.columns([1, 4, 1])
    with col_back:
        if st.button("⬅️ Back", key="onb_step5_back_conf"):
            st.session_state[SS_STEP] = 4
            st.rerun()
    with col_next:
        if st.button("📊 View Diff Report ➡️", type="primary", key="onb_step5_next_conf"):
            logger.info(f"Resolutions captured for {len(resolutions)} SKUs")
            st.session_state[SS_STEP] = 6
            st.rerun()


# =============================================================================
# STEP 6: DIFF REPORT
# =============================================================================

def _render_step_6_diff():
    """Step 6 — Show diff against existing DB before commit."""
    st.header("Step 6 — Diff Report")

    diff = st.session_state[SS_DIFF]
    if not diff:
        st.error("No diff data found. Please go back and re-parse.")
        if st.button("⬅️ Back to Upload"):
            st.session_state[SS_STEP] = 1
            st.rerun()
        return

    is_first = _is_first_run()
    if is_first:
        st.info("🆕 **First-time setup** — all rows below will be inserted fresh.")
    else:
        st.warning(
            "🔄 **Re-running wizard** — review changes carefully before committing. "
            "User-curated fields (COGS, category, custom GST rates) will be preserved."
        )

    # Top-line metrics
    st.subheader("📈 Change Summary")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("🆕 New Products", diff['new_products'])
    m2.metric("🔄 Updated Products", diff['updated_products'])
    m3.metric("🆕 New Listings", diff['new_listings'])
    m4.metric("⏭️ Unchanged Listings", diff['unchanged_listings'])

    n1, n2, n3 = st.columns(3)
    n1.metric("💰 Price Changes", len(diff['price_changes']), delta_color="off")
    n2.metric("📐 Dimension Updates", len(diff['dim_updates']), delta_color="off")
    n3.metric("📦 New Packs", diff['new_packs'])

    st.divider()

    # Detailed price changes (most actionable on re-run)
    if diff['price_changes']:
        st.subheader("💰 Price Changes")
        st.caption("These prices will be updated in channel_listings:")
        pc_df = pd.DataFrame(diff['price_changes'])
        pc_df['old_price'] = pc_df['old_price'].apply(lambda x: f"₹{x:.2f}")
        pc_df['new_price'] = pc_df['new_price'].apply(lambda x: f"₹{x:.2f}")
        pc_df['delta'] = pc_df['delta'].apply(
            lambda x: f"+₹{x:.2f}" if x >= 0 else f"-₹{abs(x):.2f}"
        )
        pc_df.columns = ['Channel SKU', 'Marketplace', 'Internal SKU',
                         'Old Price', 'New Price', 'Delta']
        st.dataframe(pc_df, hide_index=True, width='stretch', height=300)

    # Dimension updates
    if diff['dim_updates']:
        with st.expander(f"📐 Dimension updates ({len(diff['dim_updates'])} packs)", expanded=False):
            du_df = pd.DataFrame(diff['dim_updates'])
            du_df.columns = ['Pack SKU', 'Changes']
            st.dataframe(du_df, hide_index=True, width='stretch')

    # New SKUs preview
    if diff['new_product_skus']:
        with st.expander(f"🆕 New SKUs to be added ({len(diff['new_product_skus'])})", expanded=False):
            st.code("\n".join(diff['new_product_skus'][:50]))
            if len(diff['new_product_skus']) > 50:
                st.caption(f"...and {len(diff['new_product_skus']) - 50} more")

    st.divider()

    # Defaults disclaimer
    with st.expander("ℹ️ Field defaults & preservation rules", expanded=False):
        st.markdown("""
        **For NEW products, these fields will default to:**
        - `category` → NULL (fill in Data Manager later)
        - `mfg_cost`, `total_unit_cogs`, `packaging_cost` → 0
        - `gst_rate` → parsed from marketplace tax codes if available, else NULL
        - `brand` → from marketplace data if available, else NULL

        **For EXISTING products on re-run, these are PRESERVED (never overwritten):**
        - `category`, `mfg_cost`, `total_unit_cogs`, all other COGS fields
        - `gst_rate` and `brand` (only filled if currently NULL)
        - `pack_master.quantity`, `packaging_cogs`
        - `channel_listings.channel_id`, `listing_url`, `comment`

        **For EXISTING products on re-run, these ARE updated:**
        - `name`, `mrp`, `hsn` from marketplace data
        - `selling_price`, `listing_status` (price drift tracking)
        - Pack dimensions (only if marketplace has new values; never wiped to NULL)
        """)

    st.divider()

    # Final confirmation
    col_back, col_spacer, col_commit = st.columns([1, 3, 2])
    with col_back:
        if st.button("⬅️ Back", key="onb_step6_back"):
            st.session_state[SS_STEP] = 5 if st.session_state[SS_CONFLICTS] else 4
            st.rerun()
    with col_commit:
        commit_label = "✅ Commit to Database" if is_first else "✅ Apply Changes"
        if st.button(commit_label, type="primary", key="onb_step6_commit"):
            _execute_commit()


# =============================================================================
# STEP 7: SUCCESS
# =============================================================================

def _render_step_7_success():
    """Step 7 — Show commit summary and next-step guidance."""
    st.header("Step 7 — Onboarding Complete! 🎉")

    summary = st.session_state.get(SS_COMMIT_SUMMARY)
    if not summary:
        st.error("No commit summary found. Something went wrong.")
        if st.button("⬅️ Back to Start"):
            _reset_state()
            st.rerun()
        return

    st.success("✅ Your catalog has been successfully bootstrapped!")

    # Commit summary metrics
    st.subheader("📦 What Was Saved")
    c1, c2, c3 = st.columns(3)
    c1.metric("Products Upserted", summary.get('products_upserted', 0))
    c2.metric("Packs Upserted", summary.get('packs_upserted', 0))
    c3.metric("Listings Upserted", summary.get('listings_upserted', 0))

    st.divider()

    # Next-step guidance
    st.subheader("🎯 Recommended Next Steps")
    st.markdown("""
    Your catalog is now bootstrapped, but several fields are intentionally blank
    or zero. To unlock the full ERP capabilities, complete these tasks:

    1. **💰 Fill in COGS** — Open **Data Manager → Products** and enter
       `mfg_cost`, `packaging_cost`, `labeling_labor`, and `inbound_transport`
       for each product. Without these, the Profit Dashboard will show -100% margins.

    2. **📂 Set Categories** — Categories are required for pricing rule lookup
       in the Profit Dashboard. Pick from the categories defined in your
       Pricing Rules table.

    3. **📊 Verify GST Rates** — Marketplace files don't always provide tax codes.
       Review products with NULL `gst_rate` and fill them in (5%, 12%, 18%, etc.).

    4. **📐 Add Missing Dimensions** — Meesho-only SKUs won't have package
       dimensions. Add them in **Data Manager → Packs** so shipping fees can
       be calculated correctly.

    5. **📥 Update Inventory** — Use **Inventory Manager → Bulk Update** to
       enter your current godown stock counts.

    6. **🔍 Review Profit Dashboard** — Once COGS and GST are filled, head to
       the Profit Dashboard to see your true margins.
    """)

    st.divider()

    # Action buttons
    col_a, col_b, col_c = st.columns(3)
    with col_a:
        if st.button("📂 Go to Data Manager", key="onb_goto_dm"):
            logger.info("User navigating from wizard to Data Manager")
            st.info("👈 Use the sidebar to navigate to Data Manager")
    with col_b:
        if st.button("💰 Go to Profit Dashboard", key="onb_goto_pd"):
            logger.info("User navigating from wizard to Profit Dashboard")
            st.info("👈 Use the sidebar to navigate to Profit Dashboard")
    with col_c:
        if st.button("🔄 Run Wizard Again", key="onb_run_again"):
            logger.info("User restarting wizard")
            _reset_state()
            st.rerun()


# =============================================================================
# COMMIT EXECUTION (called from Step 6)
# =============================================================================

def _execute_commit():
    """Execute the catalog commit and advance to success step."""
    logger.info("User confirmed commit. Executing upsert...")

    staging_df = st.session_state[SS_STAGING_DF]
    grouped_df = st.session_state[SS_GROUPED_DF]
    resolutions = st.session_state.get(SS_RESOLUTIONS, {})

    if staging_df is None or grouped_df is None:
        st.error("❌ Session state lost. Please start over.")
        logger.error("Commit attempted with missing session state")
        return

    with st.spinner("Committing to database..."):
        try:
            service = OnboardingService()
            summary = service.commit_catalog(
                staging_df=staging_df,
                grouped_df=grouped_df,
                name_resolutions=resolutions,
            )

            # Cache invalidation so dashboards see new data immediately.
            # Non-fatal: a cache-clear failure shouldn't hide a successful commit.
            try:
                clear_all_caches()
                logger.debug("Caches cleared after commit")
            except Exception as cache_err:
                logger.warning(f"Cache clear failed (non-fatal): {cache_err}")

            st.session_state[SS_COMMIT_SUMMARY] = summary
            st.session_state[SS_STEP] = 7
            logger.info(f"✅ Commit successful: {summary}")
            st.rerun()

        except Exception as e:
            show_error(logger, "Commit failed", e)
            st.info(
                "Your data was NOT saved due to the error above. "
                "You can retry by clicking the Commit button again, or "
                "click 'Start Over' to upload different files."
            )