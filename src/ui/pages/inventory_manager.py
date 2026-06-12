"""Inventory Manager UI Page.

Provides interfaces for managing godown stock levels including:
- Current Balances: View all SKU inventories
- Update Stock: Add or remove packs with reason tracking (supports barcode scanning)
- Recent History: View transaction ledger
- Bulk Update: Mass import from Excel

Godown Mode (sidebar toggle):
  Activates a streamlined mobile-friendly scan-and-update workflow that supports:
  - Hardware barcode scanners (keyboard emulation via USB/Bluetooth)
  - Camera barcode scanning via phone/tablet browser (Html5-QRCode)
"""
import streamlit as st
import pandas as pd
import io
from src.core.services.inventory_service import InventoryService
from src.infrastructure.database import get_engine
from src.infrastructure.logger import get_logger
from src.core.cache import clear_inventory_cache
from src.ui.components.barcode_scanner import render_camera_scanner, render_hardware_scanner_input
from sqlalchemy import text

logger = get_logger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def color_action(val):
    """Apply color styling to transaction type: green for ADD, red for REMOVE.

    Args:
        val: Transaction type ('ADD' or 'REMOVE')

    Returns:
        str: CSS color styling
    """
    color = 'green' if val == 'ADD' else 'red'
    return f'color: {color}; font-weight: bold'


def _inject_mobile_styles():
    """Inject CSS that makes the Godown Mode cards look great on any screen."""
    st.markdown("""
    <style>
    /* ── Godown Mode overall layout ── */
    .godown-header {
        background: linear-gradient(135deg, #1e3a5f 0%, #0f2a45 100%);
        border: 1px solid rgba(99,179,237,0.25);
        border-radius: 16px;
        padding: 20px 24px;
        margin-bottom: 18px;
        display: flex;
        align-items: center;
        gap: 14px;
    }
    .godown-header-icon { font-size: 2.2rem; }
    .godown-header-text h2 {
        margin: 0;
        font-size: 1.4rem;
        color: #93c5fd;
        font-weight: 700;
        letter-spacing: 0.5px;
    }
    .godown-header-text p {
        margin: 2px 0 0;
        font-size: 0.85rem;
        color: #64748b;
    }

    /* ── Product card that appears after scan ── */
    .product-card {
        background: linear-gradient(135deg, #0f2a1e 0%, #0d1f16 100%);
        border: 1px solid rgba(74,222,128,0.3);
        border-radius: 16px;
        padding: 20px 24px;
        margin: 14px 0;
    }
    .product-card .sku-badge {
        display: inline-block;
        background: rgba(74,222,128,0.15);
        color: #4ade80;
        font-size: 0.75rem;
        font-weight: 700;
        letter-spacing: 1.5px;
        padding: 3px 10px;
        border-radius: 20px;
        margin-bottom: 8px;
    }
    .product-card .product-name {
        font-size: 1.15rem;
        font-weight: 700;
        color: #f1f5f9;
        margin: 0 0 4px;
    }
    .product-card .stock-info {
        font-size: 0.9rem;
        color: #94a3b8;
    }
    .product-card .stock-info strong { color: #e2e8f0; }

    /* ── Quick-qty stepper ── */
    .qty-row {
        display: flex;
        align-items: center;
        gap: 16px;
        margin: 16px 0 8px;
        flex-wrap: wrap;
    }
    .qty-label {
        font-size: 0.85rem;
        color: #94a3b8;
        font-weight: 600;
        letter-spacing: 0.5px;
        text-transform: uppercase;
    }

    /* ── Status chips ── */
    .chip {
        display: inline-block;
        padding: 4px 12px;
        border-radius: 20px;
        font-size: 0.78rem;
        font-weight: 700;
        letter-spacing: 0.5px;
    }
    .chip-green { background: rgba(74,222,128,0.15); color: #4ade80; }
    .chip-red   { background: rgba(248,113,113,0.15); color: #f87171; }
    .chip-blue  { background: rgba(99,179,237,0.15);  color: #63b3ed; }

    /* Responsive tweaks for narrow screens */
    @media (max-width: 640px) {
        .godown-header { padding: 14px 16px; }
        .godown-header-text h2 { font-size: 1.15rem; }
        .product-card { padding: 16px; }
    }
    </style>
    """, unsafe_allow_html=True)


