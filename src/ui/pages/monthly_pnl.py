"""Monthly P&L tab — actual profit from marketplace settlement files.

Presentation only: reading files, matching SKUs and all arithmetic live in
PnlService / settlement_parsers.
"""
import pandas as pd
import streamlit as st

from src.core.services.pnl_service import PnlService
from src.infrastructure.logger import get_logger, DataValidationException
from src.infrastructure.settlement_parsers import PLATFORM_LABELS
from src.ui.cache_adapter import clear_all_caches, get_seller_accounts
from src.ui.components.catalog_columns import FULFILMENT_OPTIONS
from src.ui.components.errors import show_error

logger = get_logger(__name__)

_FLASH = "pnl_flash"


def _inr(x) -> str:
    """Whole rupees with Indian grouping: 372223 -> ₹3,72,223."""
    if x is None or pd.isna(x):
        return "—"
    digits = str(int(round(abs(x))))
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        pairs = []
        while len(head) > 2:
            pairs.insert(0, head[-2:])
            head = head[:-2]
        digits = ",".join(([head] if head else []) + pairs + [tail])
    return f"{'-' if x < 0 else ''}₹{digits}"


def render():
    try:
        service = PnlService()
    except Exception as e:
        show_error(logger, "Failed to start the P&L service", e)
        return

    if _FLASH in st.session_state:
        st.success(st.session_state.pop(_FLASH))

    accounts = get_seller_accounts()
    account_names = dict(accounts)
    months = service.available_months()

    _render_import(service, accounts, expanded=not months)
    _render_imports_list(service, account_names)

    if not months:
        st.info(
            "Upload a month's payment reports above to see the P&L. You need, per seller "
            "account: Amazon **Date Range Report → Transaction** (CSV), Flipkart "
            "**Settled Transactions** (Excel) and Meesho **Order Payments** (Excel)."
        )
        return

    st.divider()
    c1, c2, c3 = st.columns(3)
    month = c1.selectbox(
        "Month (money settled)", months, key="pnl_month",
        format_func=lambda m: pd.Timestamp(f"{m}-01").strftime("%B %Y"),
    )
    acct_options = [None] + [a[0] for a in accounts]
    account = c2.selectbox(
        "Seller account", acct_options, key="pnl_account",
        format_func=lambda a: "All accounts" if a is None else account_names.get(a, a),
    )
    platform = c3.selectbox(
        "Marketplace", [None, "amazon", "flipkart", "meesho"], key="pnl_platform",
        format_func=lambda p: "All marketplaces" if p is None else PLATFORM_LABELS[p],
    )

    try:
        res = service.monthly_pnl(month, account_id=account, platform=platform)
    except Exception as e:
        show_error(logger, "Failed to calculate the monthly P&L", e)
        return
    if res.get("empty"):
        st.info("No settlement data for this selection.")
        return

    for w in res["warnings"]:
        st.warning(f"⚠️ {w}")

    summary = res["summary"]
    revenue = summary.loc["Revenue (excl. GST)", "Total"]
    contribution = summary.loc["Contribution", "Total"]
    sold, returned = res["units"]["sold"], res["units"]["returned"]

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Revenue (excl. GST)", _inr(revenue))
    m2.metric("Contribution", _inr(contribution),
              help="Revenue minus goods cost, packaging, fees, shipping and ads, plus "
                   "the GST credit on fees. Fixed costs (rent, salaries) are not deducted yet.")
    m3.metric("Margin", f"{contribution / revenue * 100:.1f}%" if revenue else "—")
    m4.metric("Units sold / returned", f"{sold:,} / {returned:,}",
              help=f"{returned / sold * 100:.1f}% returned" if sold else None)

    st.subheader("Profit & loss")
    st.caption(
        "Revenue excludes the GST you collect. Fees, shipping and ads exclude the GST "
        "you reclaim, which is shown as a credit. TCS and TDS are withheld but "
        "recovered later, so they are not costs. Goods cost counts only units "
        "customers kept; packaging counts every pack shipped."
    )
    st.dataframe(
        summary.style.format(_inr).apply(
            lambda row: ["font-weight: bold" if row.name in
                         ("Contribution before GST credit", "Contribution") else ""] * len(row),
            axis=1,
        ),
        width="stretch",
    )

    _render_unmatched(service, res["unmatched"])
    _render_by_sku(res["by_sku"], month)
    _render_cash(res["cash"])


