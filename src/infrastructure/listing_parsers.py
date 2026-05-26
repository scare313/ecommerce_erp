"""Marketplace catalog/listing file parsers for onboarding wizard.

Parses inventory/listing exports from Amazon, Flipkart, and Meesho into a
unified staging schema for catalog bootstrapping.

Unified output schema (all parsers return DataFrames with these columns):
    internal_sku    : str   - Seller SKU (cleaned, uppercase)
    marketplace     : str   - 'Amazon' | 'Flipkart' | 'Meesho'
    channel_sku     : str   - Marketplace identifier (Listing ID / ASIN / Product ID)
    name            : str   - Product title
    brand           : str   - Brand name (or None)
    category        : str   - Category proxy (or None)
    mrp             : float - MRP / list price (or None)
    selling_price   : float - Current selling price (0.0 if not available)
    hsn             : str   - HSN code (or None)
    gst_rate        : float - GST % (or None)
    listing_status  : str   - 'ACTIVE' / 'INACTIVE' / etc.
    length_cm       : float - Package length in cm (or None)
    width_cm        : float - Package width in cm (or None)
    height_cm       : float - Package height in cm (or None)
    weight_kg       : float - Package weight in kg (or None)

Contract (matches parsers.py [15]):
    Each parser returns a tuple: (DataFrame | None, error_message | None)
    - On success: (df, None)
    - On failure: (None, str)
    - Never raises on bad input
"""
import pandas as pd
import numpy as np
from src.infrastructure.logger import get_logger

logger = get_logger(__name__)


# =============================================================================
# CONSTANTS
# =============================================================================

# Unified column schema returned by all parsers
UNIFIED_COLUMNS = [
    "internal_sku", "marketplace", "channel_sku", "name", "brand", "category",
    "mrp", "selling_price", "hsn", "gst_rate", "listing_status",
    "length_cm", "width_cm", "height_cm", "weight_kg",
]

# Flipkart Tax Code -> GST percentage
# Per Flipkart listing file convention (e.g., "GST_5" means 5%)
FLIPKART_TAX_MAP = {
    "GST_0": 0.0,
    "GST_3": 3.0,
    "GST_5": 5.0,
    "GST_12": 12.0,
    "GST_18": 18.0,
    "GST_28": 28.0,
}

# Amazon Product Tax Code -> GST percentage
# Using post-Sep-22-2025 rates per user-provided mapping table.
# NOTE: A_GEN_PEAK_CESS60 has 60% Cess unmodeled (schema has no cess column);
# we store only the GST portion (28%) and emit a warning during ingestion.
AMAZON_TAX_MAP = {
    "A_GEN_EXEMPT": 0.0,
    "A_GEN_MINIMUM": 0.25,
    "A_GEN_SUPERREDUCED": 5.0,
    "A_GEN_REDUCED": 5.0,
    "A_GEN_STANDARD": 18.0,
    "A_GEN_PEAK": 18.0,
    "A_GEN_PEAK_CESS12": 40.0,    # 28% + 12% Cess merged to 40% post-Sep-2025
    "A_GEN_PEAK_CESS60": 28.0,    # Cess (60%) unmodeled — see note above
    "A_GEN_JEWELLERY": 3.0,
}

# Unit conversion factors
LENGTH_TO_CM = {
    "centimeters": 1.0, "centimeter": 1.0, "cm": 1.0, "cms": 1.0,
    "millimeters": 0.1, "millimeter": 0.1, "mm": 0.1,
    "meters": 100.0, "meter": 100.0, "m": 100.0,
    "inches": 2.54, "inch": 2.54, "in": 2.54,
    "feet": 30.48, "foot": 30.48, "ft": 30.48,
}