# ──────────────────────────────────────────────────────────────────────────────
# Godown Mode — streamlined scan-and-update workflow
# ──────────────────────────────────────────────────────────────────────────────

def _render_godown_mode(service: InventoryService):
    """Render the mobile-optimised Godown Mode scan workflow.

    Flow:
        1. Choose scanner type (Hardware / Camera)
        2. Scan → product card appears with current stock
        3. Set packs, multiplier, action (ADD / REMOVE)
        4. Commit — instant feedback, ready for next scan
    """
    _inject_mobile_styles()

    st.markdown("""
    <div class="godown-header">
        <div class="godown-header-icon">📦</div>
        <div class="godown-header-text">
            <h2>Godown Scan Mode</h2>
            <p>Scan a barcode to instantly update stock — no typing needed</p>
        </div>
    </div>
    """, unsafe_allow_html=True)

    # ── Scanner type selector ─────────────────────────────────────────────────
    scanner_type = st.radio(
        "Scanner Input",
        ["🔌 Hardware Scanner (USB/Bluetooth)", "📷 Camera (Phone/Tablet)"],
        horizontal=True,
        key="godown_scanner_type",
        help="Hardware scanners work like keyboards — just focus and scan. Camera mode uses your device camera.",
    )

    st.divider()

    # ── Input section ─────────────────────────────────────────────────────────
    scanned_sku = ""

    if "Hardware" in scanner_type:
        # Hardware scanner sends barcode + Enter via keyboard emulation
        scanned_sku = render_hardware_scanner_input(
            label="🔍 Point scanner at barcode, or type SKU",
            key="hw_sku_input"
        )
    else:
        # Camera scanner via Html5-QRCode JS component
        st.info(
            "📱 **Camera Mode** — Tap **Start Camera**, point at the barcode, "
            "then tap **✔ Use This SKU** when it locks on.",
            icon="ℹ️"
        )

        render_camera_scanner(height=460)

        # Receive value from JS postMessage via a hidden text_input
        # The JS in barcode_scanner.py targets input elements; we use a
        # dedicated session-state key as the bridge.
        cam_val = st.text_input(
            "Camera scan result (auto-filled)",
            key="cam_scan_result",
            label_visibility="collapsed",
            placeholder="Waiting for camera scan…",
        )
        if cam_val:
            scanned_sku = cam_val.strip().upper()
            if st.button("🔄 Scan Another", key="cam_clear_btn"):
                st.session_state["cam_scan_result"] = ""
                st.rerun()

    # ── Look up SKU in inventory ──────────────────────────────────────────────
    if scanned_sku:
        st.markdown(f"<br>", unsafe_allow_html=True)
        df_inv = service.get_godown_inventory()
        row = df_inv[df_inv['sku'].str.upper() == scanned_sku]

        if row.empty:
            st.error(
                f"❌ **SKU not found:** `{scanned_sku}`\n\n"
                "Please check the barcode or add this product via the Onboarding Wizard."
            )
        else:
            r = row.iloc[0]
            current_packs = int(r.get('godown_stock_packs', 0))
            default_mult  = int(r.get('pack_multiplier', 1))
            category       = r.get('category', '–')
            total_pieces  = current_packs * default_mult

            # ── Product card ─────────────────────────────────────────────────
            st.markdown(f"""
            <div class="product-card">
                <div class="sku-badge">✅ {scanned_sku}</div>
                <div class="product-name">{category}</div>
                <div class="stock-info">
                    Current stock:&nbsp;
                    <strong>{current_packs} packs</strong>
                    &nbsp;×&nbsp;
                    <strong>{default_mult} pcs/pack</strong>
                    &nbsp;=&nbsp;
                    <strong>{total_pieces} pcs</strong>
                </div>
            </div>
            """, unsafe_allow_html=True)

            # ── Update form ──────────────────────────────────────────────────
            with st.form(key=f"godown_update_{scanned_sku}"):
                st.markdown("#### 🛒 Update Stock")

                col_action, col_packs, col_mult = st.columns([2, 2, 2])

                action = col_action.selectbox(
                    "Action",
                    ["ADD ➕", "REMOVE ➖"],
                    key="godown_action"
                )

                # Quick-step number inputs — large targets for touch screens
                num_packs = col_packs.number_input(
                    "Packs",
                    min_value=1,
                    value=1,
                    step=1,
                    key="godown_packs",
                )
                pack_size = col_mult.number_input(
                    "Pcs / Pack",
                    min_value=1,
                    value=default_mult,
                    step=1,
                    key="godown_mult",
                )

                # Quick ±1 shortcut row (rendered as markdown helper text)
                st.caption(
                    f"📊 This will change stock by **{int(num_packs)} packs "
                    f"({int(num_packs) * int(pack_size)} pcs)**"
                )

                reason = st.text_input(
                    "Note / Reference (Optional)",
                    placeholder="e.g. GRN-20240612, Return, Damage",
                    key="godown_reason",
                )

                submitted = st.form_submit_button(
                    "✅ Confirm Update",
                    type="primary",
                    use_container_width=True,
                )

            if submitted:
                action_type = "ADD" if "ADD" in action else "REMOVE"
                try:
                    logger.info(
                        f"[GodownMode] {action_type} {num_packs} packs "
                        f"(×{pack_size}) for SKU={scanned_sku}"
                    )
                    service.manage_godown_stock(
                        scanned_sku,
                        int(num_packs),
                        int(pack_size),
                        action_type,
                        reason or "Godown scan",
                    )
                    clear_inventory_cache()

                    new_packs = current_packs + int(num_packs) if action_type == "ADD" else current_packs - int(num_packs)
                    st.success(
                        f"✅ **{action_type}** · {int(num_packs)} packs · "
                        f"New stock: **{new_packs} packs**"
                    )
                    logger.info(f"[GodownMode] Update successful for {scanned_sku}")

                    # Clear the scanner input so the operator can scan the next item
                    if "Hardware" in scanner_type:
                        st.session_state["hw_sku_input"] = ""
                    else:
                        st.session_state["cam_scan_result"] = ""

                    st.rerun()

                except Exception as e:
                    logger.error(f"[GodownMode] Update failed: {e}", exc_info=True)
                    st.error(f"❌ Update failed: {str(e)}")
    else:
        # Idle state hint
        st.markdown("""
        <div style="
            text-align: center;
            padding: 40px 20px;
            border: 2px dashed rgba(99,179,237,0.2);
            border-radius: 16px;
            margin-top: 16px;
            color: #475569;
        ">
            <div style="font-size:3rem; margin-bottom:10px;">🎯</div>
            <p style="font-size:1rem; font-weight:600; color:#64748b;">
                Waiting for scan…
            </p>
            <p style="font-size:0.85rem;">
                Use a hardware scanner or start camera above.
            </p>
        </div>
        """, unsafe_allow_html=True)