def _render_import(service, accounts, expanded):
    with st.expander("📤 Import payment reports", expanded=expanded):
        st.caption(
            "One seller account at a time. File names don't matter — each file is "
            "recognised from its contents. A file, or a period already imported for "
            "the same account and marketplace, is refused so nothing is counted twice."
        )
        names = [a[1] for a in accounts]
        account_label = st.selectbox("These files belong to", names, key="pnl_import_account")
        account_id = next(a[0] for a in accounts if a[1] == account_label)
        files = st.file_uploader(
            "Settlement files", type=["csv", "xlsx"], accept_multiple_files=True,
            key="pnl_files", label_visibility="collapsed",
        )
        if not files:
            return

        previews, readable = [], []
        for f in files:
            data = f.getvalue()
            try:
                p = service.preview_file(data, f.name)
                previews.append({
                    "File": f.name, "Marketplace": p["platform_label"],
                    "From": p["date_from"], "To": p["date_to"],
                    "Units sold": p["units"], "Returned": p["returned_units"],
                    "Settled": _inr(p["settlement_total"]), "Status": "Ready",
                })
                readable.append((f.name, data))
            except DataValidationException as e:
                previews.append({"File": f.name, "Status": f"❌ {e}"})
            except Exception as e:
                logger.error(f"Preview failed for {f.name}: {e}", exc_info=True)
                previews.append({"File": f.name, "Status": "❌ Could not read this file."})
        st.dataframe(pd.DataFrame(previews), hide_index=True, width="stretch")

        if readable and st.button(
            f"Import {len(readable)} file(s) for {account_label}", type="primary", key="pnl_import_btn"
        ):
            done, failed = [], []
            for name, data in readable:
                try:
                    r = service.import_file(data, name, account_id)
                    done.append(f"{r['platform_label']} {r['date_from']} → {r['date_to']}")
                except DataValidationException as e:
                    failed.append(f"{name}: {e}")
                except Exception as e:
                    logger.error(f"Import failed for {name}: {e}", exc_info=True)
                    failed.append(f"{name}: unexpected error — see logs")
            for msg in failed:
                st.error(f"❌ {msg}")
            if done:
                clear_all_caches()
                st.session_state[_FLASH] = f"✅ Imported for {account_label}: " + "; ".join(done)
                st.session_state.pop("pnl_files", None)
                st.rerun()


def _render_imports_list(service, account_names):
    imports = service.list_imports()
    if imports.empty:
        return
    with st.expander(f"🗂️ Imported files ({len(imports)})"):
        view = imports.copy()
        view["platform"] = view["platform"].map(PLATFORM_LABELS)
        view["account_id"] = view["account_id"].map(lambda a: account_names.get(a, a))
        view["settlement_total"] = view["settlement_total"].map(_inr)
        view.columns = ["ID", "Marketplace", "Account", "From", "To", "Rows",
                        "Settled", "File", "Imported at"]
        st.dataframe(view, hide_index=True, width="stretch")

        st.caption("Imported under the wrong account, or need to replace a file? Delete it and import again.")
        c1, c2, c3 = st.columns([3, 2, 1])
        options = {
            f"#{r.import_id} — {PLATFORM_LABELS[r.platform]} {account_names.get(r.account_id, r.account_id)} "
            f"{r.date_from} → {r.date_to}": int(r.import_id)
            for r in imports.itertuples()
        }
        choice = c1.selectbox("Import to delete", list(options), key="pnl_delete_choice",
                              label_visibility="collapsed")
        confirm = c2.checkbox("Yes, delete it", key="pnl_delete_confirm")
        if c3.button("Delete", disabled=not confirm, key="pnl_delete_btn"):
            try:
                service.delete_import(options[choice])
                clear_all_caches()
                st.session_state[_FLASH] = f"🗑️ Deleted {choice}."
                st.session_state.pop("pnl_delete_confirm", None)
                st.rerun()
            except DataValidationException as e:
                st.error(f"❌ {e}")


