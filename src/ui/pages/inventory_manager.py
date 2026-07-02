"""Inventory Manager UI Page.

Single tabbed interface with inline camera scanning on SKU fields:
- Current Balances: View inventory with export and low-stock alerts
- Update Stock: Add/remove stock; 📷 camera + hardware scanner; commit queue
  for batch operations without a separate scan mode
- Recent History: Ledger with date/SKU/type filters and camera search
- Bulk Update: Mass import from Excel
- Transfer to Shop: Move packs from godown to shop (camera-assisted SKU lookup)
- Movement Report: Per-SKU movement history with opening/closing balance
"""
import streamlit as st
import pandas as pd
import io
from datetime import date, timedelta
from src.core.services.inventory_service import InventoryService
from src.infrastructure.database import get_engine
from src.infrastructure.logger import get_logger
from src.core.cache import clear_inventory_cache
from src.ui.components.barcode_scanner import render_camera_scanner
from src.ui.components.mobile_style import inject_mobile_css
from src.ui.components.errors import show_error
from sqlalchemy import text

logger = get_logger(__name__)


REASON_CODES = {
    "Godown": {
        "ADD":    ["RECEIVED", "COUNT_CORRECTION", "ADJUSTMENT"],
        "REMOVE": ["SALE", "DAMAGED", "LOST", "COUNT_CORRECTION", "ADJUSTMENT"],
    },
    "Shop": {
        "ADD":    ["COUNT_CORRECTION", "RETURN", "ADJUSTMENT"],
        "REMOVE": ["SALE", "DAMAGED", "LOST", "COUNT_CORRECTION", "ADJUSTMENT"],
    },
}


def color_action(val):
    colors = {
        "ADD":        "green",
        "REMOVE":     "red",
        "STOCK_TAKE": "orange",
        "ADJUSTMENT": "steelblue",
    }
    return f'color: {colors.get(val, "grey")}; font-weight: bold'


# ──────────────────────────────────────────────────────────────────────────────
# Inline Camera / SKU Search Helper
# ──────────────────────────────────────────────────────────────────────────────

def _sku_scan_input(
    tab_key: str,
    label: str = "🔍 Scan / Search SKU",
    placeholder: str = "Type SKU or use 📷 camera…",
    camera_height: int = 400,
) -> str:
    """Renders a SKU text input with an inline 📷 camera toggle button.

    Hardware barcode scanners (USB/Bluetooth keyboard-emulation) work naturally
    through the text field. The 📷 button opens a camera scanner for phone/tablet.

    When the camera detects a barcode it closes itself, fills the text input,
    and triggers a rerun — so the value flows into any downstream selectbox on
    the same render cycle.

    Returns the current field value, stripped and uppercased.
    """
    input_key   = f"sku_typed_{tab_key}"
    cam_key     = f"cam_open_{tab_key}"
    counter_key = f"cam_counter_{tab_key}"
    pending_key = f"cam_pending_{tab_key}"

    # Transfer any pending scan result into the widget key BEFORE the widget
    # is instantiated. This is the only valid window — writing to session_state
    # after a widget renders raises StreamlitAPIException.
    # is_fresh_scan signals to the caller that a scan result was just applied
    # THIS render, so the caller can also pre-set downstream selectbox keys
    # (same rule: must happen before those widgets render).
    is_fresh_scan = False
    if pending_key in st.session_state:
        st.session_state[input_key] = st.session_state.pop(pending_key)
        is_fresh_scan = True

    col_in, col_cam = st.columns([7, 2])
    with col_in:
        typed = st.text_input(label, key=input_key, placeholder=placeholder,
                              autocomplete="off")
    with col_cam:
        st.markdown("<br>", unsafe_allow_html=True)
        cam_open = st.session_state.get(cam_key, False)
        if st.button(
            "📷 Close" if cam_open else "📷 Camera",
            key=f"cam_btn_{tab_key}",
            use_container_width=True,
        ):
            st.session_state[cam_key] = not cam_open
            st.rerun()

    if st.session_state.get(cam_key, False):
        st.info(
            "Point camera at barcode. "
            "Camera requires **HTTPS or localhost** when using a phone.",
            icon="📷",
        )
        render_camera_scanner(height=camera_height)
        counter = st.session_state.get(counter_key, 0)
        bridge = st.text_input(
            "Camera scan result",
            key=f"cam_bridge_{tab_key}_{counter}",
            label_visibility="collapsed",
            placeholder="Waiting for camera scan…",
            autocomplete="off",
        )
        if (bridge or "").strip():
            scanned = bridge.strip().upper()
            # Store in pending_key (not input_key — that widget is already rendered).
            # On the next render the pending value is transferred above before
            # the widget instantiates.
            st.session_state[pending_key]   = scanned
            st.session_state[cam_key]       = False
            st.session_state[counter_key]   = counter + 1
            st.rerun()

    return (typed or "").strip().upper(), is_fresh_scan


# ──────────────────────────────────────────────────────────────────────────────
# Tab 2 — Commit Queue (batch staging before writing to DB)
# ──────────────────────────────────────────────────────────────────────────────