WEIGHT_TO_KG = {
    "kilograms": 1.0, "kilogram": 1.0, "kg": 1.0, "kgs": 1.0,
    "grams": 0.001, "gram": 0.001, "g": 0.001, "gm": 0.001, "gms": 0.001,
    "milligrams": 0.000001, "milligram": 0.000001, "mg": 0.000001,
    "pounds": 0.4536, "pound": 0.4536, "lb": 0.4536, "lbs": 0.4536,
    "ounces": 0.02835, "ounce": 0.02835, "oz": 0.02835,
}


# =============================================================================
# HELPERS
# =============================================================================

def clean_sku(sku):
    """
    Standardizes SKU format: Uppercase, stripped of whitespace.

    Mirrors parsers.clean_sku() [15] for consistency across the codebase.

    Args:
        sku: SKU value (can be string, numeric, or NaN)

    Returns:
        str: Standardized SKU, or None if blank/NaN
    """
    try:
        if pd.isna(sku):
            return None
        s = str(sku).strip().upper()
        return s if s else None
    except Exception as e:
        logger.error(f"Error cleaning SKU {sku}: {str(e)}", exc_info=True)
        return None


def _safe_float(value):
    """Convert to float, returning None on failure (preserves NULL semantics)."""
    try:
        if pd.isna(value):
            return None
        f = float(value)
        return f if not (np.isnan(f) or np.isinf(f)) else None
    except (ValueError, TypeError):
        return None


def _safe_str(value):
    """Convert to stripped string, returning None on blank/NaN."""
    try:
        if pd.isna(value):
            return None
        s = str(value).strip()
        return s if s else None
    except Exception:
        return None


def _convert_length(value, unit):
    """Convert a length value to cm. Returns None if unparseable."""
    val = _safe_float(value)
    if val is None:
        return None
    unit_str = (_safe_str(unit) or "cm").lower()
    factor = LENGTH_TO_CM.get(unit_str)
    if factor is None:
        logger.warning(f"Unknown length unit '{unit_str}', assuming cm")
        factor = 1.0
    return round(val * factor, 2)


def _convert_weight(value, unit):
    """Convert a weight value to kg. Returns None if unparseable."""
    val = _safe_float(value)
    if val is None:
        return None
    unit_str = (_safe_str(unit) or "kg").lower()
    factor = WEIGHT_TO_KG.get(unit_str)
    if factor is None:
        logger.warning(f"Unknown weight unit '{unit_str}', assuming kg")
        factor = 1.0
    return round(val * factor, 3)


def _parse_flipkart_tax(tax_code):
    """Parse Flipkart 'GST_5' -> 5.0. Returns None on failure."""
    s = _safe_str(tax_code)
    if not s:
        return None
    s_upper = s.upper().replace(" ", "")
    if s_upper in FLIPKART_TAX_MAP:
        return FLIPKART_TAX_MAP[s_upper]
    # Try generic pattern: GST_<n>
    if s_upper.startswith("GST_"):
        try:
            return float(s_upper.split("_", 1)[1])
        except (ValueError, IndexError):
            pass
    logger.warning(f"Unknown Flipkart tax code: '{s}'")
    return None


def _parse_amazon_tax(tax_code):
    """Parse Amazon 'A_GEN_STANDARD' -> 18.0. Returns None on failure."""
    s = _safe_str(tax_code)
    if not s:
        return None
    s_upper = s.upper().replace(" ", "")
    if s_upper in AMAZON_TAX_MAP:
        if s_upper == "A_GEN_PEAK_CESS60":
            logger.warning(
                "A_GEN_PEAK_CESS60 has 60%% Cess unmodeled in schema; "
                "storing 28%% GST only. Manual adjustment may be needed."
            )
        return AMAZON_TAX_MAP[s_upper]
    logger.warning(f"Unknown Amazon tax code: '{s}'")
    return None


def _empty_unified_df():
    """Return an empty DataFrame with the unified schema."""
    return pd.DataFrame(columns=UNIFIED_COLUMNS)


