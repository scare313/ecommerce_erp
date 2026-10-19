"""Column names and editors for the Catalog grids.

One vocabulary across Products, Packs and Listings, so the same thing is
always called the same name on screen:

    Master SKU   a product            product_master.sku, pack_master.master_sku
    Pack SKU     what you ship         pack_master.pack_sku, channel_listings.internal_sku
    Listing SKU  what a marketplace    channel_listings.channel_sku
                 shows

Database column names are unchanged (they are used throughout the code);
only the labels people see are set here. Key columns come first, and the
columns that point to another table are dropdowns of values that exist.

Presentation only: no data access, no validation (CatalogService does that).
"""
import streamlit as st

LISTING_STATUSES = ["ACTIVE", "INACTIVE", "SUPPRESSED"]

# Label shown to the user -> value stored in channel_listings.fulfilment_type
FULFILMENT_OPTIONS = {"Self-ship / Easy Ship": "easy_ship", "FBA (Amazon ships)": "fba"}

_C = st.column_config

PRODUCT_ORDER = [
    "sku", "name", "category", "brand", "lifecycle_status",
    "total_unit_cogs", "mfg_cost", "packaging_cost", "labeling_labor", "inbound_transport",
    "gst_rate", "hsn", "mrp",
    "supplier_code", "supplier_product_code", "supplier",
]

PACK_ORDER = [
    "pack_sku", "master_sku", "quantity", "packaging_cogs",
    "final_l_cm", "final_w_cm", "final_h_cm", "final_wt_kg",
]

LISTING_ORDER = [
    "channel_sku", "marketplace", "internal_sku", "account_id", "fulfilment_type",
    "selling_price", "listing_status", "channel_id", "listing_url", "comment", "last_updated",
]


def _money(label, help_text=None):
    return _C.NumberColumn(label, min_value=0.0, format="₹%.2f", help=help_text)


def product_columns(categories):
    return {
        "sku": _C.TextColumn("Master SKU", required=True,
                             help="Your own code for the product. Packs point to it."),
        "name": _C.TextColumn("Product name"),
        "category": _C.SelectboxColumn("Category", options=categories,
                                       help="Decides which marketplace fee rules apply."),
        "brand": _C.TextColumn("Brand"),
        "lifecycle_status": _C.SelectboxColumn("Status", options=["Active", "Discontinued"]),
        "total_unit_cogs": _money("Landed cost / unit",
                                  "Cost of ONE unit delivered to you. Used for profit."),
        "mfg_cost": _money("Making cost"),
        "packaging_cost": _money("Unit box cost"),
        "labeling_labor": _money("Labour"),
        "inbound_transport": _money("Inbound freight"),
        "gst_rate": _C.NumberColumn("GST %", min_value=0.0, max_value=28.0),
        "hsn": _C.TextColumn("HSN"),
        "mrp": _money("MRP"),
        "supplier_code": _C.TextColumn("Supplier code"),
        "supplier_product_code": _C.TextColumn("Supplier's product code"),
        "supplier": _C.TextColumn("Supplier (old note)", help="Historical text; use Supplier code."),
    }


def pack_columns(master_skus):
    return {
        "pack_sku": _C.TextColumn("Pack SKU", required=True,
                                  help="What you ship. Listings point to it."),
        "master_sku": _C.SelectboxColumn("Master SKU", options=master_skus, required=True,
                                         help="The product inside this pack."),
        "quantity": _C.NumberColumn("Units in pack", min_value=1, step=1, required=True,
                                    help="How many units of the Master SKU are in one pack."),
        "packaging_cogs": _money("Packaging cost / pack",
                                 "Polybag, box, label — paid once per pack shipped."),
        "final_l_cm": _C.NumberColumn("Length (cm)", min_value=0.0),
        "final_w_cm": _C.NumberColumn("Width (cm)", min_value=0.0),
        "final_h_cm": _C.NumberColumn("Height (cm)", min_value=0.0),
        "final_wt_kg": _C.NumberColumn("Weight (kg)", min_value=0.0, format="%.3f"),
    }


def listing_columns(pack_skus, marketplaces, account_ids):
    return {
        "channel_sku": _C.TextColumn("Listing SKU", required=True,
                                     help="The SKU exactly as it appears on the marketplace."),
        "marketplace": _C.SelectboxColumn("Marketplace", options=marketplaces, required=True),
        "internal_sku": _C.SelectboxColumn("Pack SKU", options=pack_skus, required=True,
                                           help="The pack this listing sells."),
        "account_id": _C.SelectboxColumn("Seller account", options=account_ids, required=True),
        "fulfilment_type": _C.SelectboxColumn("Fulfilment", options=list(FULFILMENT_OPTIONS.values()),
                                              required=True),
        "selling_price": _money("Price (incl. GST)", "Needed for profit calculations."),
        "listing_status": _C.SelectboxColumn("Status", options=LISTING_STATUSES),
        "channel_id": _C.TextColumn("ASIN / listing ID"),
        "listing_url": _C.LinkColumn("Link"),
        "comment": _C.TextColumn("Note"),
        "last_updated": _C.TextColumn("Last updated", disabled=True),
    }


# What a person needs to see and edit day to day. Everything else stays in
# the database (and in imports/exports) and appears with "Show all columns".
# These are exactly the fields the profit, stock and purchase calculations use.
PRODUCT_ESSENTIAL = ["sku", "category", "total_unit_cogs", "gst_rate", "supplier_code"]
PACK_ESSENTIAL = ["pack_sku", "master_sku", "quantity", "packaging_cogs", "final_wt_kg"]
LISTING_ESSENTIAL = ["channel_sku", "marketplace", "internal_sku", "account_id",
                     "fulfilment_type", "selling_price", "listing_status"]


def ordered(df, order):
    """Column order for a grid: known columns in `order`, then any others."""
    return [c for c in order if c in df.columns] + [c for c in df.columns if c not in order]


def visible(df, order, essential, show_all):
    """Columns to display. Hidden columns are still returned by st.data_editor
    and saved unchanged — hiding only affects what is drawn."""
    if show_all:
        return ordered(df, order)
    return [c for c in essential if c in df.columns]
