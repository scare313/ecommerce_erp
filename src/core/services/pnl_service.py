"""Actual monthly profit & loss from marketplace settlement reports.

Flow:
    upload file -> settlement_parsers (normalised rows) -> settlement_lines
    month report -> match each sold SKU to listing -> pack -> product
                 -> apply costs -> P&L by marketplace / account / SKU

Accounting rules (MARKETPLACE_PNL_HANDOFF §7):
    revenue          = what customers paid, EXCLUDING output GST
    goods cost       = landed cost x units customers KEPT (sold - returned)
    packaging        = packaging cost x packs SHIPPED (a return wastes it)
    fees / shipping / ads are charged excluding GST; the GST on them is input
    tax credit, reported separately and not a cost
    TCS / TDS        = withheld and recovered later — never a cost
    Amazon payouts ("Transfer") were already excluded by the reader.

Basis: money SETTLED in the month (the date each line was paid), which is
what the reports contain. Orders placed in a month but settled the next
appear in the next month.

Nothing is guessed silently: SKUs that do not match the catalog, products
without a landed cost and GST rates that had to be assumed are all returned
as warnings with the units and money they affect.
"""
import hashlib
import re

import pandas as pd
from sqlalchemy import text

from src.infrastructure.database import get_engine
from src.infrastructure.logger import get_logger, DataValidationException
from src.infrastructure.settlement_parsers import (
    parse_settlement_file, PLATFORM_LABELS,
)

logger = get_logger(__name__)

DEFAULT_GST_RATE = 18.0  # used only when neither the report nor the catalog gives one

_LINE_COLUMNS = [
    "txn_date", "order_id", "line_type", "sku_raw", "units", "returned_units",
    "sales_incl_gst", "gst_collected", "gst_rate", "fees_ex_gst", "shipping_ex_gst",
    "ads_ex_gst", "other_ex_gst", "itc", "tcs", "tds", "settlement_value", "is_deferred",
]

# P&L rows in display order: (key, label)
PNL_ROWS = [
    ("revenue", "Revenue (excl. GST)"),
    ("cogs", "Goods cost (units kept)"),
    ("packaging", "Packaging (packs shipped)"),
    ("fees", "Marketplace fees"),
    ("shipping", "Shipping / FBA"),
    ("ads", "Advertising"),
    ("other", "Reimbursements & other"),
    ("contribution_before_itc", "Contribution before GST credit"),
    ("itc", "GST credit on fees (ITC)"),
    ("contribution", "Contribution"),
]