# ──────────────────────────────────────────────────────────────────────────────
# Desktop / Standard Mode — full-featured tabbed interface
# ──────────────────────────────────────────────────────────────────────────────

def _render_standard_mode(service: InventoryService):
    """Render the standard desktop tabbed inventory interface."""

    tab1, tab2, tab3, tab4 = st.tabs(
        ["📊 Current Balances", "➕ Update Stock", "📜 Recent History", "📥 Bulk Update"]
    )

    # ── TAB 1: BALANCES ───────────────────────────────────────────────────────
    with tab1:
        try:
            logger.debug("Loading godown inventory balances...")
            df = service.get_godown_inventory()
            logger.info(f"Loaded inventory for {len(df)} SKUs")

            if df.empty:
                logger.warning("No inventory records found")
                st.warning("No inventory records found.")
            else:
                df['Total Pieces'] = df['godown_stock_packs'] * df['pack_multiplier']
                st.dataframe(df, use_container_width=True, hide_index=True)
        except Exception as e:
            logger.error(f"Error loading inventory balances: {str(e)}", exc_info=True)
            st.error(f"Error loading inventory: {str(e)}")

    # ── TAB 2: UPDATE (with hardware-scanner assist) ──────────────────────────
    with tab2:
        try:
            logger.debug("Loading inventory for update form...")
            df_for_select = service.get_godown_inventory()

            if df_for_select.empty:
                logger.warning("No inventory records found for update")
                st.warning("No inventory records found. Please add stock first.")
            else:
                st.info(
                    "💡 **Tip:** If you have a USB/Bluetooth barcode scanner connected, "
                    "click inside the **Scan / Search SKU** box below and scan the barcode — "
                    "it will auto-fill the SKU for you.",
                    icon="ℹ️"
                )

                # ── Optional hardware scanner shortcut ────────────────────────
                quick_sku = render_hardware_scanner_input(
                    label="🔍 Scan / Search SKU (optional)",
                    key="desktop_hw_scan"
                )

                # Derive the selected SKU — scanner result overrides dropdown
                sku_options = df_for_select['sku'].unique().tolist()
                default_idx = 0
                if quick_sku and quick_sku in sku_options:
                    default_idx = sku_options.index(quick_sku)

                with st.form("stock_form"):
                    c1, c2 = st.columns(2)
                    sku = c1.selectbox(
                        "Select SKU",
                        sku_options,
                        index=default_idx,
                    )
                    action = c2.selectbox("Action", ["ADD", "REMOVE"])

                    filtered_row = df_for_select[df_for_select['sku'] == sku]
                    if filtered_row.empty:
                        logger.error(f"SKU {sku} not found in inventory")
                        st.error(f"SKU {sku} not found in inventory.")
                    else:
                        current_row = filtered_row.iloc[0]
                        try:
                            default_mult = int(current_row['pack_multiplier'])
                        except (ValueError, TypeError):
                            logger.warning(f"Invalid pack_multiplier for {sku}, using default")
                            default_mult = 1
                            st.warning("Using default pack size of 1")

                        c3, c4 = st.columns(2)
                        num_packs = c3.number_input("Number of Packs", min_value=1, step=1)
                        pack_size = c4.number_input(
                            "Pieces per Pack (Multiplier)",
                            min_value=1,
                            value=default_mult,
                        )

                        reason = st.text_input("Note / Reference (Optional)")

                        if st.form_submit_button("Update Inventory"):
                            try:
                                logger.info(f"Updating inventory: {sku} {action} {num_packs} packs")
                                service.manage_godown_stock(sku, num_packs, pack_size, action, reason)
                                clear_inventory_cache()
                                logger.info(f"Successfully updated inventory for {sku}")
                                st.success(f"✅ {action} {num_packs} packs for {sku}")
                                st.rerun()
                            except Exception as e:
                                logger.error(f"Error updating inventory: {str(e)}", exc_info=True)
                                st.error(f"Update failed: {str(e)}")
        except Exception as e:
            logger.error(f"Error in inventory update tab: {str(e)}", exc_info=True)
            st.error(f"Error loading inventory: {str(e)}")

    # ── TAB 3: HISTORY ────────────────────────────────────────────────────────
    with tab3:
        try:
            logger.debug("Loading stock ledger history...")
            history_query = "SELECT * FROM stock_ledger ORDER BY timestamp DESC"
            history_df = pd.read_sql(history_query, service.engine)
            logger.info(f"Loaded {len(history_df)} history records")

            if not history_df.empty:
                if 'transaction_type' in history_df.columns:
                    st.dataframe(
                        history_df.style.map(color_action, subset=['transaction_type']),
                        use_container_width=True,
                        hide_index=True,
                    )
                else:
                    st.dataframe(history_df, use_container_width=True, hide_index=True)
            else:
                logger.info("No history records found")
                st.info("No history records found.")
        except Exception as e:
            logger.error(f"Error loading history: {str(e)}", exc_info=True)
            st.error(f"Failed to load history: {str(e)}")

    # ── TAB 4: BULK UPDATE ────────────────────────────────────────────────────
    with tab4:
        st.subheader("📥 Bulk Update Inventory from Excel")
        st.markdown("""
        Upload an Excel file with the following columns:
        - **sku**: Product SKU (REQUIRED)
        - **godown_stock_packs**: Number of packs in stock
        - **pack_multiplier**: Pieces per pack

        Example format:
        | sku | godown_stock_packs | pack_multiplier |
        |-----|-------------------|--------------------|
        | BEANIE-FOLD-2PLAIN-BLUE | 10 | 6 |
        | ARMSLEEVES-FULL-BLACK | 5 | 1 |
        """)

        uploaded_file = st.file_uploader(
            "Upload stock file",
            type=['xlsx', 'csv'],
            key='inv_bulk_upload',
        )

        if uploaded_file is not None:
            try:
                logger.info(f"Processing inventory upload: {uploaded_file.name}")
                df_upload = pd.read_excel(uploaded_file)

                if 'sku' not in df_upload.columns:
                    logger.error("SKU column missing from upload")
                    st.error("❌ Missing required column: 'sku'")
                else:
                    st.subheader("Preview of data to import:")
                    st.dataframe(df_upload.head(10), use_container_width=True)

                    st.subheader("Validation Results:")
                    total_rows = len(df_upload)
                    valid_rows = df_upload[
                        df_upload['sku'].notna() & (df_upload['sku'] != '')
                    ].shape[0]

                    logger.debug(f"Upload validation: Total={total_rows}, Valid={valid_rows}")

                    c1, c2, c3 = st.columns(3)
                    c1.metric("Total Rows", total_rows)
                    c2.metric("Valid Rows", valid_rows)
                    c3.metric("Invalid Rows", total_rows - valid_rows)

                    if valid_rows == 0:
                        logger.error("No valid records in upload file")
                        st.error("❌ No valid records found in the file.")
                    else:
                        if st.button("✅ Import Data", type="primary"):
                            try:
                                logger.info(f"Starting inventory import with {valid_rows} valid rows...")
                                engine = get_engine()

                                df_upload = df_upload[
                                    df_upload['sku'].notna() & (df_upload['sku'] != '')
                                ].copy()
                                df_upload['sku'] = df_upload['sku'].str.strip().str.upper()

                                if 'godown_stock_packs' not in df_upload.columns:
                                    df_upload['godown_stock_packs'] = 0
                                if 'pack_multiplier' not in df_upload.columns:
                                    df_upload['pack_multiplier'] = 1

                                df_upload['godown_stock_packs'] = pd.to_numeric(
                                    df_upload['godown_stock_packs'], errors='coerce'
                                ).fillna(0).astype(int)
                                df_upload['pack_multiplier'] = pd.to_numeric(
                                    df_upload['pack_multiplier'], errors='coerce'
                                ).fillna(1).astype(int)

                                st.write("🔍 **Debug Info:**")
                                st.write(f"SKUs to update: {df_upload['sku'].tolist()}")

                                updated_count = 0
                                not_found = []

                                conn = engine.raw_connection()
                                cursor = conn.cursor()

                                try:
                                    for _, row in df_upload.iterrows():
                                        sku = row['sku']
                                        packs = row['godown_stock_packs']
                                        multiplier = row['pack_multiplier']

                                        cursor.execute(
                                            "SELECT COUNT(*) FROM inventory_master WHERE sku = ?",
                                            (sku,),
                                        )
                                        exists = cursor.fetchone()[0] > 0

                                        if not exists:
                                            not_found.append(sku)
                                            logger.debug(f"SKU not found in database: {sku}")
                                        else:
                                            cursor.execute(
                                                """UPDATE inventory_master
                                                   SET godown_stock_packs = ?,
                                                       pack_multiplier = ?
                                                   WHERE sku = ?""",
                                                (packs, multiplier, sku),
                                            )
                                            updated_count += 1
                                            logger.debug(
                                                f"Updated {sku}: {packs} packs, {multiplier} multiplier"
                                            )

                                    conn.commit()
                                    clear_inventory_cache()
                                    logger.info(
                                        f"Inventory import completed: {updated_count} updated, "
                                        f"{len(not_found)} not found"
                                    )
                                finally:
                                    cursor.close()
                                    conn.close()

                                st.success(f"✅ Successfully updated {updated_count} records!")

                                if not_found:
                                    logger.warning(
                                        f"{len(not_found)} SKUs not found: {not_found[:5]}"
                                    )
                                    st.warning(
                                        f"⚠️ {len(not_found)} SKUs not found: "
                                        f"{', '.join(not_found[:5])}"
                                    )

                                st.rerun()
                            except Exception as e:
                                logger.error(f"Inventory import failed: {str(e)}", exc_info=True)
                                st.error(f"❌ Import failed: {str(e)}")
                                import traceback
                                st.error(traceback.format_exc())
            except Exception as e:
                logger.error(f"Error reading inventory upload file: {str(e)}", exc_info=True)
                st.error(f"❌ Error reading file: {str(e)}")

        # Download template
        st.divider()
        st.subheader("📋 Download Template")

        try:
            logger.debug("Generating inventory template...")
            df_template = service.get_godown_inventory()[
                ['sku', 'godown_stock_packs', 'pack_multiplier']
            ]

            excel_buffer = io.BytesIO()
            with pd.ExcelWriter(excel_buffer, engine='openpyxl') as writer:
                df_template.to_excel(writer, index=False, sheet_name='Inventory')
            excel_buffer.seek(0)

            st.download_button(
                label="📥 Download Current Inventory as Excel",
                data=excel_buffer.getvalue(),
                file_name="inventory_export.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )

            csv = df_template.to_csv(index=False)
            st.download_button(
                label="📥 Download Current Inventory as CSV",
                data=csv,
                file_name="inventory_export.csv",
                mime="text/csv",
            )
            logger.info("Inventory template generated successfully")
        except Exception as e:
            logger.warning(f"Template generation warning: {str(e)}")
            st.info(f"Template generation: {str(e)}")


# ──────────────────────────────────────────────────────────────────────────────
# Page Entry Point
# ──────────────────────────────────────────────────────────────────────────────

def render():
    """Render the Inventory Manager page.

    A mode-switcher banner at the top of the page lets users switch between:
    - Standard Mode: full tabbed desktop interface
    - Godown Mode: streamlined scan-and-update mobile interface
    """
    try:
        logger.info("Rendering Inventory Manager...")
        st.title("📦 Godown Inventory")

        try:
            service = InventoryService()
            logger.debug("InventoryService initialized")
        except Exception as e:
            logger.error(f"Failed to initialize InventoryService: {str(e)}", exc_info=True)
            st.error(f"Failed to initialize service: {str(e)}")
            return

        # ── Mode switcher — prominent banner on the page body ─────────────────
        # Also keep sidebar toggle for convenience on desktop
        st.sidebar.divider()
        st.sidebar.toggle(
            "🏭 Godown Mode",
            value=st.session_state.get("godown_mode_active", False),
            key="_sidebar_godown_sync",
            help="Switch to barcode scan-first mobile UI",
        )
        # Sync sidebar toggle → session state
        if "_sidebar_godown_sync" in st.session_state:
            st.session_state["godown_mode_active"] = st.session_state["_sidebar_godown_sync"]

        godown_mode = st.session_state.get("godown_mode_active", False)

        # ── Big visible mode switcher on the main page ────────────────────────
        st.markdown("""
        <style>
        .mode-banner {
            display: flex;
            gap: 12px;
            margin-bottom: 24px;
            flex-wrap: wrap;
        }
        .mode-btn {
            flex: 1;
            min-width: 160px;
            padding: 16px 20px;
            border-radius: 14px;
            border: 2px solid transparent;
            cursor: pointer;
            text-align: center;
            font-size: 1rem;
            font-weight: 700;
            transition: all 0.2s ease;
        }
        .mode-btn-active {
            background: linear-gradient(135deg, #1e3a5f, #0f2a45);
            border-color: #63b3ed;
            color: #93c5fd;
        }
        .mode-btn-inactive {
            background: rgba(255,255,255,0.04);
            border-color: rgba(255,255,255,0.1);
            color: #64748b;
        }
        </style>
        """, unsafe_allow_html=True)

        col_std, col_gdn = st.columns(2)
        with col_std:
            if st.button(
                "🖥️  Standard Mode\n\nFull desktop view with all tabs",
                key="btn_standard_mode",
                use_container_width=True,
                type="primary" if not godown_mode else "secondary",
            ):
                st.session_state["godown_mode_active"] = False
                st.rerun()
        with col_gdn:
            if st.button(
                "📱  Godown Mode\n\nScan barcodes — hardware or camera",
                key="btn_godown_mode",
                use_container_width=True,
                type="primary" if godown_mode else "secondary",
            ):
                st.session_state["godown_mode_active"] = True
                st.rerun()

        st.divider()

        if godown_mode:
            _render_godown_mode(service)
        else:
            _render_standard_mode(service)

    except Exception as e:
        logger.error(f"Critical error in Inventory Manager render: {str(e)}", exc_info=True)
        st.error(f"Critical error: {e}")
        st.info("Please try refreshing the page or check the application logs.")