def _find_col(columns, *candidates):
    """
    Find first matching column name (case-insensitive substring match).
    Returns the actual column name from the DataFrame, or None.
    """
    cols_lower = {c.lower().strip(): c for c in columns}
    # First pass: exact match
    for cand in candidates:
        if cand.lower() in cols_lower:
            return cols_lower[cand.lower()]
    # Second pass: substring match
    for cand in candidates:
        cand_lower = cand.lower()
        for col_lower, original in cols_lower.items():
            if cand_lower in col_lower:
                return original
    return None


# =============================================================================
# FLIPKART PARSER
# =============================================================================

def parse_flipkart_listings(file_buffer):
    """
    Parses Flipkart Listings export (.xls).

    Expected columns (from S_listing--ui--group_*.xls) [1]:
        Product Title, Seller SKU Id, Listing ID, Listing Status,
        MRP, Your Selling Price, Package Length/Breadth/Height/Weight,
        HSN, Tax Code, Sub-category

    Note: Flipkart files have descriptive header rows; data starts after.
    We use header row detection via 'Seller SKU Id' anchor.

    Args:
        file_buffer: File-like object containing Flipkart .xls

    Returns:
        tuple: (DataFrame with unified schema, error_message)
    """
    try:
        logger.info("Parsing Flipkart listings file...")

        # Flipkart .xls files have header in row 0 with descriptions in row 1
        # Read with no header to detect structure
        try:
            raw = pd.read_excel(file_buffer, header=0)
        except Exception as e:
            logger.error(f"Failed to read Flipkart Excel: {str(e)}")
            return None, f"Could not read Flipkart file: {str(e)}"

        if raw.empty:
            return None, "Flipkart file is empty"

        # Drop the descriptive row(s) — Flipkart puts a description row right
        # after the header. Detect by checking if 'Seller SKU Id' column has
        # the literal text "Your Identifier for a product"
        sku_col_candidates = ["Seller SKU Id", "Seller SKU", "SKU"]
        sku_col = _find_col(raw.columns, *sku_col_candidates)
        if not sku_col:
            return None, "Flipkart file missing 'Seller SKU Id' column"

        # Filter rows: drop description rows and rows with no SKU
        df = raw[raw[sku_col].notna()].copy()
        # Drop rows where SKU value looks like a description (contains spaces
        # and is unusually long, or matches known description text)
        desc_markers = ["Your Identifier", "Identifier for"]
        for marker in desc_markers:
            df = df[~df[sku_col].astype(str).str.contains(marker, case=False, na=False)]

        if df.empty:
            return None, "Flipkart file has no data rows after filtering descriptions"

        # Locate all needed columns
        title_col = _find_col(df.columns, "Product Title", "Title")
        listing_id_col = _find_col(df.columns, "Listing ID")
        status_col = _find_col(df.columns, "Listing Status")
        mrp_col = _find_col(df.columns, "MRP")
        price_col = _find_col(df.columns, "Your Selling Price", "Selling Price")
        hsn_col = _find_col(df.columns, "Harmonized System Nomenclature", "HSN")
        tax_col = _find_col(df.columns, "Tax Code")
        sub_cat_col = _find_col(df.columns, "Sub-category", "Sub category")
        pkg_l_col = _find_col(df.columns, "Package Length")
        pkg_b_col = _find_col(df.columns, "Package Breadth")
        pkg_h_col = _find_col(df.columns, "Package Height")
        pkg_w_col = _find_col(df.columns, "Package Weight")

        # Build unified DataFrame
        rows = []
        skipped = 0
        for _, src_row in df.iterrows():
            internal_sku = clean_sku(src_row.get(sku_col))
            if not internal_sku:
                skipped += 1
                continue

            channel_sku = _safe_str(src_row.get(listing_id_col)) if listing_id_col else None
            if not channel_sku:
                # Listing ID is critical for channel_listings PK; skip if missing
                logger.warning(f"Flipkart row missing Listing ID for SKU {internal_sku}; skipping")
                skipped += 1
                continue

            rows.append({
                "internal_sku": internal_sku,
                "marketplace": "Flipkart",
                "channel_sku": channel_sku,
                "name": _safe_str(src_row.get(title_col)) if title_col else None,
                "brand": None,  # Flipkart file doesn't expose brand cleanly
                "category": _safe_str(src_row.get(sub_cat_col)) if sub_cat_col else None,
                "mrp": _safe_float(src_row.get(mrp_col)) if mrp_col else None,
                "selling_price": _safe_float(src_row.get(price_col)) if price_col else None,
                "hsn": _safe_str(src_row.get(hsn_col)) if hsn_col else None,
                "gst_rate": _parse_flipkart_tax(src_row.get(tax_col)) if tax_col else None,
                "listing_status": (_safe_str(src_row.get(status_col)) or "ACTIVE").upper() if status_col else "ACTIVE",
                # Flipkart already provides dimensions in cm and weight in kg
                "length_cm": _safe_float(src_row.get(pkg_l_col)) if pkg_l_col else None,
                "width_cm": _safe_float(src_row.get(pkg_b_col)) if pkg_b_col else None,
                "height_cm": _safe_float(src_row.get(pkg_h_col)) if pkg_h_col else None,
                "weight_kg": _safe_float(src_row.get(pkg_w_col)) if pkg_w_col else None,
            })

        if not rows:
            return None, "Flipkart file produced no valid rows"

        result = pd.DataFrame(rows, columns=UNIFIED_COLUMNS)
        # Default selling_price to 0.0 if NULL (matches schema default [16])
        result["selling_price"] = result["selling_price"].fillna(0.0)

        logger.info(
            f"✅ Parsed Flipkart listings: {len(result)} rows "
            f"({skipped} skipped due to missing SKU/Listing ID)"
        )
        return result, None

    except Exception as e:
        error_msg = f"Error reading Flipkart file: {str(e)}"
        logger.error(error_msg, exc_info=True)
        return None, error_msg