def _render_unmatched(service, unmatched):
    if unmatched.empty:
        return
    st.subheader("🔗 Match SKUs that are not in the catalog")
    st.caption(
        "The marketplace reported these SKUs but no listing in the catalog has them, so "
        "their goods cost is unknown. Pick the Pack SKU each one sells; this adds the "
        "listing to the catalog (set its price later in Catalog → Listings)."
    )
    from src.core.services.catalog_service import CatalogService
    packs = CatalogService().get_pack_dropdown()

    grid = pd.DataFrame({
        "Marketplace": unmatched["platform"].map(PLATFORM_LABELS),
        "Account": unmatched["account_id"],
        "Reported SKU": unmatched["sku_raw"],
        "Units sold": unmatched["units"],
        "Pack SKU": unmatched["suggested_pack"].where(unmatched["suggested_pack"].isin(packs), None),
        "Fulfilment": "easy_ship",
    })
    edited = st.data_editor(
        grid, hide_index=True, width="stretch", key="pnl_unmatched_editor",
        disabled=["Marketplace", "Account", "Reported SKU", "Units sold"],
        column_config={
            "Pack SKU": st.column_config.SelectboxColumn("Pack SKU", options=packs,
                                                          help="Pre-filled when there is an obvious match."),
            "Fulfilment": st.column_config.SelectboxColumn(
                "Fulfilment", options=list(FULFILMENT_OPTIONS.values()), required=True),
        },
    )
    chosen = edited[edited["Pack SKU"].notna()]
    if st.button(f"Save {len(chosen)} match(es)", disabled=chosen.empty, key="pnl_match_btn"):
        saved, errors = 0, []
        reverse = {v: k for k, v in PLATFORM_LABELS.items()}
        for r in chosen.itertuples(index=False):
            try:
                service.map_unmatched_sku(
                    r[2], reverse[r[0]], r[1], r[4], fulfilment_type=r[5]
                )
                saved += 1
            except DataValidationException as e:
                errors.append(f"{r[2]}: {e}")
        for e in errors:
            st.error(f"❌ {e}")
        if saved:
            clear_all_caches()
            st.session_state[_FLASH] = f"✅ Matched {saved} SKU(s) to the catalog."
            st.session_state.pop("pnl_unmatched_editor", None)
            st.rerun()


def _render_by_sku(by_sku, month):
    st.subheader("Per SKU")
    st.caption(
        "Charges without a SKU (Easy Ship labels, ads, fee refunds, storage) are shared "
        "across SKUs by units. Worst first."
    )
    status = st.multiselect(
        "Show", ["OK", "No cost", "Not in catalog"],
        default=["OK", "No cost", "Not in catalog"], key="pnl_sku_status",
    )
    view = by_sku[by_sku["status"].isin(status)]
    cols = {
        "platform": "Marketplace", "sku_raw": "Reported SKU", "master_sku": "Master SKU",
        "units": "Sold", "returned": "Returned", "return_pct": "Return %",
        "revenue": "Revenue", "cogs": "Goods cost", "fees": "Fees", "shipping": "Shipping",
        "ads": "Ads", "contribution": "Contribution", "contribution_per_unit": "Per unit kept",
        "status": "Status",
    }
    table = view[list(cols)].rename(columns=cols)
    money = ["Revenue", "Goods cost", "Fees", "Shipping", "Ads", "Contribution", "Per unit kept"]
    st.dataframe(
        table.style.format({c: _inr for c in money} | {"Return %": "{:.0f}%"}, na_rep="—"),
        hide_index=True, width="stretch", height=420,
    )
    st.download_button(
        "⬇️ Download per-SKU table (CSV)", table.to_csv(index=False).encode("utf-8"),
        file_name=f"pnl_by_sku_{month}.csv", mime="text/csv", key="pnl_sku_download",
    )


def _render_cash(cash):
    with st.expander("💵 Cash view (for matching against the bank)"):
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Settled by marketplaces", _inr(cash["settled"]))
        c2.metric("TCS withheld", _inr(cash["tcs"]), help="Recovered in your GST return.")
        c3.metric("TDS withheld", _inr(cash["tds"]), help="Recovered in your income tax return.")
        c4.metric("Still held by Amazon", _inr(cash["deferred"]),
                  help="Deferred until delivery is confirmed; counted in this month's P&L.")
        st.caption(
            f"Output GST collected from customers: {_inr(cash['output_gst'])} — owed to "
            "the government, so it is not revenue."
        )
