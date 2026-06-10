"""Supplier Manager UI Page.

Provides interfaces for managing supplier master data including:
- All Suppliers: Read-only view of all suppliers with status
- Add Supplier: Create new supplier with live code preview
- Manage Supplier: Edit details and deactivate / bulk-reassign
"""
import streamlit as st
import pandas as pd
from src.core.services.supplier_service import SupplierService
from src.infrastructure.logger import get_logger, DataValidationException

logger = get_logger(__name__)


def _status_style(val):
    """Style the Status column: Active = green, Inactive = grey."""
    if val == "Active":
        return "color: green; font-weight: bold"
    return "color: grey"


def render():
    """Render the Supplier Manager page."""
    try:
        logger.info("Rendering Supplier Manager...")
        st.title("🏭 Supplier Master")

        try:
            service = SupplierService()
        except Exception as e:
            logger.error(f"Failed to initialize SupplierService: {e}", exc_info=True)
            st.error(f"Failed to initialize service: {e}")
            return

        tab1, tab2, tab3 = st.tabs([
            "📋 All Suppliers",
            "➕ Add Supplier",
            "✏️ Manage Supplier",
        ])

        # ── TAB 1: ALL SUPPLIERS ──────────────────────────────────────────────
        with tab1:
            try:
                df = service.get_all_suppliers(active_only=False)

                if df.empty:
                    st.info("No suppliers found. Add your first supplier in the **Add Supplier** tab.")
                else:
                    display = df[["supplier_code", "name", "contact_name",
                                  "lead_time_days", "payment_terms", "is_active"]].copy()
                    display["Status"] = display["is_active"].map(
                        {1: "Active", 0: "Inactive"}
                    )
                    display = display.drop(columns=["is_active"])
                    display = display.rename(columns={
                        "supplier_code": "Supplier Code",
                        "name":          "Name",
                        "contact_name":  "Contact Name",
                        "lead_time_days": "Lead Time (days)",
                        "payment_terms": "Payment Terms",
                    })

                    styled = display.style.map(_status_style, subset=["Status"])
                    st.dataframe(styled, use_container_width=True, hide_index=True)

                    total    = len(df)
                    active   = int(df["is_active"].sum())
                    inactive = total - active
                    st.caption(f"Showing {total} supplier(s) ({active} active, {inactive} inactive).")

            except Exception as e:
                logger.error(f"Tab 1 error: {e}", exc_info=True)
                st.error(f"Error loading suppliers: {e}")

        # ── TAB 2: ADD SUPPLIER ───────────────────────────────────────────────
        with tab2:
            try:
                # supplier_code lives OUTSIDE the form so the live preview caption
                # updates on every keystroke. Placing it inside a form would freeze
                # the caption until submission (stale-inside-form antipattern).
                supplier_code_input = st.text_input(
                    "Supplier Code *",
                    key="add_supplier_code",
                    help="Unique identifier. Will be stored in UPPER CASE.",
                )
                preview = supplier_code_input.strip().upper() or "—"
                st.caption(f"Will be stored as: **{preview}**")

                with st.form("add_supplier_form"):
                    name         = st.text_input("Supplier Name *")
                    contact_name  = st.text_input("Contact Name")
                    contact_email = st.text_input("Contact Email")
                    contact_phone = st.text_input("Contact Phone")
                    lead_time     = st.number_input(
                        "Lead Time (Days)", min_value=1, value=10, step=1
                    )
                    payment_terms = st.text_input(
                        "Payment Terms", placeholder="e.g. Net 30, Advance"
                    )
                    notes = st.text_area("Notes")
                    submitted = st.form_submit_button("Add Supplier")

                if submitted:
                    # Read supplier_code from session_state — set by the widget
                    # outside the form which has already been evaluated this run.
                    code = st.session_state.get("add_supplier_code", "").strip()
                    try:
                        service.add_supplier({
                            "supplier_code": code,
                            "name":          name,
                            "contact_name":  contact_name,
                            "contact_email": contact_email,
                            "contact_phone": contact_phone,
                            "lead_time_days": lead_time,
                            "payment_terms": payment_terms,
                            "notes":         notes,
                        })
                        st.success(f"✅ {code.upper()} — {name} added.")
                        st.rerun()
                    except DataValidationException as e:
                        st.error(str(e))
                    except Exception as e:
                        logger.error(f"add_supplier error: {e}", exc_info=True)
                        st.error(f"Unexpected error: {e}")

            except Exception as e:
                logger.error(f"Tab 2 error: {e}", exc_info=True)
                st.error(f"Error in Add Supplier tab: {e}")

        # ── TAB 3: MANAGE SUPPLIER ────────────────────────────────────────────
        with tab3:
            try:
                all_df = service.get_all_suppliers(active_only=False)

                if all_df.empty:
                    st.info("No suppliers found. Add your first supplier in the **Add Supplier** tab.")
                else:
                    # Build option list for the single selectbox that drives both
                    # Edit and Deactivate sections. Inactive suppliers are labelled
                    # so the user can still edit them, but not reassign to them.
                    def _option_label(row):
                        suffix = " [Inactive]" if row["is_active"] == 0 else ""
                        return f"{row['supplier_code']} — {row['name']}{suffix}"

                    option_labels = all_df.apply(_option_label, axis=1).tolist()
                    code_by_label = dict(zip(option_labels, all_df["supplier_code"].tolist()))

                    # Single selectbox — OUTSIDE any form so both sections below
                    # rerender immediately when the selection changes.
                    selected_label = st.selectbox(
                        "Select Supplier",
                        options=option_labels,
                        key="tab3_supplier_code",
                    )
                    selected_code = code_by_label[selected_label]
                    supplier = service.get_supplier_by_code(selected_code)

                    if supplier is None:
                        st.error(f"Could not load supplier '{selected_code}'.")
                    else:
                        st.divider()

                        # ── Section A: Edit ───────────────────────────────────
                        st.subheader("Edit Details")
                        st.text(f"Supplier Code: {selected_code}  (cannot be changed)")

                        with st.form("edit_supplier_form"):
                            e_name          = st.text_input("Supplier Name *",  value=supplier.get("name", ""))
                            e_contact_name  = st.text_input("Contact Name",     value=supplier.get("contact_name") or "")
                            e_contact_email = st.text_input("Contact Email",    value=supplier.get("contact_email") or "")
                            e_contact_phone = st.text_input("Contact Phone",    value=supplier.get("contact_phone") or "")
                            e_lead_time     = st.number_input(
                                "Lead Time (Days)",
                                min_value=1,
                                value=int(supplier.get("lead_time_days", 10)),
                                step=1,
                            )
                            e_payment_terms = st.text_input("Payment Terms",   value=supplier.get("payment_terms") or "")
                            e_notes         = st.text_area("Notes",             value=supplier.get("notes") or "")
                            save_clicked = st.form_submit_button("Save Changes")

                        if save_clicked:
                            try:
                                service.update_supplier(selected_code, {
                                    "name":          e_name,
                                    "contact_name":  e_contact_name,
                                    "contact_email": e_contact_email,
                                    "contact_phone": e_contact_phone,
                                    "lead_time_days": e_lead_time,
                                    "payment_terms": e_payment_terms,
                                    "notes":         e_notes,
                                })
                                st.success(f"✅ {selected_code} updated.")
                                st.rerun()
                            except DataValidationException as e:
                                st.error(str(e))
                            except Exception as e:
                                logger.error(f"update_supplier error: {e}", exc_info=True)
                                st.error(f"Unexpected error: {e}")

                        st.divider()

                        # ── Section B: Deactivate ─────────────────────────────
                        st.subheader("Deactivate")

                        if supplier.get("is_active") == 0:
                            st.info("This supplier is already inactive.")
                        else:
                            products_df = service.get_products_for_supplier(selected_code)
                            n_products = len(products_df)

                            st.write(f"**Products assigned to this supplier: {n_products}**")
                            if n_products > 0:
                                st.dataframe(
                                    products_df,
                                    use_container_width=True,
                                    hide_index=True,
                                )

                            if n_products == 0:
                                # Direct deactivation path — two-step confirmation.
                                # Checkbox is OUTSIDE any form so checking it
                                # immediately rerenders and reveals the button.
                                st.warning(
                                    f"⚠️ Deactivating **{selected_code}** will remove it "
                                    "from all dropdowns."
                                )
                                confirmed = st.checkbox(
                                    "I confirm I want to deactivate this supplier",
                                    key="deactivate_confirm",
                                )
                                if confirmed:
                                    if st.button("Deactivate", type="primary", key="btn_deactivate"):
                                        try:
                                            service.deactivate_supplier(selected_code)
                                            st.success(f"✅ {selected_code} deactivated.")
                                            st.rerun()
                                        except DataValidationException as e:
                                            st.error(str(e))
                                        except Exception as e:
                                            logger.error(f"deactivate_supplier error: {e}", exc_info=True)
                                            st.error(f"Unexpected error: {e}")

                            else:
                                # Bulk reassign path — must pick a target before deactivating.
                                st.info(
                                    f"This supplier has **{n_products}** assigned product(s). "
                                    "To deactivate, reassign them to another supplier first."
                                )

                                # Build active-supplier options excluding the current one.
                                active_others = all_df[
                                    (all_df["is_active"] == 1) &
                                    (all_df["supplier_code"] != selected_code)
                                ]

                                if active_others.empty:
                                    st.error(
                                        "No other active suppliers available. "
                                        "Add another supplier before deactivating this one."
                                    )
                                else:
                                    reassign_labels = (
                                        active_others["supplier_code"] + " — " + active_others["name"]
                                    ).tolist()
                                    reassign_code_by_label = dict(
                                        zip(reassign_labels, active_others["supplier_code"].tolist())
                                    )

                                    # selectbox OUTSIDE any form — drives the confirmation below.
                                    reassign_label = st.selectbox(
                                        f"Reassign all {n_products} product(s) to:",
                                        options=reassign_labels,
                                        key="reassign_target",
                                    )
                                    reassign_to = reassign_code_by_label[reassign_label]

                                    # Confirmation checkbox OUTSIDE any form.
                                    confirmed = st.checkbox(
                                        "I confirm reassignment and deactivation",
                                        key="reassign_confirm",
                                    )
                                    if confirmed:
                                        if st.button(
                                            "Reassign and Deactivate",
                                            type="primary",
                                            key="btn_reassign_deactivate",
                                        ):
                                            try:
                                                count = service.bulk_reassign_and_deactivate(
                                                    selected_code, reassign_to
                                                )
                                                st.success(
                                                    f"✅ {count} product(s) reassigned to "
                                                    f"**{reassign_to}**. "
                                                    f"**{selected_code}** deactivated."
                                                )
                                                st.info(
                                                    "ℹ️ Supplier Product Codes were not updated — "
                                                    "they still reflect the previous supplier's codes. "
                                                    "Review them in **Data Manager** if needed."
                                                )
                                                st.rerun()
                                            except DataValidationException as e:
                                                st.error(str(e))
                                            except Exception as e:
                                                logger.error(f"bulk_reassign error: {e}", exc_info=True)
                                                st.error(f"Unexpected error: {e}")

            except Exception as e:
                logger.error(f"Tab 3 error: {e}", exc_info=True)
                st.error(f"Error in Manage Supplier tab: {e}")

    except Exception as e:
        logger.critical(f"Supplier Manager render failed: {e}", exc_info=True)
        st.error(f"Page failed to render: {e}")