# =============================================================================
# MEESHO PARSER
# =============================================================================

def parse_meesho_listings(file_buffer):
    """
    Parses Meesho Inventory Update file (.xlsx).

    Expected columns (from Inventory-Update-File_*.xlsx) [2]:
        SERIAL NO, CATALOG NAME, CATALOG ID, PRODUCT NAME, PRODUCT ID,
        STYLE ID, VARIATION ID, VARIATION, STOCK, SYSTEM STOCK COUNT,
        YOUR STOCK COUNT

    Note: Meesho file has descriptive row right after header (e.g., "Row identifier",
    "Catalog name", etc.). We detect and drop it.

    Limitations:
        - No price information (selling_price defaults to 0.0)
        - No HSN, GST, or dimensions (all NULL)
        - No brand info beyond catalog name

    Args:
        file_buffer: File-like object containing Meesho .xlsx

    Returns:
        tuple: (DataFrame with unified schema, error_message)
    """
    try:
        logger.info("Parsing Meesho listings file...")

        try:
            raw = pd.read_excel(file_buffer, header=0)
        except Exception as e:
            logger.error(f"Failed to read Meesho Excel: {str(e)}")
            return None, f"Could not read Meesho file: {str(e)}"

        if raw.empty:
            return None, "Meesho file is empty"

        # Find STYLE ID column (the seller SKU) [2]
        style_col = _find_col(raw.columns, "STYLE ID", "Style ID")
        if not style_col:
            return None, "Meesho file missing 'STYLE ID' column"

        # Drop description row(s) — Meesho's row right after header contains
        # "Product ID/Style ID" as the STYLE ID value
        df = raw[raw[style_col].notna()].copy()
        desc_markers = ["Product ID/Style ID", "Style ID"]
        for marker in desc_markers:
            df = df[~df[style_col].astype(str).str.fullmatch(marker, case=False, na=False)]

        # Also drop rows where SERIAL NO is non-numeric (often header descriptions)
        serial_col = _find_col(df.columns, "SERIAL NO", "Serial No")
        if serial_col:
            df = df[pd.to_numeric(df[serial_col], errors="coerce").notna()]

        if df.empty:
            return None, "Meesho file has no data rows after filtering descriptions"

        # Locate columns
        product_id_col = _find_col(df.columns, "PRODUCT ID", "Product ID")
        product_name_col = _find_col(df.columns, "PRODUCT NAME", "Product Name")
        catalog_name_col = _find_col(df.columns, "CATALOG NAME", "Catalog Name")

        if not product_id_col:
            return None, "Meesho file missing 'PRODUCT ID' column"

        rows = []
        skipped = 0
        for _, src_row in df.iterrows():
            internal_sku = clean_sku(src_row.get(style_col))
            if not internal_sku:
                skipped += 1
                continue

            channel_sku = _safe_str(src_row.get(product_id_col))
            if not channel_sku:
                logger.warning(f"Meesho row missing Product ID for SKU {internal_sku}; skipping")
                skipped += 1
                continue

            rows.append({
                "internal_sku": internal_sku,
                "marketplace": "Meesho",
                "channel_sku": channel_sku,
                "name": _safe_str(src_row.get(product_name_col)) if product_name_col else None,
                "brand": _safe_str(src_row.get(catalog_name_col)) if catalog_name_col else None,
                "category": None,  # Meesho file has no clean category field
                "mrp": None,
                "selling_price": None,  # Meesho inventory file has no price
                "hsn": None,
                "gst_rate": None,
                "listing_status": "ACTIVE",  # Inventory file implies active listing
                "length_cm": None,
                "width_cm": None,
                "height_cm": None,
                "weight_kg": None,
            })

        if not rows:
            return None, "Meesho file produced no valid rows"

        result = pd.DataFrame(rows, columns=UNIFIED_COLUMNS)
        result["selling_price"] = result["selling_price"].fillna(0.0)

        logger.info(
            f"✅ Parsed Meesho listings: {len(result)} rows "
            f"({skipped} skipped due to missing SKU/Product ID). "
            f"Note: price/HSN/dimensions not available from this file."
        )
        return result, None

    except Exception as e:
        error_msg = f"Error reading Meesho file: {str(e)}"
        logger.error(error_msg, exc_info=True)
        return None, error_msg