def _init_tab2_queue():
    if "tab2_queue" not in st.session_state:
        st.session_state["tab2_queue"] = []
    if "tab2_committed" not in st.session_state:
        st.session_state["tab2_committed"] = []


def _render_tab2_queue(service: InventoryService):
    """Renders the staged-updates queue at the bottom of Update Stock tab."""
    queue     = st.session_state["tab2_queue"]
    committed = st.session_state["tab2_committed"]

    with st.expander(
        f"🗂️ Queue — {len(queue)} staged, ready to commit" if queue
        else "🗂️ Queue — empty  (use 'Add to Queue' to stage batch updates)",
        expanded=bool(queue),
    ):
        if queue:
            display = pd.DataFrame(queue)[
                ["sku", "location", "action", "quantity", "reason_code", "reason"]
            ].rename(columns={
                "sku": "SKU", "location": "Location", "action": "Action",
                "quantity": "Qty", "reason_code": "Reason", "reason": "Note",
            })
            st.dataframe(display, hide_index=True, use_container_width=True)

            col_commit, col_clear = st.columns([3, 1])
            with col_commit:
                if st.button(
                    f"✅ Commit All {len(queue)} Updates to Database",
                    type="primary",
                    use_container_width=True,
                    key="tab2_commit_all",
                ):
                    errors = []
                    newly_committed = []
                    for item in queue:
                        try:
                            if item["location"] == "Godown":
                                service.manage_godown_stock(
                                    item["sku"],
                                    item["quantity"],
                                    item.get("multiplier", 1),
                                    item["action"],
                                    item.get("reason", ""),
                                    item.get("reason_code", "ADJUSTMENT"),
                                )
                            else:
                                service.manage_shop_stock(
                                    item["sku"],
                                    item["quantity"],
                                    item["action"],
                                    item.get("reason_code", "ADJUSTMENT"),
                                    item.get("reason", ""),
                                )
                            newly_committed.append(item)
                            logger.info(
                                f"[Queue] Committed: {item['location']} {item['action']} "
                                f"{item['quantity']} for {item['sku']}"
                            )
                        except Exception as e:
                            errors.append(f"{item['sku']}: {e}")
                            logger.error(f"[Queue] Commit error for {item['sku']}: {e}")

                    clear_inventory_cache()
                    failed_skus = {err.split(":", 1)[0] for err in errors}
                    st.session_state["tab2_queue"]     = [it for it in queue if it["sku"] in failed_skus]
                    st.session_state["tab2_committed"] += newly_committed

                    if errors:
                        st.error(
                            f"{len(newly_committed)} saved, {len(errors)} failed:\n"
                            + "\n".join(f"- {e}" for e in errors)
                        )
                    else:
                        st.success(f"✅ {len(newly_committed)} update(s) committed to database.")
                    st.rerun()

            with col_clear:
                if st.button("🗑️ Clear", use_container_width=True, key="tab2_clear_queue"):
                    st.session_state["tab2_queue"] = []
                    st.rerun()

            with st.expander("Remove individual items"):
                for i, item in enumerate(queue):
                    ci, cd = st.columns([5, 1])
                    ci.write(
                        f"**{item['sku']}** — {item['location']} {item['action']} "
                        f"{item['quantity']}"
                    )
                    if cd.button("✕", key=f"tab2_del_{i}"):
                        st.session_state["tab2_queue"].pop(i)
                        st.rerun()

        if committed:
            st.divider()
            st.caption(f"Committed this session: {len(committed)} item(s)")
            c_df = pd.DataFrame(committed)[
                ["sku", "location", "action", "quantity", "reason_code"]
            ].rename(columns={
                "sku": "SKU", "location": "Location", "action": "Action",
                "quantity": "Qty", "reason_code": "Reason",
            })
            st.dataframe(c_df, hide_index=True, use_container_width=True)


# ──────────────────────────────────────────────────────────────────────────────
# Standard Mode — tabbed interface
# ──────────────────────────────────────────────────────────────────────────────

