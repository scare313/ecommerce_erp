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
from src.core.services.catalog_service import CatalogService
from src.core.services.bulk_service import BulkService
from src.infrastructure.database import get_engine
from src.infrastructure.logger import get_logger

logger = get_logger(__name__)

def render():
    """Render the Data Manager UI page."""
    try:
        logger.info("Rendering Data Manager page...")
        st.title("🗂️ Master Data Manager")
        
        try:
            service = CatalogService()
            bulk_service = BulkService()
            engine = get_engine()
            logger.debug("Services initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize services: {str(e)}", exc_info=True)
            st.error(f"Failed to initialize services: {str(e)}")
            return

        tab1, tab2, tab3, tab4, tab5 = st.tabs(["1. Products", "2. Packs", "3. Listings", "⚙️ Config & Rules", "📥 Bulk Operations"])

        with tab1:
            st.subheader("Define Base Products")
            with st.expander("➕ Add New Product", expanded=False):
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
                    
                    if st.form_submit_button("Create Product"):
                        try:
                            final_name = name if name.strip() else sku
                            
                            service.add_product({
                                "sku": sku, "name": final_name, "category": category,
                                "mfg_cost": mfg_cost, "packaging_cost": pkg_cost, 
                                "labeling_labor": labor, "gst_rate": gst, "lifecycle_status": status
                            })
                            logger.info(f"Product created successfully: {sku}")
                            st.success(f"Created {sku}")
                            st.rerun()
                        except Exception as e:
                            logger.error(f"Error creating product {sku}: {str(e)}", exc_info=True)
                            st.error(f"Error: {e}")

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
                        logger.error(f"Error saving product changes: {str(e)}", exc_info=True)
                        st.error(f"Error saving: {e}")
            except Exception as e:
                logger.error(f"Error loading products: {str(e)}", exc_info=True)
                st.error(f"Error loading products: {e}")

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
                            logger.info(f"Pack created successfully: {pack_sku}")
                            st.success(f"Created Pack {pack_sku}")
                            st.rerun()
                        except Exception as e:
                            logger.error(f"Error creating pack {pack_sku}: {str(e)}", exc_info=True)
                            st.error(f"Error: {e}")

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
                        logger.error(f"Error saving pack changes: {str(e)}", exc_info=True)
                        st.error(f"Error saving: {e}")
            except Exception as e:
                logger.error(f"Error loading packs: {str(e)}", exc_info=True)
                st.error(f"Error loading packs: {e}")

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
                    marketplace = c1.selectbox("Marketplace", ["Amazon", "Flipkart", "Meesho"])
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
                            logger.info(f"Listing created: {channel_sku} on {marketplace}")
                            st.success(f"Linked {channel_sku} -> {selected_pack}")
                            st.rerun()
                        except ValueError as e:
                            logger.warning(f"Validation error creating listing: {str(e)}")
                            st.error(f"❌ {str(e)}")
                        except Exception as e:
                            logger.error(f"Error creating listing: {str(e)}", exc_info=True)
                            st.error(f"Error: {e}")

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
                        logger.error(f"Error saving listing changes: {str(e)}", exc_info=True)
                        st.error(f"Error saving: {e}")
            except Exception as e:
                logger.error(f"Error loading listings: {str(e)}", exc_info=True)
                st.error(f"Error loading listings: {e}")

        # --- TAB 4: CONFIG & RULES ---
        with tab4:
            st.subheader("Global Configuration")
            
            rule_choice = st.radio("Edit:", ["Pricing Rules", "Shipping Rules", "General Config"], horizontal=True)
            
            table_map = {
                "Pricing Rules": "pricing_rules",
                "Shipping Rules": "shipping_rules",
                "General Config": "config"
            }
            
            selected_table = table_map[rule_choice]
            
            try:
                df_rules = pd.read_sql(f"SELECT * FROM {selected_table}", engine)
                edited_rules = st.data_editor(df_rules, num_rows="dynamic", key=f"editor_{selected_table}")
                
                if st.button(f"Save {rule_choice}"):
                    try:
                        with engine.connect() as conn:
                            edited_rules.to_sql(selected_table, conn, if_exists='replace', index=False)
                            conn.commit()
                        logger.info(f"Configuration saved for {selected_table}: {len(edited_rules)} rows")
                        st.success(f"Updated {rule_choice}")
                    except Exception as e:
                        logger.error(f"Error saving {rule_choice}: {str(e)}", exc_info=True)
                        st.error(f"Error saving: {e}")
            except Exception as e:
                logger.error(f"Error loading {rule_choice}: {str(e)}", exc_info=True)
                st.error(f"Error loading {rule_choice}: {e}")

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
                        logger.error(f"Error exporting catalog: {str(e)}", exc_info=True)
                        st.error(f"Error exporting: {e}")

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
                            logger.error(f"Error during catalog import: {str(e)}", exc_info=True)
                            st.error(f"Error: {e}")
    
    except Exception as e:
        logger.error(f"Critical error in Data Manager render: {str(e)}", exc_info=True)
        st.error(f"Critical error: {e}")