# =============================================================================
# AMAZON PARSER
# =============================================================================

def parse_amazon_listings(file_buffer):
    """
    Parses Amazon Category Listings Report (.xlsx).

    Reads the 'Template' sheet with header at row 4 (zero-indexed: header=3) [3].

    Expected columns (per user-confirmed row 4 headers):
        Status, Title, SKU, Product Type, Listing Action, Parentage Level,
        Parent SKU, Item Name, Brand Name, Product Id, Product Id Type,
        Item Type Name, Your Price INR (Sell on Amazon, IN),
        Item Package Length, Package Length Unit, Item Package Width,
        Package Width Unit, Item Package Height, Package Height Unit,
        Package Weight, Package Weight Unit, Country of Origin, ...

    Note: Amazon's Template sheet has rows above the header (descriptions,
    instructions). Data starts at row 7 (zero-indexed 6) per Amazon's
    settings (labelRow=4, attributeRow=5, dataRow=7) [3].

    Args:
        file_buffer: File-like object containing Amazon .xlsx

    Returns:
        tuple: (DataFrame with unified schema, error_message)
    """
    try:
        logger.info("Parsing Amazon Category Listings Report...")

        # Try to read the 'Template' sheet specifically
        try:
            xls = pd.ExcelFile(file_buffer)
            sheet_name = None
            for s in xls.sheet_names:
                if s.lower() == "template":
                    sheet_name = s
                    break
            if not sheet_name:
                return None, (
                    f"Amazon file missing 'Template' sheet. "
                    f"Found sheets: {xls.sheet_names}"
                )

            # Header on row 4 (zero-indexed = 3); data starts at row 7
            # We read with header=3, then drop rows above the data (rows 5-6
            # which contain attribute names / examples)
            raw = pd.read_excel(file_buffer, sheet_name=sheet_name, header=3)
        except Exception as e:
            logger.error(f"Failed to read Amazon Excel: {str(e)}")
            return None, f"Could not read Amazon file: {str(e)}"

        if raw.empty:
            return None, "Amazon Template sheet is empty"

        # Find SKU column (primary key)
        sku_col = _find_col(raw.columns, "SKU")
        if not sku_col:
            return None, "Amazon Template sheet missing 'SKU' column"

        # Drop description/example rows that sit between header (row 4) and
        # actual data (row 7). These rows typically have non-SKU-like values
        # in the SKU column (e.g., "Product Identifier" or example text).
        df = raw[raw[sku_col].notna()].copy()

        # Filter out template instruction rows — Amazon templates often include
        # 1-2 example/description rows before real data
        desc_markers = [
            "Product Identifier", "Required", "Optional", "Example",
            "SKU", "your-sku", "item_sku"
        ]
        for marker in desc_markers:
            df = df[~df[sku_col].astype(str).str.fullmatch(marker, case=False, na=False)]

        if df.empty:
            return None, "Amazon Template sheet has no data rows after filtering descriptions"

        # Locate columns (per user-confirmed row 4 headers)
        product_id_col = _find_col(df.columns, "Product Id")
        item_name_col = _find_col(df.columns, "Item Name", "Title")
        brand_col = _find_col(df.columns, "Brand Name", "Brand")
        item_type_col = _find_col(df.columns, "Item Type Name")
        status_col = _find_col(df.columns, "Status")

        # Price column has marketplace suffix in INR
        price_col = _find_col(
            df.columns,
            "Your Price INR (Sell on Amazon, IN)",
            "Your Price INR",
            "Standard Price",
            "standard_price"
        )

        # Package dimension columns + their unit columns
        pkg_l_col = _find_col(df.columns, "Item Package Length")
        pkg_l_unit_col = _find_col(df.columns, "Package Length Unit")
        pkg_w_col = _find_col(df.columns, "Item Package Width")
        pkg_w_unit_col = _find_col(df.columns, "Package Width Unit")
        pkg_h_col = _find_col(df.columns, "Item Package Height")
        pkg_h_unit_col = _find_col(df.columns, "Package Height Unit")
        pkg_wt_col = _find_col(df.columns, "Package Weight")
        pkg_wt_unit_col = _find_col(df.columns, "Package Weight Unit")

        # Tax code (may not exist in user's specific category template — that's OK)
        tax_col = _find_col(df.columns, "Product Tax Code", "product_tax_code")

        rows = []
        skipped = 0
        for _, src_row in df.iterrows():
            internal_sku = clean_sku(src_row.get(sku_col))
            if not internal_sku:
                skipped += 1
                continue

            # Channel SKU = ASIN (Product Id). If missing, fall back to SKU
            # since Amazon allows new listings without an existing ASIN.
            channel_sku = _safe_str(src_row.get(product_id_col)) if product_id_col else None
            if not channel_sku:
                # For new listings, ASIN may not yet be assigned. Use SKU
                # prefixed with marketplace as a synthetic channel_sku.
                channel_sku = f"AMZ-{internal_sku}"
                logger.debug(
                    f"Amazon row missing Product Id for SKU {internal_sku}; "
                    f"using synthetic channel_sku '{channel_sku}'"
                )

            # Convert dimensions to cm and weight to kg
            length_cm = (
                _convert_length(src_row.get(pkg_l_col), src_row.get(pkg_l_unit_col))
                if pkg_l_col else None
            )
            width_cm = (
                _convert_length(src_row.get(pkg_w_col), src_row.get(pkg_w_unit_col))
                if pkg_w_col else None
            )
            height_cm = (
                _convert_length(src_row.get(pkg_h_col), src_row.get(pkg_h_unit_col))
                if pkg_h_col else None
            )
            weight_kg = (
                _convert_weight(src_row.get(pkg_wt_col), src_row.get(pkg_wt_unit_col))
                if pkg_wt_col else None
            )

            # Parse status — Amazon uses values like "Active", "Inactive", "Incomplete"
            status_raw = _safe_str(src_row.get(status_col)) if status_col else None
            listing_status = (status_raw or "ACTIVE").upper()

            rows.append({
                "internal_sku": internal_sku,
                "marketplace": "Amazon",
                "channel_sku": channel_sku,
                "name": _safe_str(src_row.get(item_name_col)) if item_name_col else None,
                "brand": _safe_str(src_row.get(brand_col)) if brand_col else None,
                "category": _safe_str(src_row.get(item_type_col)) if item_type_col else None,
                "mrp": None,  # No reliable MRP field in standard Amazon template
                "selling_price": _safe_float(src_row.get(price_col)) if price_col else None,
                "hsn": None,  # Not standard in Amazon template
                "gst_rate": _parse_amazon_tax(src_row.get(tax_col)) if tax_col else None,
                "listing_status": listing_status,
                "length_cm": length_cm,
                "width_cm": width_cm,
                "height_cm": height_cm,
                "weight_kg": weight_kg,
            })

        if not rows:
            return None, "Amazon file produced no valid rows"

        result = pd.DataFrame(rows, columns=UNIFIED_COLUMNS)
        # Default selling_price to 0.0 if NULL (matches schema default [16])
        result["selling_price"] = result["selling_price"].fillna(0.0)

        logger.info(
            f"✅ Parsed Amazon listings: {len(result)} rows "
            f"({skipped} skipped due to missing SKU)"
        )
        return result, None

    except Exception as e:
        error_msg = f"Error reading Amazon file: {str(e)}"
        logger.error(error_msg, exc_info=True)
        return None, error_msg


