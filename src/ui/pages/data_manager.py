import streamlit as st
import pandas as pd
from src.core.services.catalog_service import CatalogService
from src.core.services.bulk_service import BulkService # New Service
from src.infrastructure.database import get_engine

def render():
    st.title("🗂️ Master Data Manager")
    
    service = CatalogService()
    bulk_service = BulkService() # Initialize
    engine = get_engine()

    # Tabs for Workflow
    # Added "Bulk Operations" tab
    tab1, tab2, tab3, tab4, tab5 = st.tabs(["1. Products", "2. Packs", "3. Listings", "⚙️ Config & Rules", "📥 Bulk Operations"])

    # --- TAB 1: PRODUCT MASTER ---
    with tab1:
        st.subheader("Define Base Products")
        with st.expander("➕ Add New Product", expanded=False):
            with st.form("new_product_form"):
                c1, c2, c3 = st.columns(3)
                sku = c1.text_input("Master SKU (Unique)", placeholder="TSHIRT-BLK-L")
                name = c2.text_input("Product Name")
                
                # FIXED: Category Dropdown
                cat_options = service.get_category_dropdown()
                # Allow typing a new one if needed, or select existing
                category = c3.selectbox("Category (from Rules)", options=cat_options)
                
                c4, c5, c6 = st.columns(3)
                mfg_cost = c4.number_input("Mfg Cost", min_value=0.0)
                pkg_cost = c5.number_input("Unit Box Cost", min_value=0.0)
                labor = c6.number_input("Labor Cost", min_value=0.0)
                
                c7, c8 = st.columns(2)
                gst = c7.number_input("GST Rate (%)", value=5.0)
                status = c8.selectbox("Status", ["Active", "Discontinued"])
                
                if st.form_submit_button("Create Product"):
                    try:
                        # Use SKU as Name if Name is empty
                        final_name = name if name.strip() else sku
                        
                        service.add_product({
                            "sku": sku, "name": final_name, "category": category,
                            "mfg_cost": mfg_cost, "packaging_cost": pkg_cost, 
                            "labeling_labor": labor, "gst_rate": gst, "lifecycle_status": status
                        })
                        st.success(f"Created {sku}")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Error: {e}")

        # View/Edit Grid
        df_prod = pd.read_sql("SELECT * FROM product_master", engine)
        edited_prod = st.data_editor(df_prod, key="editor_prod", num_rows="dynamic")
        if st.button("Save Product Changes"):
            with engine.connect() as conn:
                edited_prod.to_sql('product_master', conn, if_exists='replace', index=False)
                st.success("Saved!")

    # --- TAB 2: PACK MASTER ---
    with tab2:
        st.subheader("Create Sellable Packs")
        with st.expander("➕ Add New Pack", expanded=False):
            with st.form("new_pack_form"):
                # DROPDOWN: Select from existing Products (Fixed Display)
                product_options = service.get_product_dropdown()
                selected_prod = st.selectbox("Select Base Product", product_options)
                
                c1, c2 = st.columns(2)
                pack_sku = c1.text_input("Pack SKU (Unique)", placeholder="TSHIRT-BLK-L-PK2")
                qty = c2.number_input("Quantity in Pack", min_value=1, value=1)
                
                c3, c4 = st.columns(2)
                pack_cost = c3.number_input("Extra Packaging Cost", min_value=0.0)
                weight = c4.number_input("Final Weight (Kg)", min_value=0.0, format="%.3f")
                
                if st.form_submit_button("Create Pack"):
                    # Extract pure SKU from dropdown string if it contains " | "
                    if " | " in selected_prod:
                        master_sku_clean = selected_prod.split(" | ")[0]
                    else:
                        master_sku_clean = selected_prod
                        
                    try:
                        service.add_pack({
                            "pack_sku": pack_sku, "master_sku": master_sku_clean,
                            "quantity": qty, "packaging_cogs": pack_cost,
                            "final_wt_kg": weight
                        })
                        st.success(f"Created Pack {pack_sku}")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Error: {e}")

        # View/Edit Grid
        df_pack = pd.read_sql("SELECT * FROM pack_master", engine)
        edited_pack = st.data_editor(df_pack, key="editor_pack", num_rows="dynamic")
        if st.button("Save Pack Changes"):
            with engine.connect() as conn:
                edited_pack.to_sql('pack_master', conn, if_exists='replace', index=False)
                st.success("Saved!")

    # --- TAB 3: CHANNEL LISTINGS ---
    with tab3:
        st.subheader("Map Listings to Packs")
        with st.expander("➕ Add Listing", expanded=False):
            with st.form("new_listing_form"):
                # DROPDOWN: Select from existing Packs
                pack_options = service.get_pack_dropdown()
                selected_pack = st.selectbox("Select Internal Pack", pack_options)
                
                c1, c2, c3 = st.columns(3)
                marketplace = c1.selectbox("Marketplace", ["Amazon", "Flipkart", "Meesho"])
                channel_sku = c2.text_input("Channel SKU / ASIN")
                status = c3.selectbox("Status", ["LIVE", "INACTIVE", "SUPPRESSED"])
                
                if st.form_submit_button("Link Listing"):
                    try:
                        service.add_listing({
                            "channel_sku": channel_sku, "marketplace": marketplace,
                            "internal_sku": selected_pack, "listing_status": status
                        })
                        st.success(f"Linked {channel_sku} -> {selected_pack}")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Error: {e}")

        # View/Edit Grid
        df_list = pd.read_sql("SELECT * FROM channel_listings", engine)
        edited_list = st.data_editor(df_list, key="editor_list", num_rows="dynamic")
        if st.button("Save Listing Changes"):
            with engine.connect() as conn:
                edited_list.to_sql('channel_listings', conn, if_exists='replace', index=False)
                st.success("Saved!")

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
        df_rules = pd.read_sql(f"SELECT * FROM {selected_table}", engine)
        
        edited_rules = st.data_editor(df_rules, num_rows="dynamic", key=f"editor_{selected_table}")
        
        if st.button(f"Save {rule_choice}"):
            with engine.connect() as conn:
                edited_rules.to_sql(selected_table, conn, if_exists='replace', index=False)
                st.success(f"Updated {rule_choice}")

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
                excel_data = bulk_service.download_full_catalog()
                st.download_button(
                    label="Click to Save File",
                    data=excel_data,
                    file_name="Master_Catalog_Export.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )

        with col2:
            st.markdown("### 2. Import Data")
            uploaded_file = st.file_uploader("Upload Master Catalog", type=["xlsx"])
            
            if uploaded_file:
                if st.button("🚀 Process Full Update"):
                    success, log = bulk_service.upload_full_catalog(uploaded_file)
                    if success:
                        st.success("Update Complete!")
                        st.text(log)
                    else:
                        st.error(f"Failed: {log}")