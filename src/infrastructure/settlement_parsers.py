"""Readers for marketplace settlement (payment) reports.

Each reader turns one platform's report into the same normalised rows, so the
P&L calculation never needs to know which marketplace a row came from. Every
file quirk is handled here and nowhere else (see MARKETPLACE_PNL_HANDOFF).

Supported reports:
    Amazon    Payments > Reports Repository > Date Range Report > Transaction (CSV)
    Flipkart  Payments > Settled Transactions (XLSX, sheets Orders / Ads / Fines ...)
    Meesho    Payments > Payment Overview > Order Payments (XLSX)

Normalised row (one per settlement line). Money is signed from the seller's
point of view: received is positive, deducted is negative.

    txn_date        date the money moved (Amazon: posted date in IST;
                    Flipkart / Meesho: payment date), 'YYYY-MM-DD'
    order_id        marketplace order / sub-order id (None for non-order rows)
    line_type       order | refund | shipping | ads | fee | income | other
    sku_raw         SKU exactly as the marketplace reports it (None if none)
    units           units shipped to a customer on this line
    returned_units  units that came back (refund / RTO / return)
    sales_incl_gst  what the customer paid incl. GST, net of returns/discounts
    gst_collected   output GST inside sales_incl_gst, or None when the report
                    does not say (Flipkart) — then the catalog rate is used
    gst_rate        product GST % when the report gives it, else None
    fees_ex_gst     marketplace fees excluding GST
    shipping_ex_gst forward + reverse logistics / FBA fulfilment, excl. GST
    ads_ex_gst      advertising, excl. GST
    other_ex_gst    reimbursements, compensation, fines, storage …
    itc             GST charged on fees/shipping/ads — input tax credit,
                    positive = reclaimable
    tcs, tds        tax withheld at source (negative)
    settlement_value cash this line added to / removed from the payout
    is_deferred     1 if the marketplace is still holding the money

Every row satisfies, to the paisa:
    settlement_value = sales_incl_gst + fees_ex_gst + shipping_ex_gst
                       + ads_ex_gst + other_ex_gst - itc + tcs + tds
so a mis-read column cannot silently drop money; the reader raises instead.

Identify files by content, never by filename (Flipkart names are GUIDs).
"""
import io
import re

import pandas as pd

from src.infrastructure.logger import get_logger, DataValidationException

logger = get_logger(__name__)

GST_ON_FEES = 0.18  # GST on every marketplace fee, shipping and ads charge (verified)

NORMALISED_COLUMNS = [
    "txn_date", "order_id", "line_type", "sku_raw", "units", "returned_units",
    "sales_incl_gst", "gst_collected", "gst_rate",
    "fees_ex_gst", "shipping_ex_gst", "ads_ex_gst", "other_ex_gst",
    "itc", "tcs", "tds", "settlement_value", "is_deferred",
]