class PnlService:
    """Import settlement files and build the monthly P&L."""

    def __init__(self):
        self.engine = get_engine()

    # ------------------------------------------------------------------
    # Import
    # ------------------------------------------------------------------

    def preview_file(self, data: bytes, filename: str) -> dict:
        """Read a file without saving it. Raises DataValidationException if unreadable."""
        platform, rows = parse_settlement_file(data, filename)
        months = sorted(rows["txn_date"].str[:7].unique())
        return {
            "platform": platform,
            "platform_label": PLATFORM_LABELS[platform],
            "rows": rows,
            "date_from": rows["txn_date"].min(),
            "date_to": rows["txn_date"].max(),
            "months": months,
            "settlement_total": round(float(rows["settlement_value"].sum()), 2),
            "units": int(rows["units"].sum()),
            "returned_units": int(rows["returned_units"].sum()),
        }

    def import_file(self, data: bytes, filename: str, account_id: str) -> dict:
        """Store one settlement file for one seller account.

        Refused, saving nothing, when:
          - the exact file was imported before
          - the account does not exist
          - a file for the same marketplace and account already covers any of
            these dates (a re-downloaded copy would double-count the month)
        """
        file_hash = hashlib.sha256(data).hexdigest()
        preview = self.preview_file(data, filename)
        platform, rows = preview["platform"], preview["rows"]
        label = preview["platform_label"]

        with self.engine.begin() as conn:
            dup = conn.execute(
                text("SELECT file_name, account_id FROM settlement_imports WHERE file_hash = :h"),
                {"h": file_hash},
            ).fetchone()
            if dup:
                raise DataValidationException(
                    f"This file was already imported (as '{dup[0]}' for {dup[1]})."
                )
            if not conn.execute(
                text("SELECT 1 FROM seller_accounts WHERE account_id = :a"), {"a": account_id}
            ).first():
                raise DataValidationException(f"Seller account {account_id} does not exist.")
            overlap = conn.execute(text("""
                SELECT file_name, date_from, date_to FROM settlement_imports
                WHERE platform = :p AND account_id = :a
                  AND date_from <= :to AND date_to >= :frm
            """), {"p": platform, "a": account_id,
                   "frm": preview["date_from"], "to": preview["date_to"]}).fetchone()
            if overlap:
                raise DataValidationException(
                    f"{label} for {account_id} is already imported for {overlap[1]} to "
                    f"{overlap[2]} ('{overlap[0]}'), which overlaps this file "
                    f"({preview['date_from']} to {preview['date_to']}). Delete that import "
                    "first if this file should replace it."
                )

            import_id = conn.execute(text("""
                INSERT INTO settlement_imports
                    (file_hash, file_name, platform, account_id, date_from, date_to,
                     row_count, settlement_total)
                VALUES (:h, :n, :p, :a, :f, :t, :c, :s)
            """), {"h": file_hash, "n": filename, "p": platform, "a": account_id,
                   "f": preview["date_from"], "t": preview["date_to"],
                   "c": len(rows), "s": preview["settlement_total"]}).lastrowid

            store = rows.copy()
            store["import_id"] = import_id
            store["account_id"] = account_id
            store["platform"] = platform
            store["month"] = store["txn_date"].str[:7]
            store = store.astype(object).where(store.notna(), None)
            store.to_sql("settlement_lines", conn, if_exists="append", index=False)

        logger.info(
            f"Imported {label} settlement '{filename}' for {account_id}: {len(rows)} rows, "
            f"{preview['date_from']}..{preview['date_to']}, settled {preview['settlement_total']}"
        )
        return {**{k: v for k, v in preview.items() if k != "rows"},
                "import_id": import_id, "row_count": len(rows)}

    def list_imports(self) -> pd.DataFrame:
        with self.engine.connect() as conn:
            return pd.read_sql(text("""
                SELECT import_id, platform, account_id, date_from, date_to, row_count,
                       settlement_total, file_name, imported_at
                FROM settlement_imports ORDER BY date_from DESC, platform, account_id
            """), conn)

    def delete_import(self, import_id: int) -> None:
        """Remove one imported file and all its lines (e.g. imported under the wrong account)."""
        with self.engine.begin() as conn:
            conn.execute(text("DELETE FROM settlement_lines WHERE import_id = :i"), {"i": import_id})
            gone = conn.execute(
                text("DELETE FROM settlement_imports WHERE import_id = :i"), {"i": import_id}
            ).rowcount
        if not gone:
            raise DataValidationException(f"Import {import_id} not found.")
        logger.info(f"Deleted settlement import {import_id}")

    def available_months(self) -> list[str]:
        with self.engine.connect() as conn:
            return [r[0] for r in conn.execute(text(
                "SELECT DISTINCT month FROM settlement_lines ORDER BY month DESC"
            ))]

    # ------------------------------------------------------------------
    # SKU matching
    # ------------------------------------------------------------------

    def _catalog_lookup(self, conn) -> pd.DataFrame:
        """Every way a reported SKU can reach a product, with its costs."""
        return pd.read_sql(text("""
            SELECT pk.pack_sku, pk.master_sku, COALESCE(pk.quantity, 1) AS pack_qty,
                   COALESCE(pk.packaging_cogs, 0) AS packaging_cost,
                   COALESCE(p.total_unit_cogs, 0) AS unit_cost, p.gst_rate AS catalog_gst_rate
            FROM pack_master pk LEFT JOIN product_master p ON p.sku = pk.master_sku
        """), conn)

    def _resolve(self, conn, lines: pd.DataFrame) -> pd.DataFrame:
        """Attach pack, product and costs to each line.

        Order: the listing for that marketplace (channel SKU -> Pack SKU), then
        a Pack SKU equal to the reported SKU. Anything else is unmatched.
        """
        listings = pd.read_sql(text(
            "SELECT UPPER(channel_sku) AS sku_raw, marketplace, internal_sku AS pack_sku "
            "FROM channel_listings"
        ), conn)
        listings["platform"] = listings["marketplace"].str.lower()
        packs = self._catalog_lookup(conn)

        out = lines.merge(listings[["sku_raw", "platform", "pack_sku"]],
                          on=["sku_raw", "platform"], how="left")
        direct = out["pack_sku"].isna() & out["sku_raw"].isin(packs["pack_sku"])
        out.loc[direct, "pack_sku"] = out.loc[direct, "sku_raw"]
        out = out.merge(packs, on="pack_sku", how="left")
        out["matched"] = out["master_sku"].notna()
        out["costed"] = out["matched"] & (out["unit_cost"] > 0)
        return out

    def suggest_pack(self, conn, sku_raw: str, platform: str) -> str | None:
        """Best guess at the Pack SKU for an unmatched reported SKU.

        1. the same SKU string listed on another marketplace
        2. a Pack SKU whose words match once channel prefixes and trailing
           digits are ignored (L-CAP-X, CAPS-X, X1 ...) — only if unambiguous
        """
        hit = conn.execute(text(
            "SELECT internal_sku FROM channel_listings WHERE UPPER(channel_sku) = :s "
            "AND LOWER(marketplace) != :p LIMIT 1"
        ), {"s": sku_raw.upper(), "p": platform}).scalar()
        if hit:
            return hit
        packs = [r[0] for r in conn.execute(text("SELECT pack_sku FROM pack_master"))]
        target = _sku_words(sku_raw)
        if len(target) < 2:
            return None
        matches = [p for p in packs if _sku_words(p) == target]
        return matches[0] if len(matches) == 1 else None

    def map_unmatched_sku(self, sku_raw: str, platform: str, account_id: str, pack_sku: str,
                          fulfilment_type: str = "easy_ship") -> None:
        """Record that a reported SKU is a listing of an existing pack.

        Creates the channel listing (price left blank — set it in Catalog), so
        the next P&L run and the Price check both know it.
        """
        from src.core.services.catalog_service import CatalogService
        CatalogService().add_listing({
            "channel_sku": sku_raw, "marketplace": PLATFORM_LABELS[platform],
            "internal_sku": pack_sku, "listing_status": "ACTIVE", "selling_price": 0.0,
            "account_id": account_id,
            "fulfilment_type": fulfilment_type,
        })

    # ------------------------------------------------------------------
    # Monthly P&L
    # ------------------------------------------------------------------

    def monthly_pnl(self, month: str, account_id: str | None = None,
                    platform: str | None = None) -> dict:
        """P&L for one settlement month.

        Returns:
            dict with
              summary      DataFrame: one row per P&L line, a column per
                           marketplace plus Total
              by_sku       DataFrame: per reported SKU, incl. its share of
                           charges that have no SKU (allocated on units)
              cash         dict: settled, tcs, tds, deferred, output GST
              warnings     list[str]
              unmatched    DataFrame: SKUs that could not be matched
              units        dict: sold, returned
        """
        where, params = ["month = :m"], {"m": month}
        if account_id:
            where.append("account_id = :a")
            params["a"] = account_id
        if platform:
            where.append("platform = :p")
            params["p"] = platform

        with self.engine.connect() as conn:
            lines = pd.read_sql(
                text(f"SELECT * FROM settlement_lines WHERE {' AND '.join(where)}"), conn, params=params
            )
            if lines.empty:
                return {"empty": True}
            lines = self._resolve(conn, lines)
            unmatched = self._unmatched_table(conn, lines)

        money = ["sales_incl_gst", "fees_ex_gst", "shipping_ex_gst", "ads_ex_gst",
                 "other_ex_gst", "itc", "tcs", "tds", "settlement_value"]
        lines[money] = lines[money].astype(float)
        lines["units"] = lines["units"].astype(int)
        lines["returned_units"] = lines["returned_units"].astype(int)

        # Output GST: as reported (Amazon, Meesho) or from the product's rate.
        reported = lines["gst_collected"].notna()
        rate = pd.to_numeric(lines["gst_rate"], errors="coerce")
        rate = rate.fillna(pd.to_numeric(lines["catalog_gst_rate"], errors="coerce"))
        assumed = ~reported & rate.isna() & (lines["sales_incl_gst"] != 0)
        rate = rate.fillna(DEFAULT_GST_RATE)
        derived = lines["sales_incl_gst"] * rate / (100 + rate)
        lines["gst_out"] = lines["gst_collected"].astype(float).where(reported, derived)

        kept = lines["units"] - lines["returned_units"]
        pack_qty = lines["pack_qty"].fillna(1)
        lines["revenue"] = lines["sales_incl_gst"] - lines["gst_out"]
        lines["cogs"] = -(kept * pack_qty * lines["unit_cost"].fillna(0))
        lines["packaging"] = -(lines["units"] * lines["packaging_cost"].fillna(0))
        lines["fees"] = lines["fees_ex_gst"]
        lines["shipping"] = lines["shipping_ex_gst"]
        lines["ads"] = lines["ads_ex_gst"]
        lines["other"] = lines["other_ex_gst"]
        lines["contribution"] = lines[
            ["revenue", "cogs", "packaging", "fees", "shipping", "ads", "other"]
        ].sum(axis=1)
        lines["contribution_before_itc"] = lines["contribution"] - lines["itc"]

        summary = self._summary(lines)
        by_sku = self._by_sku(lines)

        has_sku = lines["sku_raw"].notna()
        units_moved = lines["units"] + lines["returned_units"]
        warnings = []
        um = lines[has_sku & ~lines["matched"]]
        if not um.empty:
            warnings.append(
                f"{int((um['units'] + um['returned_units']).sum())} units across "
                f"{um['sku_raw'].nunique()} SKU(s) are not in the catalog, so their goods "
                f"cost is missing and profit is overstated. Match them below."
            )
        uc = lines[has_sku & lines["matched"] & ~lines["costed"]]
        if not uc.empty:
            warnings.append(
                f"{int(units_moved[uc.index].sum())} units across {uc['master_sku'].nunique()} "
                "product(s) have no landed cost in the catalog, so their goods cost is "
                "counted as ₹0 and profit is overstated. Add costs in Catalog → Products."
            )
        if assumed.any():
            warnings.append(
                f"GST rate unknown for ₹{lines.loc[assumed, 'sales_incl_gst'].sum():,.0f} of "
                f"sales — {DEFAULT_GST_RATE:.0f}% assumed. Set GST % on those products."
            )
        deferred = float(lines.loc[lines["is_deferred"] == 1, "settlement_value"].sum())

        return {
            "empty": False,
            "summary": summary,
            "by_sku": by_sku,
            "unmatched": unmatched,
            "warnings": warnings,
            "units": {"sold": int(lines["units"].sum()),
                      "returned": int(lines["returned_units"].sum())},
            "cash": {
                "settled": round(float(lines["settlement_value"].sum()), 2),
                "tcs": round(float(lines["tcs"].sum()), 2),
                "tds": round(float(lines["tds"].sum()), 2),
                "output_gst": round(float(lines["gst_out"].sum()), 2),
                "deferred": round(deferred, 2),
            },
        }

    @staticmethod
    def _summary(lines: pd.DataFrame) -> pd.DataFrame:
        keys = [k for k, _ in PNL_ROWS]
        by_mkt = lines.groupby("platform")[keys].sum()
        table = by_mkt.T
        table.columns = [PLATFORM_LABELS.get(c, c) for c in table.columns]
        table["Total"] = table.sum(axis=1)
        table.index = [label for _, label in PNL_ROWS]
        return table.round(2)

    @staticmethod
    def _by_sku(lines: pd.DataFrame) -> pd.DataFrame:
        """Per reported SKU. Charges with no SKU (Easy Ship labels, ads, fee
        refunds, storage) are shared out on units within the same account and
        marketplace — joining them to a random order would distort SKUs."""
        cols = ["revenue", "cogs", "packaging", "fees", "shipping", "ads", "other", "itc", "contribution"]
        sku_lines = lines[lines["sku_raw"].notna()]
        pooled = lines[lines["sku_raw"].isna()].groupby(["account_id", "platform"])[cols].sum()

        g = sku_lines.groupby(["account_id", "platform", "sku_raw"]).agg(
            units=("units", "sum"), returned=("returned_units", "sum"),
            master_sku=("master_sku", "first"), matched=("matched", "max"), costed=("costed", "max"),
            **{c: (c, "sum") for c in cols},
        ).reset_index()
        if not pooled.empty and not g.empty:
            weight = g["units"] + g["returned"]
            share = (weight / weight.groupby([g["account_id"], g["platform"]]).transform("sum")
                     .replace(0, 1)).to_numpy()
            keys = pd.MultiIndex.from_frame(g[["account_id", "platform"]])
            pool = pooled.reindex(keys).fillna(0.0)
            for c in cols:
                g[c] = g[c] + pool[c].to_numpy() * share
        # Blank (not 0 or infinity) when a SKU only had refunds this month.
        sold = g["units"].astype(float)
        kept = (g["units"] - g["returned"]).astype(float)
        g["return_pct"] = (g["returned"] / sold.where(sold > 0) * 100).astype(float).round(1)
        g["contribution_per_unit"] = (g["contribution"].astype(float) / kept.where(kept > 0)).round(2)
        g["platform"] = g["platform"].map(PLATFORM_LABELS)
        g["status"] = g.apply(
            lambda r: "OK" if r["costed"] else ("No cost" if r["matched"] else "Not in catalog"), axis=1
        )
        return g.sort_values("contribution").round(2).reset_index(drop=True)

    def _unmatched_table(self, conn, lines: pd.DataFrame) -> pd.DataFrame:
        um = lines[lines["sku_raw"].notna() & ~lines["matched"]]
        if um.empty:
            return pd.DataFrame(columns=["platform", "account_id", "sku_raw", "units", "sales_incl_gst", "suggested_pack"])
        t = um.groupby(["platform", "account_id", "sku_raw"]).agg(
            units=("units", "sum"), sales_incl_gst=("sales_incl_gst", "sum")
        ).reset_index().sort_values("units", ascending=False)
        t["suggested_pack"] = [self.suggest_pack(conn, s, p) for s, p in zip(t["sku_raw"], t["platform"])]
        return t.reset_index(drop=True)


_PREFIX_NOISE = {"L", "M", "S", "A", "O", "F", "PLAIN", "PK1"}
_SYNONYMS = {"NAVY": "NAVYBLUE", "CAPS": "CAP", "PARA": "PARACHUTE", "FULLNET": "NETFULL",
             "GREEN": "OLIVEGREEN", "OLIVE": "OLIVEGREEN"}


def _sku_words(sku: str) -> frozenset:
    """Words that identify a product, ignoring channel prefixes and suffix digits."""
    out = []
    for tok in re.sub(r"[^A-Z0-9]+", "-", str(sku).upper()).split("-"):
        if not tok:
            continue
        tok = re.sub(r"(\D)\d+$", r"\1", tok)
        tok = _SYNONYMS.get(tok, tok)
        if tok in _PREFIX_NOISE or tok in {"CAP", "BASEBALL"}:
            continue
        out.append(tok)
    return frozenset(out)
