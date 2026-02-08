import streamlit as st
import pandas as pd
from src.core.services.inventory_service import InventoryService

def render():
    st.title("📦 Godown Inventory")
    service = InventoryService()
    
    # Organize into Tabs
    tab1, tab2, tab3 = st.tabs(["📊 Current Balances", "➕ Update Stock", "📜 Recent History"])

    # --- TAB 1: BALANCES ---
    with tab1:
        df = service.get_godown_inventory()
        df['Total Pieces'] = df['godown_stock_packs'] * df['pack_multiplier']
        st.dataframe(df, use_container_width=True, hide_index=True)

    # --- TAB 2: UPDATE ---
    with tab2:
        df_for_select = service.get_godown_inventory()
        with st.form("stock_form"):
            c1, c2 = st.columns(2)
            sku = c1.selectbox("Select SKU", df_for_select['sku'].unique())
            action = c2.selectbox("Action", ["ADD", "REMOVE"])
            
            # Fetch the multiplier this SKU "remembers"
            current_row = df_for_select[df_for_select['sku'] == sku].iloc[0]
            default_mult = int(current_row['pack_multiplier'])
            
            c3, c4 = st.columns(2)
            num_packs = c3.number_input("Number of Packs", min_value=1, step=1)
            pack_size = c4.number_input("Pieces per Pack (Multiplier)", min_value=1, value=default_mult)
            
            reason = st.text_input("Note / Reference (Optional)")
            
            if st.form_submit_button("Update Inventory"):
                service.manage_godown_stock(sku, num_packs, pack_size, action, reason)
                st.success(f"Success: {action} {num_packs} packs for {sku}")
                st.rerun()

    # --- TAB 3: HISTORY ---
    with tab3:
        history_query = "SELECT * FROM stock_ledger ORDER BY timestamp DESC"
        history_df = pd.read_sql(history_query, service.engine)
        
        # Color coding for better visibility
        def color_action(val):
            color = 'green' if val == 'ADD' else 'red'
            return f'color: {color}; font-weight: bold'

        if not history_df.empty:
            st.dataframe(
                history_df.style.map(color_action, subset=['transaction_type']),
                use_container_width=True,
                hide_index=True
            )
        else:
            st.info("No history records found.")