PLATFORM_LABELS = {"amazon": "Amazon", "flipkart": "Flipkart", "meesho": "Meesho"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _num(series):
    """Numbers from a report column. Amazon writes amounts over 1,000 as
    "-1,016.10", so separators are stripped before converting."""
    if series.dtype == object or pd.api.types.is_string_dtype(series):
        series = series.astype(str).str.replace(",", "", regex=False).str.strip()
    return pd.to_numeric(series, errors="coerce").fillna(0.0)


def _split_gross(gross):
    """Split a GST-inclusive charge into (ex-GST amount, ITC).

    A deduction of -118 is -100 of fee plus 18 of reclaimable GST, so the
    ITC comes back positive. A refund of a fee (+118) reverses the credit.
    """
    ex = gross / (1 + GST_ON_FEES)
    return ex, -(gross - ex)


def _col(columns, prefix, required=True):
    """First column whose cleaned name starts with prefix (case-insensitive).

    startswith, not 'in': Meesho lists 'Return Shipping Charge' before
    'Shipping Charge', and a substring match picks the wrong one.
    """
    p = prefix.lower()
    for c in columns:
        if str(c).strip().lower().startswith(p):
            return c
    if required:
        raise DataValidationException(f"Report is missing the column '{prefix}'.")
    return None


def _finish(df, platform):
    """Order columns, round, and enforce the settlement identity."""
    for c in NORMALISED_COLUMNS:
        if c not in df.columns:
            df[c] = None
    df = df[NORMALISED_COLUMNS].copy()
    money = ["sales_incl_gst", "fees_ex_gst", "shipping_ex_gst", "ads_ex_gst",
             "other_ex_gst", "itc", "tcs", "tds", "settlement_value"]
    df[money] = df[money].astype(float)
    explained = (df["sales_incl_gst"] + df["fees_ex_gst"] + df["shipping_ex_gst"]
                 + df["ads_ex_gst"] + df["other_ex_gst"] - df["itc"] + df["tcs"] + df["tds"])
    gap = (df["settlement_value"] - explained).abs()
    if (gap > 0.05).any():
        bad = df[gap > 0.05].head(3)
        raise DataValidationException(
            f"{PLATFORM_LABELS[platform]} report has amounts this reader does not "
            f"recognise (first rows: {bad['order_id'].tolist()}). Nothing was imported — "
            "the report format may have changed."
        )
    df["units"] = df["units"].fillna(0).astype(int)
    df["returned_units"] = df["returned_units"].fillna(0).astype(int)
    df["is_deferred"] = df["is_deferred"].fillna(0).astype(int)
    df["sku_raw"] = df["sku_raw"].where(df["sku_raw"].notna(), None)
    df.loc[df["sku_raw"].notna(), "sku_raw"] = (
        df.loc[df["sku_raw"].notna(), "sku_raw"].astype(str).str.strip().str.upper()
    )
    df.loc[df["sku_raw"] == "", "sku_raw"] = None
    return df.reset_index(drop=True)


def _read_two_row_header(buf, sheet):
    """Flipkart/Meesho sheets: group banner, real header, formula row, data."""
    df = pd.read_excel(buf, sheet_name=sheet, header=1, skiprows=[2])
    df.columns = [str(c).replace("\n", " ").strip() for c in df.columns]
    return df


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def detect_platform(data: bytes, filename: str = "") -> str | None:
    """Return 'amazon', 'flipkart', 'meesho' or None, from the file's content."""
    head = data[:6000].decode("utf-8-sig", errors="ignore").lower()
    if "date/time" in head and "settlement id" in head:
        return "amazon"
    if data[:2] == b"PK":  # xlsx is a zip
        try:
            sheets = set(pd.ExcelFile(io.BytesIO(data)).sheet_names)
        except Exception:
            return None
        if "Order Payments" in sheets:
            return "meesho"
        if "Orders" in sheets and ("TCS_Recovery" in sheets or "Ads" in sheets):
            return "flipkart"
    return None


# ---------------------------------------------------------------------------
# Amazon — Date Range Report, Transaction (CSV)
# ---------------------------------------------------------------------------

def _amazon_header_row(text_lines):
    for i, line in enumerate(text_lines):
        low = line.lower()
        # The preamble contains a decoy "Date/Time range" line; the real
        # header is the one that also has many columns.
        if low.lstrip('"').startswith("date/time") and low.count(",") > 10:
            return i
    raise DataValidationException("Amazon report: could not find the column header row.")


def parse_amazon(data: bytes) -> pd.DataFrame:
    lines = data.decode("utf-8-sig", errors="ignore").splitlines()
    raw = pd.read_csv(io.StringIO("\n".join(lines)), skiprows=_amazon_header_row(lines),
                      thousands=",", dtype=str)
    raw.columns = [c.strip() for c in raw.columns]

    ttype = raw["type"].fillna("").str.strip()
    # Transfers are bank payouts of money already counted in the order rows.
    raw = raw[ttype.str.lower() != "transfer"].copy()
    ttype = raw["type"].fillna("").str.strip()
    desc = raw["description"].fillna("").str.strip()

    sales = (_num(raw["product sales"]) + _num(raw["shipping credits"])
             + _num(raw["gift wrap credits"]) + _num(raw["promotional rebates"]))
    gst = _num(raw[_col(raw.columns, "Total sales tax liable")])
    tcs = _num(raw["TCS-CGST"]) + _num(raw["TCS-SGST"]) + _num(raw["TCS-IGST"])
    tds = _num(raw[_col(raw.columns, "TDS")])
    selling = _num(raw["selling fees"])
    fba = _num(raw["fba fees"])
    other_txn = _num(raw["other transaction fees"])
    other = _num(raw["other"])
    total = _num(raw["total"])
    qty = _num(raw["quantity"]).astype(int)

    fees_gross = pd.Series(0.0, index=raw.index)
    ship_gross = pd.Series(0.0, index=raw.index)
    ads_gross = pd.Series(0.0, index=raw.index)
    other_no_gst = pd.Series(0.0, index=raw.index)
    line_type = pd.Series("other", index=raw.index)

    is_order = ttype == "Order"
    is_refund = ttype == "Refund"
    is_sale_row = is_order | is_refund
    line_type[is_order] = "order"
    line_type[is_refund] = "refund"
    # On order/refund rows: referral + closing ("selling", "other transaction")
    # are marketplace fees; "fba fees" is fulfilment (incl. the Rs 14 tech fee).
    # On refunds the "selling fees" figure is a return handling charge.
    fees_gross[is_sale_row] = selling[is_sale_row] + other_txn[is_sale_row]
    ship_gross[is_sale_row] = fba[is_sale_row]
    other_no_gst[is_sale_row] = other[is_sale_row]

    rest = ~is_sale_row
    charge = selling + fba + other_txn  # GST-inclusive charges on non-order rows
    is_ads = rest & (ttype == "Service Fee") & desc.str.lower().str.contains("advertising")
    is_ship = rest & ttype.isin(["Shipping Services", "Fulfilment Fee Refund", "Fulfillment Fee Refund"])
    is_fee = rest & ~is_ads & ~is_ship & charge.ne(0)
    ads_gross[is_ads] = charge[is_ads] + other[is_ads]
    line_type[is_ads] = "ads"
    ship_gross[is_ship] = charge[is_ship] + other[is_ship]
    line_type[is_ship] = "shipping"
    fees_gross[is_fee] = charge[is_fee]
    line_type[is_fee] = "fee"
    # Service fees put part of the charge in "other" (e.g. subscription).
    svc = is_fee & (ttype == "Service Fee")
    fees_gross[svc] += other[svc]
    # Reimbursements, SAFE-T claims, adjustments: compensation, no GST.
    plain = rest & ~is_ads & ~is_ship & ~svc
    other_no_gst[plain] = other[plain]
    line_type[plain & other.ne(0) & charge.eq(0)] = "income"

    fees_ex, fees_itc = _split_gross(fees_gross)
    ship_ex, ship_itc = _split_gross(ship_gross)
    ads_ex, ads_itc = _split_gross(ads_gross)

    when = pd.to_datetime(
        raw["date/time"].str.replace("Sept", "Sep", regex=False).str.replace(" UTC", "", regex=False),
        format="%d %b %Y %I:%M:%S %p", errors="coerce",
    )
    if when.isna().any():
        raise DataValidationException("Amazon report: some dates could not be read.")
    when = when.dt.tz_localize("UTC").dt.tz_convert("Asia/Kolkata")
    status = raw.get("Transaction Status", pd.Series("", index=raw.index)).fillna("")

    out = pd.DataFrame({
        "txn_date": when.dt.strftime("%Y-%m-%d"),
        "order_id": raw["order id"].where(raw["order id"].notna(), None),
        "line_type": line_type,
        "sku_raw": raw["Sku"],
        "units": qty.where(is_order, 0),
        "returned_units": qty.where(is_refund, 0),
        "sales_incl_gst": sales + gst,
        "gst_collected": gst,
        "gst_rate": None,
        "fees_ex_gst": fees_ex,
        "shipping_ex_gst": ship_ex,
        "ads_ex_gst": ads_ex,
        "other_ex_gst": other_no_gst,
        "itc": fees_itc + ship_itc + ads_itc,
        "tcs": tcs,
        "tds": tds,
        "settlement_value": total,
        "is_deferred": (status.str.lower() == "deferred").astype(int),
    })
    return _finish(out, "amazon")


# ---------------------------------------------------------------------------
# Flipkart — Settled Transactions (XLSX)
# ---------------------------------------------------------------------------

def parse_flipkart(data: bytes) -> pd.DataFrame:
    buf = io.BytesIO(data)
    sheets = pd.ExcelFile(buf).sheet_names
    frames = []

    o = _read_two_row_header(buf, "Orders")
    o = o[o[_col(o.columns, "Order ID")].notna()].copy()
    if not o.empty:
        c = lambda p: _col(o.columns, p)
        sale = _num(o[c("Sale Amount (Rs.)")])
        refund = _num(o[c("Refund (Rs.)")])
        bank = _num(o[c("Bank Settlement Value")])
        # Lines that neither settled nor refunded in this cycle carry no money;
        # counting them as sold would charge COGS against revenue that never came.
        o = o[~((sale == 0) & (refund == 0) & (bank == 0))].copy()
        c = lambda p: _col(o.columns, p)
        sale, refund, bank = _num(o[c("Sale Amount (Rs.)")]), _num(o[c("Refund (Rs.)")]), _num(o[c("Bank Settlement Value")])
        qty = _num(o[c("Quantity")]).astype(int)

        revenue = (sale + refund + _num(o[c("Total Offer Amount (Rs.)")]) + _num(o[c("My share (Rs.)")])
                   + _num(o[c("Customer Add-ons Amount (Rs.)")]) + _num(o[c("Offer Adjustments (Rs.)")]))
        mp_fee = _num(o[c("Marketplace Fee")])  # already excludes GST
        shipping = _num(o[c("Shipping Fee (Rs.)")]) + _num(o[c("Reverse Shipping Fee (Rs.)")])
        rate = _num(o[c("Item GST Rate")])  # reported as 0 by Flipkart; kept if it ever isn't
        frames.append(pd.DataFrame({
            "txn_date": pd.to_datetime(o[c("Payment Date")]).dt.strftime("%Y-%m-%d"),
            "order_id": o[c("Order ID")].astype(str),
            "line_type": "order",
            "sku_raw": o[c("Seller SKU")],
            "units": qty.where(sale != 0, 0),
            "returned_units": qty.where(refund != 0, 0),
            "sales_incl_gst": revenue,
            "gst_collected": None,
            "gst_rate": rate.where(rate > 0, None),
            "fees_ex_gst": mp_fee - shipping,
            "shipping_ex_gst": shipping,
            "ads_ex_gst": 0.0,
            "other_ex_gst": _num(o[c("Protection Fund (Rs.)")]),
            # Flipkart states the GST on its fees per order — use it, don't derive.
            "itc": -_num(o[c("GST on MP Fees")]),
            "tcs": _num(o[c("TCS (Rs.)")]),
            "tds": _num(o[c("TDS (Rs.)")]),
            "settlement_value": bank,
            "is_deferred": 0,
        }))

    if "Ads" in sheets:
        a = _read_two_row_header(buf, "Ads")
        a = a[a[_col(a.columns, "Payment Date")].notna()].copy()
        if not a.empty:
            # Cash paid for ads in the cycle is the wallet top-up (GST-inclusive).
            topup = _num(a[_col(a.columns, "Wallet Topup")])
            ex, itc = _split_gross(topup)
            settled = _num(a[_col(a.columns, "Settlement Value")])
            frames.append(pd.DataFrame({
                "txn_date": pd.to_datetime(a[_col(a.columns, "Payment Date")]).dt.strftime("%Y-%m-%d"),
                "order_id": a[_col(a.columns, "Campaign / Transaction ID")].astype(str),
                "line_type": "ads", "sku_raw": None, "units": 0, "returned_units": 0,
                "sales_incl_gst": 0.0, "fees_ex_gst": 0.0, "shipping_ex_gst": 0.0,
                "ads_ex_gst": ex, "itc": itc,
                # Wallet movements other than the top-up net to zero in the payout.
                "other_ex_gst": settled - topup,
                "tcs": 0.0, "tds": 0.0, "settlement_value": settled, "is_deferred": 0,
            }))

    # Remaining service / penalty sheets share one layout: an amount and its GST.
    for sheet in ("Fines", "Value Added Services", "Support Services Services",
                  "Review Accelerator Services", "Insight Subscription Services",
                  "Google Ads Services", "Storage_Recall", "Non_Order_SPF", "MP Fee Rebate"):
        if sheet not in sheets:
            continue
        s = _read_two_row_header(buf, sheet)
        date_col = _col(s.columns, "Payment Date", required=False)
        if date_col is None:
            continue
        s = s[s[date_col].notna()].copy()
        if s.empty:
            continue
        settled = _num(s[_col(s.columns, "Settlement Value")])
        gst_col = next((c for c in s.columns if c.lower().startswith("gst on")), None)
        itc = -_num(s[gst_col]) if gst_col else pd.Series(0.0, index=s.index)
        net = settled + itc  # amount excluding GST
        is_ads = sheet == "Google Ads Services"
        is_income = sheet in ("Non_Order_SPF", "MP Fee Rebate")
        frames.append(pd.DataFrame({
            "txn_date": pd.to_datetime(s[date_col]).dt.strftime("%Y-%m-%d"),
            "order_id": None,
            "line_type": "ads" if is_ads else ("income" if is_income else "fee"),
            "sku_raw": None, "units": 0, "returned_units": 0, "sales_incl_gst": 0.0,
            "fees_ex_gst": 0.0 if (is_ads or is_income) else net,
            "shipping_ex_gst": 0.0,
            "ads_ex_gst": net if is_ads else 0.0,
            "other_ex_gst": net if is_income else 0.0,
            "itc": itc, "tcs": 0.0, "tds": 0.0, "settlement_value": settled, "is_deferred": 0,
        }))

    if not frames:
        raise DataValidationException("Flipkart report has no settled rows.")
    return _finish(pd.concat(frames, ignore_index=True), "flipkart")


# ---------------------------------------------------------------------------
# Meesho — Order Payments (XLSX)
# ---------------------------------------------------------------------------

_MEESHO_FEES_INCL_GST = [
    "Fixed Fee (Incl. GST)", "Warehousing fee (inc Gst)", "Return premium (incl GST)",
    "Return premium (incl GST) of Return", "Meesho Commission (Incl. GST)",
    "Meesho gold platform fee (Incl. GST)", "Meesho mall platform fee (Incl. GST)",
    "Fixed Fee (Incl. GST).1", "Warehousing fee (Incl. GST)",
]
_MEESHO_SHIPPING_INCL_GST = ["Shipping Charge (Incl. GST)", "Return Shipping Charge (Incl. GST)"]
_MEESHO_OTHER = ["GST Compensation (PRP Shipping)", "Compensation", "Claims", "Recovery"]


def parse_meesho(data: bytes) -> pd.DataFrame:
    buf = io.BytesIO(data)
    o = _read_two_row_header(buf, "Order Payments")
    o = o[o["Sub Order No"].notna()].copy()
    if o.empty:
        raise DataValidationException("Meesho report has no order payments.")

    def money(name):
        # Exact names: several Meesho columns share a prefix.
        return _num(o[name]) if name in o.columns else pd.Series(0.0, index=o.index)

    status = o["Live Order Status"].fillna("").str.strip().str.upper()
    qty = _num(o["Quantity"]).astype(int)
    fees_ex, fees_itc = _split_gross(sum(money(c) for c in _MEESHO_FEES_INCL_GST))
    ship_ex, ship_itc = _split_gross(sum(money(c) for c in _MEESHO_SHIPPING_INCL_GST))
    support_ex = money("Net Other Support Service Charges (Excl. GST)")
    support_itc = -money("GST on Net Other Support Service Charges")
    rate = _num(o["Product GST %"]) if "Product GST %" in o.columns else pd.Series(0.0, index=o.index)
    sales = money("Total Sale Amount (Incl. Shipping & GST)") + money("Total Sale Return Amount (Incl. Shipping & GST)")

    out = pd.DataFrame({
        "txn_date": pd.to_datetime(o["Payment Date"]).dt.strftime("%Y-%m-%d"),
        "order_id": o["Sub Order No"].astype(str),
        "line_type": "order",
        "sku_raw": o["Supplier SKU"],
        "units": qty,
        # No refund column on Meesho: the order status says it came back.
        "returned_units": qty.where(status.isin(["RTO", "RETURN"]), 0),
        "sales_incl_gst": sales,
        "gst_collected": sales * rate / (100 + rate),
        "gst_rate": rate.where(rate > 0, None),
        "fees_ex_gst": fees_ex + support_ex,
        "shipping_ex_gst": ship_ex,
        "ads_ex_gst": 0.0,
        "other_ex_gst": sum(money(c) for c in _MEESHO_OTHER),
        "itc": fees_itc + ship_itc + support_itc,
        "tcs": money("TCS"),
        "tds": money("TDS"),
        "settlement_value": money("Final Settlement Amount"),
        "is_deferred": 0,
    })
    frames = [out]

    # Ads Cost sheet: empty in every file seen so far ("No data is available").
    # Refuse rather than guess if it ever has rows we do not understand.
    ads = _read_two_row_header(buf, "Ads Cost") if "Ads Cost" in pd.ExcelFile(buf).sheet_names else None
    if ads is not None:
        first = ads.columns[0]
        ads = ads[ads[first].notna() & ~ads[first].astype(str).str.contains("No data", case=False)]
        if not ads.empty:
            raise DataValidationException(
                "Meesho report has Ads Cost rows, which this version cannot read yet. "
                "Nothing was imported — please send this file so it can be supported."
            )
    return _finish(pd.concat(frames, ignore_index=True), "meesho")


PARSERS = {"amazon": parse_amazon, "flipkart": parse_flipkart, "meesho": parse_meesho}


def parse_settlement_file(data: bytes, filename: str = "") -> tuple[str, pd.DataFrame]:
    """Detect the platform and parse. Returns (platform, normalised rows)."""
    platform = detect_platform(data, filename)
    if platform is None:
        raise DataValidationException(
            "Not a recognised settlement report. Upload the Amazon Date Range "
            "Transaction report (CSV), Flipkart Settled Transactions (XLSX) or "
            "Meesho Order Payments (XLSX)."
        )
    rows = PARSERS[platform](data)
    logger.info(f"Parsed {PLATFORM_LABELS[platform]} settlement file '{filename}': {len(rows)} rows")
    return platform, rows