def _render_standard_mode(service: InventoryService):
    _init_tab2_queue()

    # Each section is a nested function (bodies unchanged). The launcher/router
    # at the bottom shows a grid of action cards and renders only the section
    # the user taps — mobile-first, warehouse-friendly.

    # ── SECTION: BALANCES ─────────────────────────────────────────────────────
    def _s_balances():
        try:
            logger.debug("Loading godown inventory balances...")
            df = service.get_godown_inventory()
            logger.info(f"Loaded inventory for {len(df)} SKUs")

            if df.empty:
                st.warning("No inventory records found.")
            else:
                df["Total Pieces"] = df["godown_stock_packs"] * df["pack_multiplier"]
                df["Low?"] = (
                    (df["reorder_point"] > 0)
                    & (df["godown_stock_packs"] <= df["reorder_point"])
                ).map({True: "⚠️", False: ""})
                st.dataframe(df, width="stretch", hide_index=True)

                # 1.4.4 — Export current balances
                dl1, dl2 = st.columns(2)
                _export_df = df.drop(columns=["Low?"], errors="ignore")
                dl1.download_button(
                    "⬇️ Download CSV",
                    data=_export_df.to_csv(index=False).encode("utf-8"),
                    file_name="inventory_balances.csv",
                    mime="text/csv",
                    use_container_width=True,
                )
                _xl = io.BytesIO()
                with pd.ExcelWriter(_xl, engine="openpyxl") as _w:
                    _export_df.to_excel(_w, index=False, sheet_name="Balances")
                _xl.seek(0)
                dl2.download_button(
                    "⬇️ Download Excel",
                    data=_xl.getvalue(),
                    file_name="inventory_balances.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True,
                )

                # 1.4.2 — Low Stock Alerts
                low_df = df[
                    (df["reorder_point"] > 0) & (df["godown_stock_packs"] <= df["reorder_point"])
                ].copy()
                if not low_df.empty:
                    st.divider()
                    st.markdown(f"### ⚠️ Low Stock Alerts &nbsp; `{len(low_df)} SKU(s)`")
                    st.caption("SKUs at or below their configured reorder point.")
                    alert = low_df[
                        ["sku", "category", "godown_stock_packs", "reorder_point", "reorder_qty"]
                    ].copy()
                    alert["gap"] = alert["reorder_point"] - alert["godown_stock_packs"]
                    alert.columns = [
                        "SKU", "Category", "On Hand (packs)",
                        "Reorder Point", "Suggested Reorder Qty", "Gap (packs needed)",
                    ]
                    st.dataframe(
                        alert.style.applymap(
                            lambda _: "color: #f97316; font-weight: bold",
                            subset=["On Hand (packs)"],
                        ),
                        hide_index=True,
                        use_container_width=True,
                    )
                    st.download_button(
                        "⬇️ Export Low Stock List (CSV)",
                        data=alert.to_csv(index=False).encode("utf-8"),
                        file_name="low_stock_alerts.csv",
                        mime="text/csv",
                    )

                # 1.3.1 — Set reorder levels per SKU
                if st.session_state.get("tab1_reorder_success"):
                    st.success(st.session_state["tab1_reorder_success"])

                with st.expander("⚙️ Set Reorder Levels per SKU"):
                    r_sku = st.selectbox("SKU", df["sku"].tolist(), key="reorder_sku")
                    cur = df[df["sku"] == r_sku].iloc[0]
                    st.caption(
                        f"Current → reorder point: **{int(cur['reorder_point'])}** · "
                        f"reorder qty: **{int(cur['reorder_qty'])}** · "
                        f"on hand: **{int(cur['godown_stock_packs'])} packs**"
                    )
                    with st.form("reorder_form"):
                        rc1, rc2 = st.columns(2)
                        r_point = rc1.number_input(
                            "Reorder Point (packs)", min_value=0,
                            value=int(cur["reorder_point"]), step=1,
                            help="Low-stock alert triggers at or below this level. 0 = off.",
                        )
                        r_qty = rc2.number_input(
                            "Reorder Qty (packs)", min_value=0,
                            value=int(cur["reorder_qty"]), step=1,
                            help="Suggested quantity to reorder when low.",
                        )
                        if st.form_submit_button("💾 Save Reorder Levels"):
                            try:
                                service.set_reorder_levels(r_sku, int(r_point), int(r_qty))
                                clear_inventory_cache()
                                st.session_state["tab1_reorder_success"] = (
                                    f"✅ Reorder levels saved for **{r_sku}** — "
                                    f"point: {int(r_point)} packs, qty: {int(r_qty)} packs."
                                )
                                st.rerun()
                            except Exception as e:
                                show_error(logger, f"Failed to save reorder levels for {r_sku}", e)
        except Exception as e:
            show_error(logger, "Error loading inventory balances", e)

    # ── SECTION: UPDATE STOCK ─────────────────────────────────────────────────
    def _s_update():
        try:
            df_inv = service.get_inventory_status()

            if df_inv.empty:
                st.warning("No inventory records found. Please add stock first.")
            else:
                # Persistent success banner — set on action, cleared on next action.
                if st.session_state.get("tab2_success"):
                    st.success(st.session_state["tab2_success"])

                # SKU search field — hardware scanner works via typing, 📷 button opens camera.
                # Returns (value, is_fresh_scan). When is_fresh_scan=True the value was just
                # set by a camera scan, so we also push it into the selectbox session state
                # key BEFORE the selectbox renders — the only valid window to do this.
                sku_hint, fresh_scan = _sku_scan_input(
                    "tab2",
                    label="🔍 Scan / Search SKU",
                    placeholder="Scan with hardware scanner, use 📷 camera, or type SKU…",
                )

                # Location, Action, SKU outside the form so the balance caption
                # and multiplier default refresh immediately on any change.
                c_loc, c_act, c_sku = st.columns(3)
                location = c_loc.selectbox("Location", ["Godown", "Shop"], key="tab2_location")
                action   = c_act.selectbox("Action",   ["ADD", "REMOVE"],  key="tab2_action")

                sku_options = df_inv["sku"].unique().tolist()
                # Drive the selectbox whenever the search field value CHANGES to an exact
                # SKU match. This covers camera scans (fresh_scan=True), hardware barcode
                # scanners (keyboard emulation + Enter), and fully typed SKUs — without the
                # field fighting manual dropdown picks (the field didn't change in that case).
                _prev2 = st.session_state.get("_prev_hint_tab2", "")
                if sku_hint != _prev2 and sku_hint in sku_options:
                    st.session_state["tab2_sku"] = sku_hint
                st.session_state["_prev_hint_tab2"] = sku_hint
                sku = c_sku.selectbox("Select SKU", sku_options, key="tab2_sku")

                current_row = df_inv[df_inv["sku"] == sku].iloc[0]

                if location == "Godown":
                    current_godown = int(current_row["godown_stock_packs"])
                    try:
                        default_mult = int(current_row["multiplier"])
                    except (ValueError, TypeError):
                        default_mult = 1

                    st.caption(f"Current godown balance: **{current_godown} packs**")

                    with st.form("godown_update_form"):
                        c3, c4 = st.columns(2)
                        num_packs   = c3.number_input("Number of Packs", min_value=1, step=1)
                        pack_size   = c4.number_input("Pieces per Pack", min_value=1, value=default_mult)
                        reason_code = st.selectbox("Reason", REASON_CODES["Godown"][action])
                        reason_note = st.text_input("Notes (Optional)", placeholder="e.g. Invoice #1234", autocomplete="off")

                        col_now, col_q = st.columns(2)
                        submit_now   = col_now.form_submit_button(
                            "✅ Update Now", type="primary", use_container_width=True
                        )
                        submit_queue = col_q.form_submit_button(
                            "➕ Add to Queue", use_container_width=True
                        )

                    if submit_now:
                        st.session_state.pop("tab2_success", None)
                        try:
                            service.manage_godown_stock(
                                sku, num_packs, pack_size, action, reason_note, reason_code
                            )
                            clear_inventory_cache()
                            logger.info(f"Godown updated: {sku} {action} {num_packs} packs")
                            st.session_state["tab2_success"] = (
                                f"✅ {action} {int(num_packs)} packs of **{sku}** "
                                f"({int(num_packs) * int(pack_size)} pcs) — {reason_code}"
                            )
                            # Reset scan field so the next scan starts clean.
                            st.session_state.pop("sku_typed_tab2", None)
                            st.rerun()
                        except Exception as e:
                            show_error(logger, "Error updating godown stock", e)

                    if submit_queue:
                        st.session_state.pop("tab2_success", None)
                        st.session_state["tab2_queue"].append({
                            "sku": sku, "location": "Godown", "action": action,
                            "quantity": int(num_packs), "multiplier": int(pack_size),
                            "reason_code": reason_code, "reason": reason_note or "",
                        })
                        st.session_state["tab2_success"] = (
                            f"➕ Queued: {action} {int(num_packs)} packs of **{sku}** — "
                            f"{len(st.session_state['tab2_queue'])} item(s) in queue"
                        )
                        st.session_state.pop("sku_typed_tab2", None)
                        st.rerun()

                else:  # Shop
                    current_shop = int(current_row["shop_stock_pieces"])
                    st.caption(f"Current shop balance: **{current_shop} pieces**")

                    with st.form("shop_update_form"):
                        num_pieces  = st.number_input("Number of Pieces", min_value=1, step=1)
                        reason_code = st.selectbox("Reason", REASON_CODES["Shop"][action])
                        reason_note = st.text_input(
                            "Notes (Optional)", placeholder="e.g. Damaged in store",
                            autocomplete="off",
                        )

                        col_now, col_q = st.columns(2)
                        submit_now   = col_now.form_submit_button(
                            "✅ Update Now", type="primary", use_container_width=True
                        )
                        submit_queue = col_q.form_submit_button(
                            "➕ Add to Queue", use_container_width=True
                        )

                    if submit_now:
                        st.session_state.pop("tab2_success", None)
                        try:
                            service.manage_shop_stock(
                                sku, num_pieces, action, reason_code, reason_note
                            )
                            clear_inventory_cache()
                            logger.info(f"Shop updated: {sku} {action} {num_pieces} pieces")
                            st.session_state["tab2_success"] = (
                                f"✅ {action} {int(num_pieces)} pcs of **{sku}** (Shop) — {reason_code}"
                            )
                            st.session_state.pop("sku_typed_tab2", None)
                            st.rerun()
                        except Exception as e:
                            show_error(logger, "Error updating shop stock", e)

                    if submit_queue:
                        st.session_state.pop("tab2_success", None)
                        st.session_state["tab2_queue"].append({
                            "sku": sku, "location": "Shop", "action": action,
                            "quantity": int(num_pieces), "multiplier": 1,
                            "reason_code": reason_code, "reason": reason_note or "",
                        })
                        st.session_state["tab2_success"] = (
                            f"➕ Queued: {action} {int(num_pieces)} pcs of **{sku}** (Shop) — "
                            f"{len(st.session_state['tab2_queue'])} item(s) in queue"
                        )
                        st.session_state.pop("sku_typed_tab2", None)
                        st.rerun()

                st.divider()
                _render_tab2_queue(service)

        except Exception as e:
            show_error(logger, "Error loading the update stock form", e)

    # ── TAB 3: HISTORY ────────────────────────────────────────────────────────
    def _s_history():
        try:
            logger.debug("Loading stock ledger history...")

            # SKU filter — camera-assisted for quick per-SKU history lookup.
            # Tab 3 has no selectbox, so we only need the value (no fresh_scan action needed).
            h_sku, _ = _sku_scan_input(
                "tab3",
                label="SKU filter (partial match, or scan barcode)",
                placeholder="Type SKU, scan barcode, or leave blank for all…",
            )

            fc2, fc3 = st.columns(2)
            with fc2:
                h_types = st.multiselect(
                    "Transaction Type",
                    ["ADD", "REMOVE", "STOCK_TAKE", "ADJUSTMENT"],
                    default=[],
                    key="hist_type_filter",
                )
            with fc3:
                today = date.today()
                h_dates = st.date_input(
                    "Date Range",
                    value=(today - timedelta(days=30), today),
                    key="hist_date_filter",
                )

            history_df = pd.read_sql(
                "SELECT * FROM stock_ledger ORDER BY timestamp DESC",
                service.engine,
            )
            logger.info(f"Loaded {len(history_df)} history records")

            if not history_df.empty:
                if h_sku:
                    history_df = history_df[
                        history_df["sku"].str.upper().str.contains(h_sku, na=False)
                    ]
                if h_types:
                    history_df = history_df[history_df["transaction_type"].isin(h_types)]
                if isinstance(h_dates, (list, tuple)) and len(h_dates) == 2:
                    history_df["_d"] = pd.to_datetime(
                        history_df["timestamp"], errors="coerce"
                    ).dt.date
                    history_df = history_df[
                        (history_df["_d"] >= h_dates[0]) & (history_df["_d"] <= h_dates[1])
                    ].drop(columns=["_d"])
                elif isinstance(h_dates, date):
                    history_df["_d"] = pd.to_datetime(
                        history_df["timestamp"], errors="coerce"
                    ).dt.date
                    history_df = history_df[history_df["_d"] == h_dates].drop(columns=["_d"])

                st.caption(f"{len(history_df)} record(s) shown")

                if not history_df.empty:
                    if "transaction_type" in history_df.columns:
                        st.dataframe(
                            history_df.style.map(color_action, subset=["transaction_type"]),
                            width="stretch",
                            hide_index=True,
                        )
                    else:
                        st.dataframe(history_df, width="stretch", hide_index=True)
                    st.download_button(
                        "⬇️ Export Filtered History (CSV)",
                        data=history_df.to_csv(index=False).encode("utf-8"),
                        file_name="stock_history.csv",
                        mime="text/csv",
                    )
                else:
                    st.info("No records match the current filters.")
            else:
                logger.info("No history records found")
                st.info("No history records found.")
        except Exception as e:
            show_error(logger, "Failed to load history", e)

    # ── TAB 4: BULK UPDATE ────────────────────────────────────────────────────
    def _s_bulk():
        st.subheader("📥 Bulk Update Inventory from Excel")
        st.markdown("""
Upload an Excel file with the following columns:
- **sku**: Product SKU (REQUIRED)
- **godown_stock_packs**: Number of packs in stock
- **pack_multiplier**: Pieces per pack

| sku | godown_stock_packs | pack_multiplier |
|-----|-------------------|-----------------|
| BEANIE-FOLD-2PLAIN-BLUE | 10 | 6 |
| ARMSLEEVES-FULL-BLACK | 5 | 1 |
        """)

        uploaded_file = st.file_uploader(
            "Upload stock file", type=["xlsx", "csv"], key="inv_bulk_upload"
        )

        if uploaded_file is not None:
            try:
                logger.info(f"Processing inventory upload: {uploaded_file.name}")
                df_upload = pd.read_excel(uploaded_file)

                if "sku" not in df_upload.columns:
                    logger.error("SKU column missing from upload")
                    st.error("❌ Missing required column: 'sku'")
                else:
                    st.subheader("Preview of data to import:")
                    st.dataframe(df_upload.head(10), use_container_width=True)

                    total_rows = len(df_upload)
                    valid_rows = df_upload[
                        df_upload["sku"].notna() & (df_upload["sku"] != "")
                    ].shape[0]

                    c1, c2, c3 = st.columns(3)
                    c1.metric("Total Rows", total_rows)
                    c2.metric("Valid Rows", valid_rows)
                    c3.metric("Invalid Rows", total_rows - valid_rows)

                    if valid_rows == 0:
                        st.error("❌ No valid records found in the file.")
                    else:
                        if st.button("✅ Import Data", type="primary"):
                            try:
                                logger.info(
                                    f"Starting inventory import with {valid_rows} valid rows..."
                                )
                                engine = get_engine()
                                df_upload = df_upload[
                                    df_upload["sku"].notna() & (df_upload["sku"] != "")
                                ].copy()
                                df_upload["sku"] = df_upload["sku"].str.strip().str.upper()

                                if "godown_stock_packs" not in df_upload.columns:
                                    df_upload["godown_stock_packs"] = 0
                                if "pack_multiplier" not in df_upload.columns:
                                    df_upload["pack_multiplier"] = 1

                                df_upload["godown_stock_packs"] = (
                                    pd.to_numeric(df_upload["godown_stock_packs"], errors="coerce")
                                    .fillna(0).clip(lower=0).astype(int)
                                )
                                df_upload["pack_multiplier"] = (
                                    pd.to_numeric(df_upload["pack_multiplier"], errors="coerce")
                                    .fillna(1).clip(lower=1).astype(int)
                                )

                                updated_count     = 0
                                not_found         = []
                                skipped_no_change = 0

                                for _, row in df_upload.iterrows():
                                    sku_val   = str(row["sku"])
                                    new_packs = int(row["godown_stock_packs"])
                                    new_mult  = int(row["pack_multiplier"])

                                    try:
                                        with engine.begin() as conn:
                                            existing = conn.execute(
                                                text(
                                                    "SELECT godown_stock_packs "
                                                    "FROM inventory_master WHERE sku = :sku"
                                                ),
                                                {"sku": sku_val},
                                            ).fetchone()

                                            if existing is None:
                                                not_found.append(sku_val)
                                                logger.debug(
                                                    f"Bulk import: SKU not found: {sku_val}"
                                                )
                                                continue

                                            old_packs = (
                                                existing[0] if existing[0] is not None else 0
                                            )
                                            delta = new_packs - old_packs

                                            conn.execute(
                                                text("""
                                                    UPDATE inventory_master
                                                    SET godown_stock_packs = :packs,
                                                        pack_multiplier    = :mult,
                                                        last_updated       = CURRENT_TIMESTAMP
                                                    WHERE sku = :sku
                                                """),
                                                {
                                                    "packs": new_packs,
                                                    "mult":  new_mult,
                                                    "sku":   sku_val,
                                                },
                                            )

                                            if delta != 0:
                                                conn.execute(
                                                    text("""
                                                        INSERT INTO stock_ledger
                                                            (sku, transaction_type, packs,
                                                             multiplier, total_pieces_affected,
                                                             reason_code, reason, updated_by,
                                                             running_balance)
                                                        VALUES
                                                            (:sku, 'STOCK_TAKE', :packs,
                                                             :mult, :total, 'STOCK_TAKE',
                                                             :reason, 'bulk_import', :rb)
                                                    """),
                                                    {
                                                        "sku":    sku_val,
                                                        "packs":  abs(delta),
                                                        "mult":   new_mult,
                                                        "total":  abs(delta) * new_mult,
                                                        "reason": (
                                                            f"Bulk stock take: "
                                                            f"{old_packs} → {new_packs} packs"
                                                        ),
                                                        "rb": new_packs,
                                                    },
                                                )
                                                updated_count += 1
                                                logger.debug(
                                                    f"Bulk import: {sku_val} "
                                                    f"{old_packs} → {new_packs} (delta {delta:+d})"
                                                )
                                            else:
                                                skipped_no_change += 1

                                    except Exception as row_err:
                                        logger.error(
                                            f"Bulk import: failed to process {sku_val}: {row_err}",
                                            exc_info=True,
                                        )
                                        st.warning(f"⚠️ Failed to update {sku_val}: {row_err}")

                                clear_inventory_cache()
                                logger.info(
                                    f"Bulk import complete: {updated_count} updated, "
                                    f"{len(not_found)} not found, {skipped_no_change} unchanged"
                                )
                                st.success(
                                    f"✅ Import complete: {updated_count} updated, "
                                    f"{skipped_no_change} unchanged, {len(not_found)} not found."
                                )
                                if not_found:
                                    st.warning(
                                        f"⚠️ {len(not_found)} SKUs not found: "
                                        f"{', '.join(not_found[:5])}"
                                    )
                                st.rerun()
                            except Exception as e:
                                show_error(logger, "Inventory import failed", e)
                                import traceback
                                st.error(traceback.format_exc())
            except Exception as e:
                show_error(logger, "Error reading the uploaded file", e)

        st.divider()
        st.subheader("📋 Download Template")
        try:
            logger.debug("Generating inventory template...")
            df_template = service.get_godown_inventory()[
                ["sku", "godown_stock_packs", "pack_multiplier"]
            ]
            xl_buf = io.BytesIO()
            with pd.ExcelWriter(xl_buf, engine="openpyxl") as writer:
                df_template.to_excel(writer, index=False, sheet_name="Inventory")
            xl_buf.seek(0)
            st.download_button(
                "📥 Download Current Inventory as Excel",
                data=xl_buf.getvalue(),
                file_name="inventory_export.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
            st.download_button(
                "📥 Download Current Inventory as CSV",
                data=df_template.to_csv(index=False),
                file_name="inventory_export.csv",
                mime="text/csv",
            )
            logger.info("Inventory template generated successfully")
        except Exception as e:
            logger.warning(f"Template generation warning: {str(e)}")
            st.info(f"Template generation: {str(e)}")

    # ── TAB 5: TRANSFER TO SHOP ───────────────────────────────────────────────
    def _s_transfer():
        try:
            logger.debug("Loading godown inventory for transfer form...")
            df_godown = service.get_godown_inventory()
            df_godown = df_godown[df_godown["godown_stock_packs"] > 0]

            if df_godown.empty:
                st.info("No godown stock available to transfer.")
            else:
                if st.session_state.get("tab5_success"):
                    st.success(st.session_state["tab5_success"])

                # Camera-assisted SKU lookup — fresh scan drives the dropdown below.
                sku_hint, fresh_scan = _sku_scan_input(
                    "tab5",
                    label="🔍 Scan SKU or search",
                    placeholder="Scan barcode or type SKU to pre-select below…",
                )

                sku_options = df_godown["sku"].unique().tolist()
                _prev5 = st.session_state.get("_prev_hint_tab5", "")
                if sku_hint != _prev5 and sku_hint in sku_options:
                    st.session_state["tab5_transfer_sku"] = sku_hint
                st.session_state["_prev_hint_tab5"] = sku_hint

                transfer_sku = st.selectbox(
                    "Select SKU (Godown stock only)",
                    sku_options,
                    key="tab5_transfer_sku",
                )

                godown_row = df_godown[df_godown["sku"] == transfer_sku].iloc[0]
                current_godown_packs = int(godown_row["godown_stock_packs"])
                try:
                    default_mult = int(godown_row["pack_multiplier"])
                except (ValueError, TypeError):
                    default_mult = 1

                st.caption(f"Godown balance: **{current_godown_packs} packs**")

                with st.form("transfer_form"):
                    c1, c2 = st.columns(2)
                    transfer_packs = c1.number_input(
                        "Packs to Transfer",
                        min_value=1,
                        max_value=current_godown_packs,
                        step=1,
                    )
                    pack_size = c2.number_input(
                        "Pieces per Pack", min_value=1, value=default_mult
                    )
                    if st.form_submit_button("Transfer to Shop"):
                        st.session_state.pop("tab5_success", None)
                        try:
                            logger.info(
                                f"Transferring {transfer_packs} packs of {transfer_sku} "
                                f"to shop (multiplier {pack_size})"
                            )
                            service.transfer_to_shop(transfer_sku, transfer_packs, pack_size)
                            clear_inventory_cache()
                            logger.info(
                                f"Transfer complete: {transfer_sku} "
                                f"{transfer_packs} packs → shop"
                            )
                            st.session_state["tab5_success"] = (
                                f"✅ Transferred {transfer_packs} packs "
                                f"({transfer_packs * pack_size} pcs) of **{transfer_sku}** to shop."
                            )
                            st.session_state.pop("sku_typed_tab5", None)
                            st.rerun()
                        except Exception as e:
                            show_error(logger, f"Transfer failed for {transfer_sku}", e)
        except Exception as e:
            show_error(logger, "Error loading the transfer form", e)

    # ── SECTION: MOVEMENT REPORT ──────────────────────────────────────────────
    def _s_report():
        try:
            logger.debug("Loading Movement Report...")
            st.markdown("#### 📈 Stock Movement Report")
            st.caption(
                "Select a SKU and date range to see opening balance, "
                "all movements, and closing balance for that period."
            )

            sku_list_df = pd.read_sql(
                "SELECT DISTINCT sku FROM stock_ledger ORDER BY sku", service.engine
            )
            if sku_list_df.empty:
                st.info("No ledger history found. Record some stock movements first.")
            else:
                all_ledger_skus = sku_list_df["sku"].tolist()
                today = date.today()

                # Camera-assisted SKU selection — fresh scan drives the dropdown below.
                sku_hint, fresh_scan = _sku_scan_input(
                    "tab6",
                    label="🔍 Scan SKU or search",
                    placeholder="Scan barcode or type SKU to pre-select below…",
                )
                _prev6 = st.session_state.get("_prev_hint_tab6", "")
                if sku_hint != _prev6 and sku_hint in all_ledger_skus:
                    st.session_state["mr_sku_select"] = sku_hint
                st.session_state["_prev_hint_tab6"] = sku_hint

                mr_col1, mr_col2, mr_col3 = st.columns([2, 2, 1])
                with mr_col1:
                    mr_sku = st.selectbox(
                        "SKU", all_ledger_skus, key="mr_sku_select"
                    )
                with mr_col2:
                    mr_dates = st.date_input(
                        "Date Range",
                        value=(today - timedelta(days=30), today),
                        key="mr_date_range",
                    )
                with mr_col3:
                    st.markdown("<br>", unsafe_allow_html=True)
                    mr_run = st.button(
                        "Generate", type="primary",
                        use_container_width=True, key="mr_generate_btn",
                    )

                if isinstance(mr_dates, (list, tuple)) and len(mr_dates) == 2:
                    mr_start, mr_end = str(mr_dates[0]), str(mr_dates[1])
                elif isinstance(mr_dates, date):
                    mr_start = mr_end = str(mr_dates)
                else:
                    mr_start = mr_end = str(today)

                if mr_run or mr_sku:
                    report    = service.get_movement_report(mr_sku, mr_start, mr_end)
                    movements = report["movements"]
                    opening   = report["opening_balance"]
                    closing   = report["closing_balance"]

                    s1, s2, s3, s4 = st.columns(4)
                    s1.metric(
                        "Opening Balance",
                        f"{opening} packs" if opening is not None else "Unknown",
                        help="Balance just before this period (from running_balance; "
                             "Unknown for pre-migration history).",
                    )
                    total_in = (
                        int(movements.loc[movements["transaction_type"] == "ADD", "packs"].sum())
                        if not movements.empty else 0
                    )
                    total_out = (
                        int(movements.loc[
                            movements["transaction_type"].isin(["REMOVE", "STOCK_TAKE"]),
                            "packs",
                        ].sum())
                        if not movements.empty else 0
                    )
                    s2.metric("Total In (packs)", total_in)
                    s3.metric("Total Out (packs)", total_out)
                    s4.metric(
                        "Closing Balance",
                        f"{closing} packs" if closing is not None else "Unknown",
                        help="running_balance of the last entry in the date range.",
                    )

                    st.divider()

                    if movements.empty:
                        st.info(
                            f"No ledger entries for **{mr_sku}** "
                            f"between {mr_start} and {mr_end}."
                        )
                    else:
                        st.markdown(f"**{len(movements)} movement(s)**")
                        if "transaction_type" in movements.columns:
                            st.dataframe(
                                movements.style.map(
                                    color_action, subset=["transaction_type"]
                                ),
                                width="stretch",
                                hide_index=True,
                            )
                        else:
                            st.dataframe(movements, width="stretch", hide_index=True)

                        export_df = movements.copy()
                        export_df.insert(0, "opening_balance", opening)
                        export_df["closing_balance_period"] = closing
                        st.download_button(
                            f"⬇️ Export Movement Report (CSV) — {mr_sku}",
                            data=export_df.to_csv(index=False).encode("utf-8"),
                            file_name=f"movement_{mr_sku}_{mr_start}_{mr_end}.csv",
                            mime="text/csv",
                        )
        except Exception as e:
            show_error(logger, "Error generating movement report", e)

    # ── Navigation: action-first launcher ─────────────────────────────────────
    # (key, emoji, short label, render fn). Order foregrounds the two daily
    # warehouse actions (Update, Transfer). Append a tuple to add a feature card.
    sections = [
        ("update",   "➕", "Update",   _s_update),
        ("transfer", "🔄", "Transfer", _s_transfer),
        ("balances", "📊", "Balances", _s_balances),
        ("history",  "📜", "History",  _s_history),
        ("report",   "📈", "Report",   _s_report),
        ("bulk",     "📥", "Bulk",     _s_bulk),
    ]
    labels = {key: lbl for key, _e, lbl, _ in sections}
    emojis = {key: e   for key, e, _l, _ in sections}
    funcs  = {key: fn  for key, _e, _l, fn in sections}
    active = st.session_state.get("inv_section")

    if active not in funcs:
        # Launcher grid — two square icon cards per row (kept 2-up on mobile
        # via the .st-key-inv_launcher CSS scope).
        st.caption("Choose an action")
        with st.container(key="inv_launcher"):
            cols = st.columns(2)
            for i, (key, emoji, lbl, _fn) in enumerate(sections):
                with cols[i % 2]:
                    if st.button(emoji, key=f"nav_{key}", use_container_width=True):
                        st.session_state["inv_section"] = key
                        st.rerun()
                    st.markdown(
                        f"<div class='nav-card-label'>{lbl}</div>",
                        unsafe_allow_html=True,
                    )
        return

    # A section is active — show Back + render only that section.
    if st.button("⬅️  Back to Menu", key="nav_back", use_container_width=True):
        st.session_state.pop("inv_section", None)
        st.rerun()
    st.markdown(f"### {emojis[active]} {labels[active]}")
    funcs[active]()


# ──────────────────────────────────────────────────────────────────────────────
# Page Entry Point
# ──────────────────────────────────────────────────────────────────────────────

def render():
    """Render the Inventory Manager page."""
    try:
        logger.info("Rendering Inventory Manager...")
        inject_mobile_css()
        st.title("📦 Godown Inventory")

        try:
            service = InventoryService()
            logger.debug("InventoryService initialized")
        except Exception as e:
            show_error(logger, "Failed to initialize inventory service", e)
            return

        _render_standard_mode(service)

    except Exception as e:
        show_error(logger, "Critical error in Inventory Manager", e)
        st.info("Please try refreshing the page or check the application logs.")