# =============================================================================
# UNIFIED ORCHESTRATOR (convenience helper)
# =============================================================================

def parse_all_listings(files_dict):
    """
    Parse all uploaded marketplace listing files and concatenate into one
    unified staging DataFrame.

    Mirrors the dispatcher pattern from inventory_service.py [9].

    Args:
        files_dict: Dict mapping marketplace key to file buffer, e.g.:
            {
                'amazon': <file>,    # or None
                'flipkart': <file>,  # or None
                'meesho': <file>,    # or None
            }

    Returns:
        tuple: (DataFrame, list of error messages)
            - DataFrame: concatenated unified-schema rows from all parsed files
              (empty DataFrame if nothing parsed successfully)
            - list[str]: per-file error messages (empty list = all succeeded)
    """
    parser_map = {
        'amazon': parse_amazon_listings,
        'flipkart': parse_flipkart_listings,
        'meesho': parse_meesho_listings,
    }

    dfs = []
    errors = []

    for mkt_key, file_obj in files_dict.items():
        if not file_obj:
            continue

        parser = parser_map.get(mkt_key.lower())
        if not parser:
            msg = f"No listing parser available for marketplace: {mkt_key}"
            logger.warning(msg)
            errors.append(msg)
            continue

        logger.info(f"Dispatching listing parser for: {mkt_key}")
        df, err = parser(file_obj)

        if df is not None and not df.empty:
            dfs.append(df)
            logger.info(f"  → {mkt_key}: {len(df)} rows staged")
        elif err:
            errors.append(f"{mkt_key}: {err}")
            logger.warning(f"  → {mkt_key} parse error: {err}")

    if not dfs:
        logger.warning("parse_all_listings produced no rows from any file")
        return _empty_unified_df(), errors

    combined = pd.concat(dfs, ignore_index=True)
    logger.info(
        f"✅ parse_all_listings complete: {len(combined)} total rows from "
        f"{len(dfs)} marketplace(s)"
    )
    return combined, errors