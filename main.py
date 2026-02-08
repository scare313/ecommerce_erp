import streamlit as st
from src.infrastructure.init_db import init_database

# Initialize database on first run
if not st.session_state.get("db_initialized"):
    init_database()
    st.session_state.db_initialized = True

st.set_page_config(page_title="Ecommerce ERP", layout="wide")

st.sidebar.title("Navigation")
page = st.sidebar.radio("Go to", ["Profit Dashboard", "Gap Analysis", "Demand Planner", "Data Manager", "Inventory Manager"])

if page == "Profit Dashboard":
    from src.ui.pages import profit_dashboard
    profit_dashboard.render()
elif page == "Gap Analysis":
    from src.ui.pages import gap_dashboard
    gap_dashboard.render()
elif page == "Demand Planner": # NEW
    from src.ui.pages import demand_planner
    demand_planner.render()
elif page == "Data Manager":
    from src.ui.pages import data_manager
    data_manager.render()
elif page == "Inventory Manager":
    from src.ui.pages import inventory_manager
    inventory_manager.render()