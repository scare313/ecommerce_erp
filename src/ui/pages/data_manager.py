"""Master Data Manager UI Page.

Provides interfaces for managing the product catalog including:
- Product Master: Base product definitions with costs
- Pack Master: Packable variants and combinations
- Channel Listings: Map packs to marketplaces
- Configuration: Pricing and shipping rules
- Bulk Operations: Excel import/export
"""
import streamlit as st
import pandas as pd
from sqlalchemy import text
from src.core.services.catalog_service import CatalogService
from src.core.services.bulk_service import BulkService
from src.core.services.supplier_service import SupplierService
from src.infrastructure.database import get_engine
from src.infrastructure.logger import get_logger
from src.ui.components.errors import show_error
from src.ui.cache_adapter import clear_catalog_cache, clear_all_caches
from src.ui.cache_adapter import get_marketplaces
from src.core.data_access import set_rules_last_verified, get_rules_last_verified

logger = get_logger(__name__)


def render():
    """Render the Data Manager UI page."""
    try:
        logger.info("Rendering Data Manager page...")
        st.title("🗂️ Catalog")
        
        try:
            service = CatalogService()
            bulk_service = BulkService()
            engine = get_engine()
            logger.debug("Services initialized successfully")
        except Exception as e:
            show_error(logger, "Failed to initialize services", e)
            return

        tab1, tab2, tab3, tab4, tab5 = st.tabs(["1. Products", "2. Packs", "3. Listings", "⚙️ Config & Rules", "📥 Bulk Operations"])

        with tab1:
            st.subheader("Define Base Products")
            with st.expander("➕ Add New Product", expanded=False):
                # Load supplier dropdown BEFORE the form so we can conditionally
                # disable the submit button based on whether any active suppliers exist.
                try:
                    supplier_service = SupplierService()
                    supplier_options = supplier_service.get_supplier_dropdown()
                except Exception as e:
                    logger.error(f"Error fetching suppliers: {str(e)}", exc_info=True)
                    supplier_options = []

                if not supplier_options:
                    st.warning(
                        "⚠️ No active suppliers found. "
                        "Add suppliers in **Supplier Master** before adding products."
                    )

                with st.form("new_product_form"):
                    c1, c2, c3 = st.columns(3)
                    sku = c1.text_input("Master SKU (Unique)", placeholder="TSHIRT-BLK-L")
                    name = c2.text_input("Product Name")

                    try:
                        cat_options = service.get_category_dropdown()
                        category = c3.selectbox("Category (from Rules)", options=cat_options)
                    except Exception as e:
                        logger.error(f"Error fetching categories: {str(e)}", exc_info=True)
                        st.error("Unable to load categories. Please refresh the page.")
                        category = None

                    c4, c5, c6 = st.columns(3)
                    mfg_cost = c4.number_input("Mfg Cost", min_value=0.0)
                    pkg_cost = c5.number_input("Unit Box Cost", min_value=0.0)
                    labor = c6.number_input("Labor Cost", min_value=0.0)

                    c7, c8 = st.columns(2)
                    gst = c7.number_input("GST Rate (%)", value=5.0)
                    status = c8.selectbox("Status", ["Active", "Discontinued"])

                    # Supplier selectbox — replaces the previous supplier / supplier_code
                    # freetext inputs. Active suppliers only. If the list is empty the
                    # submit button is disabled so the user cannot bypass this requirement.
                    if supplier_options:
                        supplier_labels = [s["label"] for s in supplier_options]
                        selected_supplier_label = st.selectbox(
                            "Supplier *",
                            options=supplier_labels,
                            key="product_supplier",
                        )
                        # Resolve label back to code using original list order
                        selected_idx = supplier_labels.index(selected_supplier_label)
                        selected_supplier_code = supplier_options[selected_idx]["supplier_code"]
                    else:
                        # Render a disabled placeholder so form layout is consistent
                        st.selectbox(
                            "Supplier *",
                            options=["— No active suppliers —"],
                            disabled=True,
                            key="product_supplier_disabled",
                        )
                        selected_supplier_code = None

                    supplier_product_code = st.text_input(
                        "Supplier Product Code",
                        placeholder="e.g. RTS-2024-BLK-L",
                        help="The code this supplier uses for this item. Optional — can be added later.",
                        key="product_supplier_product_code",
                    )

                    if st.form_submit_button("Create Product", disabled=not supplier_options):
                        try:
                            final_name = name if name.strip() else sku

                            service.add_product({
                                "sku": sku, "name": final_name, "category": category,
                                "mfg_cost": mfg_cost, "packaging_cost": pkg_cost,
                                "labeling_labor": labor, "gst_rate": gst,
                                "lifecycle_status": status,
                                "supplier_code": selected_supplier_code,
                                "supplier_product_code": supplier_product_code,
                            })
                            clear_catalog_cache()
                            logger.info(f"Product created successfully: {sku}")
                            st.success(f"Created {sku}")
                            st.rerun()
                        except Exception as e:
                            show_error(logger, f"Error creating product {sku}", e)

            # View/Edit Grid
            try:
                df_prod = pd.read_sql("SELECT * FROM product_master", engine)
                edited_prod = st.data_editor(df_prod, key="editor_prod", num_rows="dynamic")
                if st.button("Save Product Changes"):
                    try:
                        with engine.connect() as conn:
                            edited_prod.to_sql('product_master', conn, if_exists='replace', index=False)
                            conn.commit()
                        logger.info(f"Product changes saved: {len(edited_prod)} rows")
                        st.success("Saved!")
                    except Exception as e:
                        show_error(logger, "Error saving product changes", e)
            except Exception as e:
                show_error(logger, "Error loading products", e)

        # --- TAB 2: PACK MASTER ---
        with tab2:
            st.subheader("Create Sellable Packs")
            with st.expander("➕ Add New Pack", expanded=False):
                with st.form("new_pack_form"):
                    try:
                        product_options = service.get_product_dropdown()
                        selected_prod = st.selectbox("Select Base Product", product_options)
                    except Exception as e:
                        logger.error(f"Error fetching products for pack: {str(e)}", exc_info=True)
                        st.error("Unable to load products")
                        selected_prod = None
                    
                    c1, c2 = st.columns(2)
                    pack_sku = c1.text_input("Pack SKU (Unique)", placeholder="TSHIRT-BLK-L-PK2")
                    qty = c2.number_input("Quantity in Pack", min_value=1, value=1)
                    
                    c3, c4 = st.columns(2)
                    pack_cost = c3.number_input("Extra Packaging Cost", min_value=0.0)
                    weight = c4.number_input("Final Weight (Kg)", min_value=0.0, format="%.3f")
                    
                    if st.form_submit_button("Create Pack"):
                        try:
                            if " | " in selected_prod:
                                master_sku_clean = selected_prod.split(" | ")[0]
                            else:
                                master_sku_clean = selected_prod
                            
                            service.add_pack({
                                "pack_sku": pack_sku, "master_sku": master_sku_clean,
                                "quantity": qty, "packaging_cogs": pack_cost,
                                "final_wt_kg": weight
                            })
                            clear_catalog_cache()
                            logger.info(f"Pack created successfully: {pack_sku}")
                            st.success(f"Created Pack {pack_sku}")
                            st.rerun()
                        except Exception as e:
                            show_error(logger, f"Error creating pack {pack_sku}", e)

            # View/Edit Grid
            try:
                df_pack = pd.read_sql("SELECT * FROM pack_master", engine)
                edited_pack = st.data_editor(df_pack, key="editor_pack", num_rows="dynamic")
                if st.button("Save Pack Changes"):
                    try:
                        with engine.connect() as conn:
                            edited_pack.to_sql('pack_master', conn, if_exists='replace', index=False)
                            conn.commit()
                        logger.info(f"Pack changes saved: {len(edited_pack)} rows")
                        st.success("Saved!")
                    except Exception as e:
                        show_error(logger, "Error saving pack changes", e)
            except Exception as e:
                show_error(logger, "Error loading packs", e)

        # --- TAB 3: CHANNEL LISTINGS ---
        with tab3:
            st.subheader("Map Listings to Packs")
            st.info("💡 **Remember**: Set 'selling_price' for each listing to enable profit calculations!")
            
            with st.expander("➕ Add Listing", expanded=False):
                with st.form("new_listing_form"):
                    try:
                        pack_options = service.get_pack_dropdown()
                        selected_pack = st.selectbox("Select Internal Pack", pack_options)
                    except Exception as e:
                        logger.error(f"Error fetching packs for listing: {str(e)}", exc_info=True)
                        st.error("Unable to load packs")
                        selected_pack = None
                    
                    c1, c2, c3 = st.columns(3)
                    marketplace = st.selectbox("Marketplace", get_marketplaces(), key="listing_mkt")
                    channel_sku = c2.text_input("Channel SKU / ASIN")
                    status = c3.selectbox("Status", ["LIVE", "INACTIVE", "SUPPRESSED"])
                    
                    selling_price = st.number_input("Selling Price (₹)", min_value=0.0, value=0.0, help="CRITICAL: Must be > 0 for profit calculation")
                    
                    if st.form_submit_button("Link Listing"):
                        try:
                            if selling_price <= 0:
                                st.warning("⚠️ Warning: Selling price is 0. This SKU will show 0% margin until price is set.")
                            
                            service.add_listing({
                                "channel_sku": channel_sku, "marketplace": marketplace,
                                "internal_sku": selected_pack, "listing_status": status,
                                "selling_price": selling_price
                            })
                            clear_catalog_cache()
                            logger.info(f"Listing created: {channel_sku} on {marketplace}")
                            st.success(f"Linked {channel_sku} -> {selected_pack}")
                            st.rerun()
                        except ValueError as e:
                            logger.warning(f"Validation error creating listing: {str(e)}")
                            st.error(f"❌ {str(e)}")
                        except Exception as e:
                            show_error(logger, "Error creating listing", e)

            # View/Edit Grid
            try:
                df_list = pd.read_sql("SELECT * FROM channel_listings", engine)
                
                def highlight_missing_price(row):
                    if row['selling_price'] <= 0:
                        return ['background-color: #fff3cd'] * len(row)
                    return [''] * len(row)
                
                edited_list = st.data_editor(df_list, key="editor_list", num_rows="dynamic")
                if st.button("Save Listing Changes"):
                    try:
                        with engine.connect() as conn:
                            edited_list.to_sql('channel_listings', conn, if_exists='replace', index=False)
                            conn.commit()
                        logger.info(f"Listing changes saved: {len(edited_list)} rows")
                        st.success("Saved!")
                    except Exception as e:
                        show_error(logger, "Error saving listing changes", e)
            except Exception as e:
                show_error(logger, "Error loading listings", e)

        # --- TAB 4: CONFIG & RULES ---
        with tab4:
            st.subheader("Global Configuration")

            # --- Freshness indicator ---
            try:
                from datetime import datetime, timezone
                lv_str = get_rules_last_verified()
                if lv_str:
                    lv = datetime.fromisoformat(lv_str)
                    days_ago = (datetime.now(timezone.utc).replace(tzinfo=None) - lv).days
                    if days_ago > 30:
                        st.warning(
                            f"⚠️ Fee rules last verified **{days_ago} days ago** "
                            f"({lv.strftime('%d %b %Y')}). "
                            "Review and save below to confirm they are current."
                        )
                    else:
                        st.caption(f"✅ Fee rules verified {lv.strftime('%d %b %Y')}")
                else:
                    st.info("ℹ️ Fee rules haven't been verified yet. Review and save below to set the verified date.")
            except Exception:
                pass

            rule_choice = st.radio("Edit:", ["Pricing Rules", "Shipping Rules", "General Config"], horizontal=True)

            # SQL table + display column mapping (no id column exposed)
            sql_table_map = {
                "Pricing Rules":  ("pricing_rules", ['marketplace', 'category_ref', 'min_price', 'max_price', 'referral_fee_pct', 'closing_fee_inr']),
                "Shipping Rules": ("shipping_rules", ['marketplace', 'weight_slab_max_kg', 'local_fee', 'regional_fee', 'national_fee']),
                "General Config": ("config",         ['marketplace', 'default_zone', 'volumetric_divisor', 'gst_on_fees']),
            }
            sql_table, sql_columns = sql_table_map[rule_choice]

            try:
                col_list = ", ".join(sql_columns)
                df_rules = pd.read_sql(f"SELECT {col_list} FROM {sql_table}", engine)
                edited_rules = st.data_editor(df_rules, num_rows="dynamic", key=f"editor_{sql_table}")

                if st.button(f"Save {rule_choice}"):
                    try:
                        save_df = edited_rules[[c for c in sql_columns if c in edited_rules.columns]]
                        with engine.begin() as conn:
                            conn.execute(text(f"DELETE FROM {sql_table}"))
                            save_df.to_sql(sql_table, conn, if_exists='append', index=False)
                        set_rules_last_verified()
                        clear_all_caches()
                        logger.info(f"Configuration saved for {rule_choice}: {len(save_df)} rows")
                        st.success(f"✅ Updated {rule_choice}. Rules verified date reset to today.")
                        st.rerun()
                    except Exception as e:
                        show_error(logger, f"Error saving {rule_choice}", e)
            except Exception as e:
                show_error(logger, f"Error loading {rule_choice}", e)
            
            # ============================================================
            # MARKETPLACE MANAGEMENT (inside tab4 — Config & Rules)
            # ============================================================
            st.divider()
            st.subheader("🌐 Marketplace Management")
            st.caption("Add or remove marketplaces. Changes reflect across all dashboards.")

            # Show existing marketplaces (from SQL)
            try:
                existing_df = pd.read_sql(
                    "SELECT marketplace, default_zone, volumetric_divisor, gst_on_fees "
                    "FROM config ORDER BY marketplace",
                    engine
                )
                if not existing_df.empty:
                    st.markdown("**Configured Marketplaces:**")
                    st.dataframe(
                        existing_df.rename(columns={
                            'marketplace': 'Marketplace',
                            'default_zone': 'Default Zone',
                            'volumetric_divisor': 'Vol. Divisor',
                            'gst_on_fees': 'GST on Fees'
                        }),
                        width='stretch',
                        hide_index=True
                    )
                else:
                    st.info("No marketplaces configured yet. Add your first one below.")
            except Exception as e:
                show_error(logger, "Failed to load existing marketplaces", e)
                existing_df = pd.DataFrame()

            # Two-column layout: Add (left) | Remove (right)
            col_add, col_remove = st.columns(2)

            # ---------- ADD MARKETPLACE ----------
            with col_add:
                st.markdown("##### ➕ Add Marketplace")
                with st.form("add_marketplace", clear_on_submit=True):
                    new_mkt = st.text_input(
                        "Marketplace Name",
                        placeholder="e.g., Myntra, Ajio, JioMart",
                        help="Must be unique. Will appear in all dashboards."
                    )
                    zone = st.selectbox(
                        "Default Zone",
                        ["Local", "Regional", "National"],
                        index=2,
                        help="Default shipping zone for fee calculations"
                    )
                    vol_div = st.number_input(
                        "Volumetric Divisor",
                        value=5000,
                        min_value=1000,
                        max_value=10000,
                        step=100,
                        help="Used for volumetric weight: (L×W×H)/divisor. Amazon=5000, Flipkart=5000"
                    )
                    gst_fees = st.number_input(
                        "GST on Fees (%)",
                        value=18.0,
                        min_value=0.0,
                        max_value=28.0,
                        step=0.5,
                        help="GST applied on platform fees (typically 18%)"
                    )

                    submitted = st.form_submit_button("Add Marketplace", type="primary")

                    if submitted:
                        new_mkt_clean = (new_mkt or "").strip()

                        if not new_mkt_clean:
                            st.error("❌ Marketplace name cannot be empty.")
                            logger.warning("Add marketplace attempted with empty name")
                        elif len(new_mkt_clean) > 50:
                            st.error("❌ Marketplace name must be 50 characters or fewer.")
                            logger.warning(f"Add marketplace name too long: {len(new_mkt_clean)} chars")
                        elif not existing_df.empty and new_mkt_clean.lower() in existing_df['marketplace'].str.lower().tolist():
                            st.error(f"❌ Marketplace '{new_mkt_clean}' already exists. Use a unique name.")
                            logger.warning(f"Duplicate marketplace insert blocked: {new_mkt_clean}")
                        else:
                            try:
                                logger.info(f"Adding new marketplace: {new_mkt_clean}")
                                gst_val = float(gst_fees) / 100.0 if gst_fees > 1.0 else float(gst_fees)
                                with engine.begin() as conn:
                                    conn.execute(text(
                                        "INSERT OR REPLACE INTO config "
                                        "(marketplace, default_zone, volumetric_divisor, gst_on_fees) "
                                        "VALUES (:m, :dz, :vd, :gf)"
                                    ), {"m": new_mkt_clean, "dz": zone.lower(),
                                        "vd": int(vol_div), "gf": gst_val})
                                set_rules_last_verified()
                                clear_all_caches()
                                logger.info(f"✅ Marketplace '{new_mkt_clean}' added successfully")
                                st.success(
                                    f"✅ Marketplace **{new_mkt_clean}** added!\n\n"
                                    f"Next steps:\n"
                                    f"1. Add **Pricing Rules** for this marketplace above\n"
                                    f"2. Add **Shipping Rules** (weight slabs and zone fees)\n"
                                    f"3. Create **Listings** in the Listings tab"
                                )
                                st.rerun()
                            except Exception as e:
                                show_error(logger, f"Failed to add marketplace '{new_mkt_clean}'", e)

            # ---------- REMOVE MARKETPLACE ----------
            with col_remove:
                st.markdown("##### 🗑️ Remove Marketplace")

                if existing_df.empty:
                    st.info("Nothing to remove yet.")
                else:
                    with st.form("remove_marketplace"):
                        mkt_to_remove = st.selectbox(
                            "Select Marketplace to Remove",
                            options=existing_df['marketplace'].tolist(),
                            help="⚠️ This will also affect listings/rules tied to this marketplace"
                        )

                        # Impact preview from SQL
                        try:
                            with engine.connect() as conn:
                                listing_count = conn.execute(
                                    text("SELECT COUNT(*) FROM channel_listings WHERE marketplace = :m"),
                                    {"m": mkt_to_remove}
                                ).scalar() or 0
                                pricing_count = conn.execute(
                                    text("SELECT COUNT(*) FROM pricing_rules WHERE marketplace = :m"),
                                    {"m": mkt_to_remove}
                                ).scalar() or 0
                                shipping_count = conn.execute(
                                    text("SELECT COUNT(*) FROM shipping_rules WHERE marketplace = :m"),
                                    {"m": mkt_to_remove}
                                ).scalar() or 0

                            if listing_count or pricing_count or shipping_count:
                                st.warning(
                                    f"⚠️ **Impact preview for '{mkt_to_remove}':**\n"
                                    f"- {listing_count} listings\n"
                                    f"- {pricing_count} pricing rules\n"
                                    f"- {shipping_count} shipping rules\n\n"
                                    f"These will become orphaned. Consider exporting via Bulk Operations first."
                                )
                        except Exception as e:
                            logger.error(f"Failed to compute impact preview: {str(e)}", exc_info=True)

                        confirm = st.checkbox(
                            f"I understand and confirm removal of '{mkt_to_remove}'",
                            key="confirm_remove_mkt"
                        )
                        cascade = st.checkbox(
                            "Also delete its listings, pricing rules, and shipping rules (cascade)",
                            value=False,
                            help="If unchecked, only the config row is removed and related rules are kept (orphaned)."
                        )

                        remove_submitted = st.form_submit_button("Remove Marketplace", type="secondary")

                        if remove_submitted:
                            if not confirm:
                                st.error("❌ Please confirm by checking the box above.")
                            else:
                                try:
                                    logger.info(f"Removing marketplace: {mkt_to_remove} (cascade={cascade})")
                                    with engine.begin() as conn:
                                        conn.execute(
                                            text("DELETE FROM config WHERE marketplace = :m"),
                                            {"m": mkt_to_remove}
                                        )
                                        if cascade:
                                            conn.execute(
                                                text("DELETE FROM pricing_rules WHERE marketplace = :m"),
                                                {"m": mkt_to_remove}
                                            )
                                            conn.execute(
                                                text("DELETE FROM shipping_rules WHERE marketplace = :m"),
                                                {"m": mkt_to_remove}
                                            )

                                    if cascade:
                                        deleted = service.delete_listings_for_marketplace(mkt_to_remove)
                                        logger.info(f"Cascade-deleted {deleted} listing(s) for {mkt_to_remove}")

                                    clear_all_caches()
                                    logger.info(f"✅ Marketplace '{mkt_to_remove}' removed")
                                    st.success(f"✅ Marketplace **{mkt_to_remove}** removed successfully.")
                                    st.rerun()
                                except Exception as e:
                                    show_error(logger, f"Failed to remove marketplace '{mkt_to_remove}'", e)
            

        # --- TAB 5: BULK OPERATIONS (EXCEL) ---
        with tab5:
            st.subheader("Mass Data Management")
            st.markdown("""
            **Workflow:**
            1. ⬇️ **Download** the full Master Excel file.
            2. ✏️ **Edit** in Excel (Add products, change prices, fix gaps).
            3. ⬆️ **Upload** the same file back to update the system.
            """)
            
            col1, col2 = st.columns(2)
            
            with col1:
                st.markdown("### 1. Export Data")
                if st.button("⬇️ Download Master Catalog (.xlsx)"):
                    try:
                        logger.info("Exporting full catalog...")
                        excel_data = bulk_service.download_full_catalog()
                        st.download_button(
                            label="Click to Save File",
                            data=excel_data,
                            file_name="Master_Catalog_Export.xlsx",
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                        )
                        logger.info("Catalog export successful")
                    except Exception as e:
                        show_error(logger, "Error exporting catalog", e)

            with col2:
                st.markdown("### 2. Import Data")
                uploaded_file = st.file_uploader("Upload Master Catalog", type=["xlsx"])
                
                if uploaded_file:
                    if st.button("🚀 Process Full Update"):
                        try:
                            logger.info(f"Starting catalog import: {uploaded_file.name}")
                            success, log = bulk_service.upload_full_catalog(uploaded_file)
                            if success:
                                logger.info("Catalog import completed successfully")
                                st.success("Update Complete!")
                                st.text(log)
                            else:
                                logger.error(f"Catalog import failed: {log}")
                                st.error(f"Failed: {log}")
                        except Exception as e:
                            show_error(logger, "Error during catalog import", e)
    
    except Exception as e:
        show_error(logger, "Critical error in Data Manager", e)