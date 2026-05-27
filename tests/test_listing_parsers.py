"""
Unit tests for src/infrastructure/listing_parsers.py

Covers:
- clean_sku()                    : normalization & NaN handling
- _safe_float() / _safe_str()    : safe type coercion
- _convert_length() / _convert_weight() : unit conversion
- _parse_flipkart_tax()          : GST_5 → 5.0 mapping
- _parse_amazon_tax()            : A_GEN_STANDARD → 18.0 mapping
- parse_flipkart_listings()      : description row filter, dimensions, tax
- parse_meesho_listings()        : SKU mapping, NULL price/dims handling
- parse_amazon_listings()        : Template sheet header=3, unit conversion
- parse_all_listings()           : orchestrator dispatch + concat
- Error contract                 : (None, error) on failure, never raises
"""
import io

import numpy as np
import pandas as pd
import pytest

from src.infrastructure.listing_parsers import (
    clean_sku,
    _safe_float,
    _safe_str,
    _convert_length,
    _convert_weight,
    _parse_flipkart_tax,
    _parse_amazon_tax,
    parse_flipkart_listings,
    parse_meesho_listings,
    parse_amazon_listings,
    parse_all_listings,
    UNIFIED_COLUMNS,
    FLIPKART_TAX_MAP,
    AMAZON_TAX_MAP,
)


# =============================================================================
# FIXTURE BUILDERS — generate in-memory Excel files matching real marketplace formats
# =============================================================================

def _make_flipkart_xlsx(rows):
    """
    Build an in-memory Flipkart-style xlsx file.

    Real Flipkart files have a header row + a description row [1].
    We mimic that structure: row 0 = headers, row 1 = description text,
    row 2+ = actual data.

    Args:
        rows: list of dicts, each representing one product

    Returns:
        BytesIO with .name attribute set
    """
    columns = [
        "Product Title", "Seller SKU Id", "Sub-category", "Listing ID",
        "Listing Status", "MRP", "Your Selling Price",
        "Package Length", "Package Breadth", "Package Height", "Package Weight",
        "Harmonized System Nomenclature", "Tax Code",
    ]
    # Description row (Flipkart inserts this after the header) [1]
    desc_row = {
        "Product Title": "Title of your product as on Flipkart.com",
        "Seller SKU Id": "Your Identifier for a product",
        "Sub-category": "Category of the product",
        "Listing ID": "Flipkart's Identifier of the Listing",
        "Listing Status": "Status of your product",
        "MRP": "MRP of your product",
        "Your Selling Price": "Selling Price of your product",
        "Package Length": "Length of package in cms",
        "Package Breadth": "Breadth of package in cms",
        "Package Height": "Height of package in cms",
        "Package Weight": "Weight of package in Kgs",
        "Harmonized System Nomenclature": "Applicable HSN code for the product",
        "Tax Code": "Applicable tax slab for the product",
    }
    all_rows = [desc_row] + rows
    df = pd.DataFrame(all_rows, columns=columns)
    buf = io.BytesIO()
    df.to_excel(buf, index=False, engine="openpyxl")
    buf.seek(0)
    buf.name = "flipkart_test.xlsx"
    return buf


def _make_meesho_xlsx(rows):
    """
    Build an in-memory Meesho-style xlsx file.

    Real Meesho files have header + description row [2].
    """
    columns = [
        "SERIAL NO", "CATALOG NAME", "CATALOG ID", "PRODUCT NAME",
        "PRODUCT ID", "STYLE ID", "VARIATION ID", "VARIATION",
        "STOCK", "SYSTEM STOCK COUNT", "YOUR STOCK COUNT",
    ]
    # Description row (Meesho inserts this) [2]
    desc_row = {
        "SERIAL NO": "Row identifier",
        "CATALOG NAME": "Catalog name",
        "CATALOG ID": "Catalog id",
        "PRODUCT NAME": "Product name",
        "PRODUCT ID": "Product id",
        "STYLE ID": "Product ID/Style ID",
        "VARIATION ID": "Variation id",
        "VARIATION": "Variation",
        "STOCK": "Stock type (IN_STOCK / OUT_OF_STOCK / ALL)",
        "SYSTEM STOCK COUNT": "Current system stock count",
        "YOUR STOCK COUNT": "Edit this (keep empty if no change in stock)",
    }
    all_rows = [desc_row] + rows
    df = pd.DataFrame(all_rows, columns=columns)
    buf = io.BytesIO()
    df.to_excel(buf, index=False, engine="openpyxl")
    buf.seek(0)
    buf.name = "meesho_test.xlsx"
    return buf


def _make_amazon_xlsx(rows, sheet_name="Template"):
    """
    Build an in-memory Amazon-style xlsx file with the Template sheet.

    Real Amazon Category Listings Reports have header at row 4 (0-indexed=3) [3].
    We mimic the structure: 3 padding rows + header row + optional example row + data.
    """
    columns = [
        "Status", "Title", "SKU", "Product Type", "Listing Action",
        "Item Name", "Brand Name", "Product Id", "Product Id Type",
        "Item Type Name",
        "Your Price INR (Sell on Amazon, IN)",
        "Item Package Length", "Package Length Unit",
        "Item Package Width", "Package Width Unit",
        "Item Package Height", "Package Height Unit",
        "Package Weight", "Package Weight Unit",
        "Country of Origin",
    ]

    # Build a DataFrame with 3 empty rows above the header
    # Pandas to_excel doesn't easily support multi-tier headers, so we write
    # raw via openpyxl-compatible approach: build a dict of column-positioned data
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        # Write 3 padding rows + header row + data rows
        # Padding rows can contain anything — Amazon uses descriptions/instructions
        padding = pd.DataFrame(
            [[f"Padding row {i+1}"] + [""] * (len(columns) - 1) for i in range(3)],
            columns=columns,
        )
        # Real data
        if rows:
            data_df = pd.DataFrame(rows, columns=columns)
        else:
            data_df = pd.DataFrame(columns=columns)

        # Stack: 3 padding + header (auto from data) + data
        # Trick: write padding without headers, then header+data with headers
        padding.to_excel(writer, sheet_name=sheet_name, index=False, header=False, startrow=0)
        data_df.to_excel(writer, sheet_name=sheet_name, index=False, header=True, startrow=3)

    buf.seek(0)
    buf.name = "amazon_test.xlsx"
    return buf


# =============================================================================
# clean_sku()
# =============================================================================

@pytest.mark.unit
@pytest.mark.parsers
class TestCleanSku:
    """Mirror clean_sku tests from test_parsers.py [29]."""

    def test_uppercase_and_strip(self):
        assert clean_sku("  cap-trucker-grey  ") == "CAP-TRUCKER-GREY"

    def test_already_clean(self):
        assert clean_sku("CAP-TRUCKER-GREY") == "CAP-TRUCKER-GREY"

    def test_nan_returns_none(self):
        # Listing parsers return None on NaN (different from sales parsers'
        # 'UNKNOWN' default — wizard rows must be skippable)
        assert clean_sku(np.nan) is None

    def test_empty_string_returns_none(self):
        assert clean_sku("") is None

    def test_whitespace_only_returns_none(self):
        assert clean_sku("   ") is None

    def test_numeric_sku_coerced_to_string(self):
        assert clean_sku(12345) == "12345"


# =============================================================================
# Safe coercion helpers
# =============================================================================

@pytest.mark.unit
@pytest.mark.parsers
class TestSafeCoercion:
    """_safe_float and _safe_str must never raise."""

    def test_safe_float_valid(self):
        assert _safe_float("123.45") == 123.45

    def test_safe_float_nan_returns_none(self):
        assert _safe_float(np.nan) is None

    def test_safe_float_garbage_returns_none(self):
        assert _safe_float("not-a-number") is None

    def test_safe_float_inf_returns_none(self):
        assert _safe_float(float("inf")) is None

    def test_safe_str_valid(self):
        assert _safe_str("  hello  ") == "hello"

    def test_safe_str_nan_returns_none(self):
        assert _safe_str(np.nan) is None

    def test_safe_str_empty_returns_none(self):
        assert _safe_str("") is None


# =============================================================================
# Unit conversion
# =============================================================================

@pytest.mark.unit
@pytest.mark.parsers
class TestUnitConversion:
    """Unit conversion for Amazon dimensions/weights."""

    def test_cm_no_conversion(self):
        assert _convert_length(20, "centimeters") == 20.0

    def test_inches_to_cm(self):
        # 10 inches = 25.4 cm
        result = _convert_length(10, "inches")
        assert abs(result - 25.4) < 0.01

    def test_meters_to_cm(self):
        assert _convert_length(1, "meters") == 100.0

    def test_kg_no_conversion(self):
        assert _convert_weight(1.5, "kilograms") == 1.5

    def test_grams_to_kg(self):
        assert _convert_weight(500, "grams") == 0.5

    def test_pounds_to_kg(self):
        # 1 lb ≈ 0.4536 kg
        result = _convert_weight(1, "pounds")
        assert abs(result - 0.4536) < 0.01

    def test_none_value_returns_none(self):
        assert _convert_length(None, "cm") is None
        assert _convert_weight(np.nan, "kg") is None

    def test_unknown_unit_assumes_base(self):
        # Unknown length unit → assume cm (returns value unchanged)
        result = _convert_length(20, "blargs")
        assert result == 20.0

    def test_case_insensitive_unit(self):
        assert _convert_length(10, "INCHES") == _convert_length(10, "inches")


# =============================================================================
# Tax code parsing
# =============================================================================

@pytest.mark.unit
@pytest.mark.parsers
class TestFlipkartTax:
    """Flipkart Tax Code parsing — matches the GST_5 convention from real files [1]."""

    def test_gst_5(self):
        assert _parse_flipkart_tax("GST_5") == 5.0

    def test_gst_18(self):
        assert _parse_flipkart_tax("GST_18") == 18.0

    def test_gst_28(self):
        assert _parse_flipkart_tax("GST_28") == 28.0

    def test_gst_0(self):
        assert _parse_flipkart_tax("GST_0") == 0.0

    def test_lowercase_input(self):
        # Per the parser implementation, input is uppercased before lookup
        assert _parse_flipkart_tax("gst_5") == 5.0

    def test_whitespace_input(self):
        assert _parse_flipkart_tax("  GST_12  ") == 12.0

    def test_unknown_code_returns_none(self):
        # Falls through to None — matches "zero/blank default" rule
        assert _parse_flipkart_tax("UNKNOWN_CODE") is None

    def test_none_input(self):
        assert _parse_flipkart_tax(None) is None

    def test_nan_input(self):
        assert _parse_flipkart_tax(np.nan) is None

    def test_generic_pattern_fallback(self):
        # Generic GST_<n> pattern works even if not in explicit map
        assert _parse_flipkart_tax("GST_7") == 7.0

    def test_all_known_codes_present(self):
        """Sanity check that constants dict covers expected GST slabs."""
        for code in ["GST_0", "GST_3", "GST_5", "GST_12", "GST_18", "GST_28"]:
            assert code in FLIPKART_TAX_MAP


@pytest.mark.unit
@pytest.mark.parsers
class TestAmazonTax:
    """Amazon Product Tax Code parsing — uses post-Sep-22-2025 GST rates."""

    def test_a_gen_standard(self):
        assert _parse_amazon_tax("A_GEN_STANDARD") == 18.0

    def test_a_gen_superreduced(self):
        assert _parse_amazon_tax("A_GEN_SUPERREDUCED") == 5.0

    def test_a_gen_reduced_post_sep_2025(self):
        # Per user-provided table: A_GEN_REDUCED maps to 5% post-Sep-22-2025
        assert _parse_amazon_tax("A_GEN_REDUCED") == 5.0

    def test_a_gen_peak_post_sep_2025(self):
        # A_GEN_PEAK merged from 28% to 18% post-Sep-22-2025
        assert _parse_amazon_tax("A_GEN_PEAK") == 18.0

    def test_a_gen_peak_cess12_merged(self):
        # 28% + 12% Cess merged to 40% post-Sep-22-2025
        assert _parse_amazon_tax("A_GEN_PEAK_CESS12") == 40.0

    def test_a_gen_peak_cess60_stores_gst_only(self):
        # Cess (60%) is unmodeled in schema; we store only the 28% GST portion
        assert _parse_amazon_tax("A_GEN_PEAK_CESS60") == 28.0

    def test_a_gen_jewellery(self):
        assert _parse_amazon_tax("A_GEN_JEWELLERY") == 3.0

    def test_a_gen_minimum(self):
        assert _parse_amazon_tax("A_GEN_MINIMUM") == 0.25

    def test_a_gen_exempt(self):
        assert _parse_amazon_tax("A_GEN_EXEMPT") == 0.0

    def test_lowercase_input(self):
        assert _parse_amazon_tax("a_gen_standard") == 18.0

    def test_unknown_code_returns_none(self):
        assert _parse_amazon_tax("A_UNKNOWN") is None

    def test_none_input(self):
        assert _parse_amazon_tax(None) is None

    def test_nan_input(self):
        assert _parse_amazon_tax(np.nan) is None

    def test_all_documented_codes_in_map(self):
        """All 9 codes from the user-provided table must be in the constants."""
        expected_codes = [
            "A_GEN_EXEMPT", "A_GEN_MINIMUM", "A_GEN_SUPERREDUCED",
            "A_GEN_REDUCED", "A_GEN_STANDARD", "A_GEN_PEAK",
            "A_GEN_PEAK_CESS12", "A_GEN_PEAK_CESS60", "A_GEN_JEWELLERY",
        ]
        for code in expected_codes:
            assert code in AMAZON_TAX_MAP, f"Missing tax code: {code}"


# =============================================================================
# parse_flipkart_listings()
# =============================================================================

@pytest.mark.unit
@pytest.mark.parsers
class TestParseFlipkartListings:
    """End-to-end Flipkart listings parser tests."""

    def test_basic_parse(self):
        rows = [{
            "Product Title": "Premium Grey Cap",
            "Seller SKU Id": "CAP-TRUCKER-PLAIN-GREY",
            "Sub-category": "Caps",
            "Listing ID": "LSTXCAHNDGHXHQ3SRFZCWEITT",
            "Listing Status": "ACTIVE",
            "MRP": 499,
            "Your Selling Price": 257,
            "Package Length": 20,
            "Package Breadth": 20,
            "Package Height": 4,
            "Package Weight": 0.1,
            "Harmonized System Nomenclature": "6505",
            "Tax Code": "GST_5",
        }]
        buf = _make_flipkart_xlsx(rows)
        df, err = parse_flipkart_listings(buf)

        assert err is None
        assert df is not None
        assert len(df) == 1

    def test_unified_schema_columns(self):
        rows = [{
            "Product Title": "Test Cap", "Seller SKU Id": "TEST-SKU-001",
            "Sub-category": "Caps", "Listing ID": "LST123", "Listing Status": "ACTIVE",
            "MRP": 500, "Your Selling Price": 300,
            "Package Length": 20, "Package Breadth": 20,
            "Package Height": 5, "Package Weight": 0.15,
            "Harmonized System Nomenclature": "6505", "Tax Code": "GST_5",
        }]
        df, _ = parse_flipkart_listings(_make_flipkart_xlsx(rows))
        assert list(df.columns) == UNIFIED_COLUMNS

    def test_marketplace_tagged(self):
        rows = [{
            "Product Title": "X", "Seller SKU Id": "SKU1",
            "Sub-category": "C", "Listing ID": "L1", "Listing Status": "ACTIVE",
            "MRP": 100, "Your Selling Price": 80,
            "Package Length": 10, "Package Breadth": 10,
            "Package Height": 2, "Package Weight": 0.05,
            "Harmonized System Nomenclature": "1234", "Tax Code": "GST_18",
        }]
        df, _ = parse_flipkart_listings(_make_flipkart_xlsx(rows))
        assert (df["marketplace"] == "Flipkart").all()

    def test_sku_normalized(self):
        rows = [{
            "Product Title": "X", "Seller SKU Id": "  cap-trucker-grey  ",
            "Sub-category": "C", "Listing ID": "L1", "Listing Status": "ACTIVE",
            "MRP": 100, "Your Selling Price": 80,
            "Package Length": 10, "Package Breadth": 10,
            "Package Height": 2, "Package Weight": 0.05,
            "Harmonized System Nomenclature": "1234", "Tax Code": "GST_5",
        }]
        df, _ = parse_flipkart_listings(_make_flipkart_xlsx(rows))
        assert df.iloc[0]["internal_sku"] == "CAP-TRUCKER-GREY"

    def test_tax_code_parsed(self):
        rows = [{
            "Product Title": "X", "Seller SKU Id": "SKU1",
            "Sub-category": "C", "Listing ID": "L1", "Listing Status": "ACTIVE",
            "MRP": 100, "Your Selling Price": 80,
            "Package Length": 10, "Package Breadth": 10,
            "Package Height": 2, "Package Weight": 0.05,
            "Harmonized System Nomenclature": "1234", "Tax Code": "GST_5",
        }]
        df, _ = parse_flipkart_listings(_make_flipkart_xlsx(rows))
        assert df.iloc[0]["gst_rate"] == 5.0

    def test_dimensions_preserved_as_cm(self):
        # Flipkart dims are already in cm/kg per file convention [1]
        rows = [{
            "Product Title": "X", "Seller SKU Id": "SKU1",
            "Sub-category": "C", "Listing ID": "L1", "Listing Status": "ACTIVE",
            "MRP": 100, "Your Selling Price": 80,
            "Package Length": 25, "Package Breadth": 15,
            "Package Height": 3, "Package Weight": 0.2,
            "Harmonized System Nomenclature": "6505", "Tax Code": "GST_5",
        }]
        df, _ = parse_flipkart_listings(_make_flipkart_xlsx(rows))
        row = df.iloc[0]
        assert row["length_cm"] == 25.0
        assert row["width_cm"] == 15.0
        assert row["height_cm"] == 3.0
        assert row["weight_kg"] == 0.2

    def test_description_row_filtered(self):
        # The description row inserted by _make_flipkart_xlsx must be dropped [1]
        rows = [{
            "Product Title": "Real Product", "Seller SKU Id": "REAL-SKU",
            "Sub-category": "C", "Listing ID": "L1", "Listing Status": "ACTIVE",
            "MRP": 100, "Your Selling Price": 80,
            "Package Length": 10, "Package Breadth": 10,
            "Package Height": 2, "Package Weight": 0.05,
            "Harmonized System Nomenclature": "1234", "Tax Code": "GST_5",
        }]
        df, _ = parse_flipkart_listings(_make_flipkart_xlsx(rows))
        # Should have exactly 1 row (description row filtered out)
        assert len(df) == 1
        assert df.iloc[0]["internal_sku"] == "REAL-SKU"
        # Description text should NOT appear as a SKU
        assert "Your Identifier" not in df["internal_sku"].astype(str).values

    def test_multiple_rows(self):
        rows = [
            {
                "Product Title": "Cap A", "Seller SKU Id": "CAP-A",
                "Sub-category": "C", "Listing ID": "LA", "Listing Status": "ACTIVE",
                "MRP": 100, "Your Selling Price": 80,
                "Package Length": 10, "Package Breadth": 10,
                "Package Height": 2, "Package Weight": 0.05,
                "Harmonized System Nomenclature": "1234", "Tax Code": "GST_5",
            },
            {
                "Product Title": "Cap B", "Seller SKU Id": "CAP-B",
                "Sub-category": "C", "Listing ID": "LB", "Listing Status": "INACTIVE",
                "MRP": 200, "Your Selling Price": 150,
                "Package Length": 15, "Package Breadth": 12,
                "Package Height": 3, "Package Weight": 0.08,
                "Harmonized System Nomenclature": "1234", "Tax Code": "GST_18",
            },
        ]
        df, _ = parse_flipkart_listings(_make_flipkart_xlsx(rows))
        assert len(df) == 2
        assert set(df["internal_sku"]) == {"CAP-A", "CAP-B"}

    def test_listing_status_uppercased(self):
        rows = [{
            "Product Title": "X", "Seller SKU Id": "SKU1",
            "Sub-category": "C", "Listing ID": "L1", "Listing Status": "active",
            "MRP": 100, "Your Selling Price": 80,
            "Package Length": 10, "Package Breadth": 10,
            "Package Height": 2, "Package Weight": 0.05,
            "Harmonized System Nomenclature": "1234", "Tax Code": "GST_5",
        }]
        df, _ = parse_flipkart_listings(_make_flipkart_xlsx(rows))
        assert df.iloc[0]["listing_status"] == "ACTIVE"

    def test_missing_listing_id_skipped(self):
        # Listing ID is critical for channel_listings PK [16] — rows missing
        # it must be skipped (not crash, not produce bad data)
        rows = [{
            "Product Title": "X", "Seller SKU Id": "SKU1",
            "Sub-category": "C", "Listing ID": None, "Listing Status": "ACTIVE",
            "MRP": 100, "Your Selling Price": 80,
            "Package Length": 10, "Package Breadth": 10,
            "Package Height": 2, "Package Weight": 0.05,
            "Harmonized System Nomenclature": "1234", "Tax Code": "GST_5",
        }]
        df, err = parse_flipkart_listings(_make_flipkart_xlsx(rows))
        # Either returns empty df with error message OR returns None+error
        if df is not None:
            assert len(df) == 0 or "SKU1" not in df["internal_sku"].values
        else:
            assert err is not None

    def test_selling_price_defaults_to_zero_when_missing(self):
        # Per user spec: missing prices default to 0.0 (matches schema [16])
        rows = [{
            "Product Title": "X", "Seller SKU Id": "SKU1",
            "Sub-category": "C", "Listing ID": "L1", "Listing Status": "ACTIVE",
            "MRP": 100, "Your Selling Price": None,
            "Package Length": 10, "Package Breadth": 10,
            "Package Height": 2, "Package Weight": 0.05,
            "Harmonized System Nomenclature": "1234", "Tax Code": "GST_5",
        }]
        df, _ = parse_flipkart_listings(_make_flipkart_xlsx(rows))
        assert df.iloc[0]["selling_price"] == 0.0

    def test_unknown_tax_code_returns_null_gst(self):
        # Per "zero/blank defaults" rule, unknown codes → NULL gst_rate
        rows = [{
            "Product Title": "X", "Seller SKU Id": "SKU1",
            "Sub-category": "C", "Listing ID": "L1", "Listing Status": "ACTIVE",
            "MRP": 100, "Your Selling Price": 80,
            "Package Length": 10, "Package Breadth": 10,
            "Package Height": 2, "Package Weight": 0.05,
            "Harmonized System Nomenclature": "1234", "Tax Code": "UNKNOWN_CODE",
        }]
        df, _ = parse_flipkart_listings(_make_flipkart_xlsx(rows))
        assert pd.isna(df.iloc[0]["gst_rate"])

    def test_empty_file_returns_error(self):
        # Empty rows list → only description row → no real data
        buf = _make_flipkart_xlsx([])
        df, err = parse_flipkart_listings(buf)
        # Either empty df or explicit error
        assert df is None or len(df) == 0

    def test_garbage_input_does_not_raise(self):
        # Per parser contract [15], never raise on bad input
        buf = io.BytesIO(b"this is not an xlsx file")
        buf.name = "garbage.xlsx"
        df, err = parse_flipkart_listings(buf)
        assert df is None
        assert err is not None


# =============================================================================
# parse_meesho_listings()
# =============================================================================

@pytest.mark.unit
@pytest.mark.parsers
class TestParseMeeshoListings:
    """Meesho parser — handles SKU mapping only [2]; price/HSN/dims unavailable."""

    def test_basic_parse(self):
        rows = [{
            "SERIAL NO": 1, "CATALOG NAME": "Fashionable Latest Men Caps & Hats",
            "CATALOG ID": 486353323,
            "PRODUCT NAME": "Premium Black Waterproof Cap for Men",
            "PRODUCT ID": 941463175, "STYLE ID": "CAP-RAIN-MILITARY-BLACK",
            "VARIATION ID": 167, "VARIATION": "Free Size",
            "STOCK": "ALL", "SYSTEM STOCK COUNT": 1000, "YOUR STOCK COUNT": "",
        }]
        buf = _make_meesho_xlsx(rows)
        df, err = parse_meesho_listings(buf)

        assert err is None
        assert df is not None
        assert len(df) == 1

    def test_unified_schema(self):
        rows = [{
            "SERIAL NO": 1, "CATALOG NAME": "X", "CATALOG ID": 1,
            "PRODUCT NAME": "Test", "PRODUCT ID": 999, "STYLE ID": "TEST-SKU",
            "VARIATION ID": 1, "VARIATION": "Free Size",
            "STOCK": "ALL", "SYSTEM STOCK COUNT": 100, "YOUR STOCK COUNT": "",
        }]
        df, _ = parse_meesho_listings(_make_meesho_xlsx(rows))
        assert list(df.columns) == UNIFIED_COLUMNS

    def test_marketplace_tagged(self):
        rows = [{
            "SERIAL NO": 1, "CATALOG NAME": "X", "CATALOG ID": 1,
            "PRODUCT NAME": "Test", "PRODUCT ID": 999, "STYLE ID": "TEST-SKU",
            "VARIATION ID": 1, "VARIATION": "Free Size",
            "STOCK": "ALL", "SYSTEM STOCK COUNT": 100, "YOUR STOCK COUNT": "",
        }]
        df, _ = parse_meesho_listings(_make_meesho_xlsx(rows))
        assert (df["marketplace"] == "Meesho").all()

    def test_style_id_used_as_internal_sku(self):
        # Meesho's STYLE ID is the seller SKU [2] — must map to internal_sku
        rows = [{
            "SERIAL NO": 1, "CATALOG NAME": "X", "CATALOG ID": 1,
            "PRODUCT NAME": "Test", "PRODUCT ID": 999,
            "STYLE ID": "CAP-TRUCKER-PLAIN-GREY",
            "VARIATION ID": 1, "VARIATION": "Free Size",
            "STOCK": "ALL", "SYSTEM STOCK COUNT": 100, "YOUR STOCK COUNT": "",
        }]
        df, _ = parse_meesho_listings(_make_meesho_xlsx(rows))
        assert df.iloc[0]["internal_sku"] == "CAP-TRUCKER-PLAIN-GREY"

    def test_style_id_used_as_channel_sku(self):
        rows = [{
            "SERIAL NO": 1, "CATALOG NAME": "X", "CATALOG ID": 486353323,
            "PRODUCT NAME": "Test", "PRODUCT ID": 939302749,
            "STYLE ID": "CAP-A",
            "VARIATION ID": 1, "VARIATION": "Free Size",
            "STOCK": "ALL", "SYSTEM STOCK COUNT": 100, "YOUR STOCK COUNT": "",
        }]
        df, _ = parse_meesho_listings(_make_meesho_xlsx(rows))
        assert df.iloc[0]["channel_sku"] == "CAP-A"

    def test_catalog_id_used_as_channel_id(self):
        rows = [{
            "SERIAL NO": 1, "CATALOG NAME": "X", "CATALOG ID": 486353323,
            "PRODUCT NAME": "Test", "PRODUCT ID": 939302749,
            "STYLE ID": "CAP-A",
            "VARIATION ID": 1, "VARIATION": "Free Size",
            "STOCK": "ALL", "SYSTEM STOCK COUNT": 100, "YOUR STOCK COUNT": "",
        }]
        df, _ = parse_meesho_listings(_make_meesho_xlsx(rows))
        assert df.iloc[0]["channel_id"] == "486353323"


    def test_price_dims_hsn_all_null(self):
        # Meesho file has no price/dims/HSN — all must be NULL [2]
        rows = [{
            "SERIAL NO": 1, "CATALOG NAME": "X", "CATALOG ID": 1,
            "PRODUCT NAME": "Test", "PRODUCT ID": 1, "STYLE ID": "SKU-A",
            "VARIATION ID": 1, "VARIATION": "Free Size",
            "STOCK": "ALL", "SYSTEM STOCK COUNT": 100, "YOUR STOCK COUNT": "",
        }]
        df, _ = parse_meesho_listings(_make_meesho_xlsx(rows))
        row = df.iloc[0]
        assert row["selling_price"] == 0.0  # default, not NULL (schema rule [16])
        assert pd.isna(row["mrp"])
        assert pd.isna(row["hsn"])
        assert pd.isna(row["gst_rate"])
        assert pd.isna(row["length_cm"])
        assert pd.isna(row["width_cm"])
        assert pd.isna(row["height_cm"])
        assert pd.isna(row["weight_kg"])

    def test_catalog_name_maps_to_brand(self):
        rows = [{
            "SERIAL NO": 1, "CATALOG NAME": "Fashionable Latest Men Caps",
            "CATALOG ID": 1,
            "PRODUCT NAME": "Test", "PRODUCT ID": 1, "STYLE ID": "SKU-A",
            "VARIATION ID": 1, "VARIATION": "Free Size",
            "STOCK": "ALL", "SYSTEM STOCK COUNT": 100, "YOUR STOCK COUNT": "",
        }]
        df, _ = parse_meesho_listings(_make_meesho_xlsx(rows))
        assert df.iloc[0]["brand"] == "Fashionable Latest Men Caps"

    def test_description_row_filtered(self):
        # Real Meesho files have a description row [2] — must be dropped
        rows = [{
            "SERIAL NO": 1, "CATALOG NAME": "X", "CATALOG ID": 1,
            "PRODUCT NAME": "Real Product", "PRODUCT ID": 1, "STYLE ID": "REAL-SKU",
            "VARIATION ID": 1, "VARIATION": "Free Size",
            "STOCK": "ALL", "SYSTEM STOCK COUNT": 100, "YOUR STOCK COUNT": "",
        }]
        df, _ = parse_meesho_listings(_make_meesho_xlsx(rows))
        assert len(df) == 1
        assert df.iloc[0]["internal_sku"] == "REAL-SKU"
        # Description marker text should NOT appear as a SKU
        assert "Product ID/Style ID" not in df["internal_sku"].astype(str).values

    def test_multiple_rows_real_sample(self):
        # Mirrors the actual Meesho sample data from the user's file [2]
        rows = [
            {
                "SERIAL NO": 1, "CATALOG NAME": "Fashionable Latest Men Caps & Hats",
                "CATALOG ID": 486353323,
                "PRODUCT NAME": "Premium Black Waterproof Cap for Men",
                "PRODUCT ID": 941463175, "STYLE ID": "CAP-RAIN-MILITARY-BLACK",
                "VARIATION ID": 167, "VARIATION": "Free Size",
                "STOCK": "ALL", "SYSTEM STOCK COUNT": 1000, "YOUR STOCK COUNT": "",
            },
            {
                "SERIAL NO": 10, "CATALOG NAME": "Casual Trendy Men Caps & Hats",
                "CATALOG ID": 484985445,
                "PRODUCT NAME": "Classic Grey Net Cap",
                "PRODUCT ID": 939302749, "STYLE ID": "CAP-TRUCKER-PLAIN-GREY",
                "VARIATION ID": 167, "VARIATION": "Free Size",
                "STOCK": "ALL", "SYSTEM STOCK COUNT": 1000, "YOUR STOCK COUNT": "",
            },
        ]
        df, err = parse_meesho_listings(_make_meesho_xlsx(rows))
        assert err is None
        assert len(df) == 2
        assert set(df["internal_sku"]) == {
            "CAP-RAIN-MILITARY-BLACK", "CAP-TRUCKER-PLAIN-GREY"
        }

    def test_listing_status_defaults_to_active(self):
        # Meesho inventory file has no status column → defaults to ACTIVE
        rows = [{
            "SERIAL NO": 1, "CATALOG NAME": "X", "CATALOG ID": 1,
            "PRODUCT NAME": "Test", "PRODUCT ID": 1, "STYLE ID": "SKU-A",
            "VARIATION ID": 1, "VARIATION": "Free Size",
            "STOCK": "ALL", "SYSTEM STOCK COUNT": 100, "YOUR STOCK COUNT": "",
        }]
        df, _ = parse_meesho_listings(_make_meesho_xlsx(rows))
        assert df.iloc[0]["listing_status"] == "ACTIVE"

    def test_garbage_input_does_not_raise(self):
        # Per parser contract [15], never raise on bad input
        buf = io.BytesIO(b"this is not an xlsx file")
        buf.name = "garbage.xlsx"
        df, err = parse_meesho_listings(buf)
        assert df is None
        assert err is not None


# =============================================================================
# parse_amazon_listings()
# =============================================================================

@pytest.mark.unit
@pytest.mark.parsers
class TestParseAmazonListings:
    """Amazon parser — reads Template sheet with header at row 4 (header=3) [3]."""

    def test_basic_parse(self):
        rows = [{
            "Status": "Active", "Title": "Test Cap",
            "SKU": "CAP-TRUCKER-PLAIN-GREY",
            "Product Type": "HAT", "Listing Action": "",
            "Item Name": "Premium Grey Mesh Cap",
            "Brand Name": "TestBrand",
            "Product Id": "B001ABCDEF", "Product Id Type": "ASIN",
            "Item Type Name": "baseball-cap",
            "Your Price INR (Sell on Amazon, IN)": 299,
            "Item Package Length": 20, "Package Length Unit": "centimeters",
            "Item Package Width": 20, "Package Width Unit": "centimeters",
            "Item Package Height": 4, "Package Height Unit": "centimeters",
            "Package Weight": 0.1, "Package Weight Unit": "kilograms",
            "Country of Origin": "IN",
        }]
        buf = _make_amazon_xlsx(rows)
        df, err = parse_amazon_listings(buf)

        assert err is None
        assert df is not None
        assert len(df) == 1

    def test_unified_schema(self):
        rows = [{
            "Status": "Active", "Title": "X", "SKU": "TEST-SKU",
            "Product Type": "HAT", "Listing Action": "",
            "Item Name": "Test", "Brand Name": "Brand",
            "Product Id": "B001", "Product Id Type": "ASIN",
            "Item Type Name": "cap",
            "Your Price INR (Sell on Amazon, IN)": 100,
            "Item Package Length": 10, "Package Length Unit": "centimeters",
            "Item Package Width": 10, "Package Width Unit": "centimeters",
            "Item Package Height": 2, "Package Height Unit": "centimeters",
            "Package Weight": 0.05, "Package Weight Unit": "kilograms",
            "Country of Origin": "IN",
        }]
        df, _ = parse_amazon_listings(_make_amazon_xlsx(rows))
        assert list(df.columns) == UNIFIED_COLUMNS

    def test_marketplace_tagged(self):
        rows = [{
            "Status": "Active", "Title": "X", "SKU": "SKU1",
            "Product Type": "HAT", "Listing Action": "",
            "Item Name": "Test", "Brand Name": "B",
            "Product Id": "B001", "Product Id Type": "ASIN",
            "Item Type Name": "cap",
            "Your Price INR (Sell on Amazon, IN)": 100,
            "Item Package Length": 10, "Package Length Unit": "centimeters",
            "Item Package Width": 10, "Package Width Unit": "centimeters",
            "Item Package Height": 2, "Package Height Unit": "centimeters",
            "Package Weight": 0.05, "Package Weight Unit": "kilograms",
            "Country of Origin": "IN",
        }]
        df, _ = parse_amazon_listings(_make_amazon_xlsx(rows))
        assert (df["marketplace"] == "Amazon").all()

    def test_sku_used_as_internal_sku(self):
        rows = [{
            "Status": "Active", "Title": "X",
            "SKU": "  cap-trucker-plain-grey  ",  # tests normalization
            "Product Type": "HAT", "Listing Action": "",
            "Item Name": "Test", "Brand Name": "B",
            "Product Id": "B001", "Product Id Type": "ASIN",
            "Item Type Name": "cap",
            "Your Price INR (Sell on Amazon, IN)": 100,
            "Item Package Length": 10, "Package Length Unit": "centimeters",
            "Item Package Width": 10, "Package Width Unit": "centimeters",
            "Item Package Height": 2, "Package Height Unit": "centimeters",
            "Package Weight": 0.05, "Package Weight Unit": "kilograms",
            "Country of Origin": "IN",
        }]
        df, _ = parse_amazon_listings(_make_amazon_xlsx(rows))
        assert df.iloc[0]["internal_sku"] == "CAP-TRUCKER-PLAIN-GREY"

    def test_sku_used_as_channel_sku(self):
        rows = [{
            "Status": "Active", "Title": "X", "SKU": "SKU1",
            "Product Type": "HAT", "Listing Action": "",
            "Item Name": "Test", "Brand Name": "B",
            "Product Id": "B07ABCDEFG", "Product Id Type": "ASIN",
            "Item Type Name": "cap",
            "Your Price INR (Sell on Amazon, IN)": 100,
            "Item Package Length": 10, "Package Length Unit": "centimeters",
            "Item Package Width": 10, "Package Width Unit": "centimeters",
            "Item Package Height": 2, "Package Height Unit": "centimeters",
            "Package Weight": 0.05, "Package Weight Unit": "kilograms",
            "Country of Origin": "IN",
        }]
        df, _ = parse_amazon_listings(_make_amazon_xlsx(rows))
        assert df.iloc[0]["channel_sku"] == "SKU1"
        assert df.iloc[0]["channel_id"] == "B07ABCDEFG"

    def test_missing_product_id_handles_gracefully(self):
        # Amazon allows new listings without an ASIN — parser must handle gracefully
        rows = [{
            "Status": "Active", "Title": "X", "SKU": "NEW-SKU-001",
            "Product Type": "HAT", "Listing Action": "",
            "Item Name": "Test", "Brand Name": "B",
            "Product Id": None, "Product Id Type": None,
            "Item Type Name": "cap",
            "Your Price INR (Sell on Amazon, IN)": 100,
            "Item Package Length": 10, "Package Length Unit": "centimeters",
            "Item Package Width": 10, "Package Width Unit": "centimeters",
            "Item Package Height": 2, "Package Height Unit": "centimeters",
            "Package Weight": 0.05, "Package Weight Unit": "kilograms",
            "Country of Origin": "IN",
        }]
        df, err = parse_amazon_listings(_make_amazon_xlsx(rows))
        assert err is None
        assert len(df) == 1
        assert df.iloc[0]["channel_sku"] == "NEW-SKU-001"
        assert df.iloc[0]["channel_id"] == ""

    def test_dimensions_in_cm_no_conversion(self):
        rows = [{
            "Status": "Active", "Title": "X", "SKU": "SKU1",
            "Product Type": "HAT", "Listing Action": "",
            "Item Name": "Test", "Brand Name": "B",
            "Product Id": "B001", "Product Id Type": "ASIN",
            "Item Type Name": "cap",
            "Your Price INR (Sell on Amazon, IN)": 100,
            "Item Package Length": 25, "Package Length Unit": "centimeters",
            "Item Package Width": 15, "Package Width Unit": "centimeters",
            "Item Package Height": 3, "Package Height Unit": "centimeters",
            "Package Weight": 0.2, "Package Weight Unit": "kilograms",
            "Country of Origin": "IN",
        }]
        df, _ = parse_amazon_listings(_make_amazon_xlsx(rows))
        row = df.iloc[0]
        assert row["length_cm"] == 25.0
        assert row["width_cm"] == 15.0
        assert row["height_cm"] == 3.0
        assert row["weight_kg"] == 0.2

    def test_dimensions_inches_converted_to_cm(self):
        # 10 inches → 25.4 cm
        rows = [{
            "Status": "Active", "Title": "X", "SKU": "SKU1",
            "Product Type": "HAT", "Listing Action": "",
            "Item Name": "Test", "Brand Name": "B",
                        "Product Id": "B001", "Product Id Type": "ASIN",
            "Item Type Name": "cap",
            "Your Price INR (Sell on Amazon, IN)": 100,
            "Item Package Length": 10, "Package Length Unit": "inches",
            "Item Package Width": 8, "Package Width Unit": "inches",
            "Item Package Height": 2, "Package Height Unit": "inches",
            "Package Weight": 1, "Package Weight Unit": "pounds",
            "Country of Origin": "IN",
        }]
        df, _ = parse_amazon_listings(_make_amazon_xlsx(rows))
        row = df.iloc[0]
        # 10 inches = 25.4 cm
        assert abs(row["length_cm"] - 25.4) < 0.1
        # 8 inches = 20.32 cm
        assert abs(row["width_cm"] - 20.32) < 0.1
        # 2 inches = 5.08 cm
        assert abs(row["height_cm"] - 5.08) < 0.1
        # 1 pound ≈ 0.4536 kg
        assert abs(row["weight_kg"] - 0.4536) < 0.01

    def test_dimensions_grams_converted_to_kg(self):
        rows = [{
            "Status": "Active", "Title": "X", "SKU": "SKU1",
            "Product Type": "HAT", "Listing Action": "",
            "Item Name": "Test", "Brand Name": "B",
            "Product Id": "B001", "Product Id Type": "ASIN",
            "Item Type Name": "cap",
            "Your Price INR (Sell on Amazon, IN)": 100,
            "Item Package Length": 20, "Package Length Unit": "centimeters",
            "Item Package Width": 15, "Package Width Unit": "centimeters",
            "Item Package Height": 3, "Package Height Unit": "centimeters",
            "Package Weight": 250, "Package Weight Unit": "grams",
            "Country of Origin": "IN",
        }]
        df, _ = parse_amazon_listings(_make_amazon_xlsx(rows))
        # 250 grams = 0.25 kg
        assert abs(df.iloc[0]["weight_kg"] - 0.25) < 0.01

    def test_brand_name_captured(self):
        rows = [{
            "Status": "Active", "Title": "X", "SKU": "SKU1",
            "Product Type": "HAT", "Listing Action": "",
            "Item Name": "Test", "Brand Name": "Bludo Global",
            "Product Id": "B001", "Product Id Type": "ASIN",
            "Item Type Name": "cap",
            "Your Price INR (Sell on Amazon, IN)": 100,
            "Item Package Length": 10, "Package Length Unit": "centimeters",
            "Item Package Width": 10, "Package Width Unit": "centimeters",
            "Item Package Height": 2, "Package Height Unit": "centimeters",
            "Package Weight": 0.05, "Package Weight Unit": "kilograms",
            "Country of Origin": "IN",
        }]
        df, _ = parse_amazon_listings(_make_amazon_xlsx(rows))
        assert df.iloc[0]["brand"] == "Bludo Global"

    def test_item_type_maps_to_category(self):
        rows = [{
            "Status": "Active", "Title": "X", "SKU": "SKU1",
            "Product Type": "HAT", "Listing Action": "",
            "Item Name": "Test", "Brand Name": "B",
            "Product Id": "B001", "Product Id Type": "ASIN",
            "Item Type Name": "baseball-cap",
            "Your Price INR (Sell on Amazon, IN)": 100,
            "Item Package Length": 10, "Package Length Unit": "centimeters",
            "Item Package Width": 10, "Package Width Unit": "centimeters",
            "Item Package Height": 2, "Package Height Unit": "centimeters",
            "Package Weight": 0.05, "Package Weight Unit": "kilograms",
            "Country of Origin": "IN",
        }]
        df, _ = parse_amazon_listings(_make_amazon_xlsx(rows))
        assert df.iloc[0]["category"] == "baseball-cap"

    def test_selling_price_captured(self):
        rows = [{
            "Status": "Active", "Title": "X", "SKU": "SKU1",
            "Product Type": "HAT", "Listing Action": "",
            "Item Name": "Test", "Brand Name": "B",
            "Product Id": "B001", "Product Id Type": "ASIN",
            "Item Type Name": "cap",
            "Your Price INR (Sell on Amazon, IN)": 299.50,
            "Item Package Length": 10, "Package Length Unit": "centimeters",
            "Item Package Width": 10, "Package Width Unit": "centimeters",
            "Item Package Height": 2, "Package Height Unit": "centimeters",
            "Package Weight": 0.05, "Package Weight Unit": "kilograms",
            "Country of Origin": "IN",
        }]
        df, _ = parse_amazon_listings(_make_amazon_xlsx(rows))
        assert df.iloc[0]["selling_price"] == 299.50

    def test_status_uppercased(self):
        rows = [{
            "Status": "active", "Title": "X", "SKU": "SKU1",
            "Product Type": "HAT", "Listing Action": "",
            "Item Name": "Test", "Brand Name": "B",
            "Product Id": "B001", "Product Id Type": "ASIN",
            "Item Type Name": "cap",
            "Your Price INR (Sell on Amazon, IN)": 100,
            "Item Package Length": 10, "Package Length Unit": "centimeters",
            "Item Package Width": 10, "Package Width Unit": "centimeters",
            "Item Package Height": 2, "Package Height Unit": "centimeters",
            "Package Weight": 0.05, "Package Weight Unit": "kilograms",
            "Country of Origin": "IN",
        }]
        df, _ = parse_amazon_listings(_make_amazon_xlsx(rows))
        assert df.iloc[0]["listing_status"] == "ACTIVE"

    def test_garbage_input_does_not_raise(self):
        buf = io.BytesIO(b"this is not an xlsx file")
        buf.name = "garbage.xlsx"
        df, err = parse_amazon_listings(buf)
        assert df is None
        assert err is not None

    def test_missing_template_sheet_returns_error(self):
        # Build a file with a non-Template sheet
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            pd.DataFrame({"col": [1, 2]}).to_excel(
                writer, sheet_name="WrongSheet", index=False
            )
        buf.seek(0)
        buf.name = "no_template.xlsx"
        df, err = parse_amazon_listings(buf)
        assert df is None
        assert err is not None
        assert "template" in err.lower()


# =============================================================================
# parse_all_listings() — orchestrator
# =============================================================================

@pytest.mark.unit
@pytest.mark.parsers
class TestParseAllListings:
    """Orchestrator dispatches to per-marketplace parsers and concatenates."""

    def test_all_three_marketplaces(self):
        # Flipkart row
        flipkart_rows = [{
            "Product Title": "Cap", "Seller SKU Id": "CAP-A",
            "Sub-category": "C", "Listing ID": "LST-A", "Listing Status": "ACTIVE",
            "MRP": 100, "Your Selling Price": 80,
            "Package Length": 10, "Package Breadth": 10,
            "Package Height": 2, "Package Weight": 0.05,
            "Harmonized System Nomenclature": "1234", "Tax Code": "GST_5",
        }]
        # Meesho row
        meesho_rows = [{
            "SERIAL NO": 1, "CATALOG NAME": "Cat", "CATALOG ID": 1,
            "PRODUCT NAME": "Test", "PRODUCT ID": 999, "STYLE ID": "CAP-A",
            "VARIATION ID": 1, "VARIATION": "Free Size",
            "STOCK": "ALL", "SYSTEM STOCK COUNT": 100, "YOUR STOCK COUNT": "",
        }]
        # Amazon row
        amazon_rows = [{
            "Status": "Active", "Title": "X", "SKU": "CAP-A",
            "Product Type": "HAT", "Listing Action": "",
            "Item Name": "Test Cap", "Brand Name": "Brand",
            "Product Id": "B001", "Product Id Type": "ASIN",
            "Item Type Name": "cap",
            "Your Price INR (Sell on Amazon, IN)": 90,
            "Item Package Length": 10, "Package Length Unit": "centimeters",
            "Item Package Width": 10, "Package Width Unit": "centimeters",
            "Item Package Height": 2, "Package Height Unit": "centimeters",
            "Package Weight": 0.05, "Package Weight Unit": "kilograms",
            "Country of Origin": "IN",
        }]

        files = {
            'flipkart': _make_flipkart_xlsx(flipkart_rows),
            'meesho': _make_meesho_xlsx(meesho_rows),
            'amazon': _make_amazon_xlsx(amazon_rows),
        }
        df, errors = parse_all_listings(files)

        assert errors == [] or all("warning" in str(e).lower() or e for e in errors)
        assert len(df) == 3
        assert set(df["marketplace"]) == {"Amazon", "Flipkart", "Meesho"}
        # Same SKU on all three marketplaces (cross-marketplace match)
        assert (df["internal_sku"] == "CAP-A").all()

    def test_partial_files_only(self):
        # Only Flipkart provided — Amazon/Meesho None
        flipkart_rows = [{
            "Product Title": "Cap", "Seller SKU Id": "CAP-A",
            "Sub-category": "C", "Listing ID": "LST-A", "Listing Status": "ACTIVE",
            "MRP": 100, "Your Selling Price": 80,
            "Package Length": 10, "Package Breadth": 10,
            "Package Height": 2, "Package Weight": 0.05,
            "Harmonized System Nomenclature": "1234", "Tax Code": "GST_5",
        }]
        files = {
            'flipkart': _make_flipkart_xlsx(flipkart_rows),
            'meesho': None,
            'amazon': None,
        }
        df, errors = parse_all_listings(files)
        assert len(df) == 1
        assert df.iloc[0]["marketplace"] == "Flipkart"

    def test_no_files_returns_empty(self):
        files = {'amazon': None, 'flipkart': None, 'meesho': None}
        df, errors = parse_all_listings(files)
        assert df.empty
        assert list(df.columns) == UNIFIED_COLUMNS

    def test_unknown_marketplace_logged_as_error(self):
        # Per the orchestrator pattern in inventory_service.py [9], unknown
        # marketplace keys should be logged as warnings/errors but not crash
        files = {'unknown_marketplace': io.BytesIO(b"x")}
        df, errors = parse_all_listings(files)
        assert df.empty
        assert len(errors) > 0
        # Error message should reference the unknown marketplace
        assert any("unknown" in err.lower() for err in errors)

    def test_cross_marketplace_sku_match(self):
        """
        Critical test: same SKU on Flipkart + Meesho must produce 2 staging
        rows with the SAME internal_sku — this is what enables auto-grouping
        in OnboardingService.auto_group().
        """
        flipkart_rows = [{
            "Product Title": "Cap Grey", "Seller SKU Id": "CAP-TRUCKER-PLAIN-GREY",
            "Sub-category": "C", "Listing ID": "LST-FK", "Listing Status": "ACTIVE",
            "MRP": 499, "Your Selling Price": 257,
            "Package Length": 20, "Package Breadth": 20,
            "Package Height": 4, "Package Weight": 0.1,
            "Harmonized System Nomenclature": "6505", "Tax Code": "GST_5",
        }]
        meesho_rows = [{
            "SERIAL NO": 1, "CATALOG NAME": "X", "CATALOG ID": 1,
            "PRODUCT NAME": "Classic Grey Net Cap",
            "PRODUCT ID": 939302749,
            "STYLE ID": "CAP-TRUCKER-PLAIN-GREY",
            "VARIATION ID": 1, "VARIATION": "Free Size",
            "STOCK": "ALL", "SYSTEM STOCK COUNT": 100, "YOUR STOCK COUNT": "",
        }]
        files = {
            'flipkart': _make_flipkart_xlsx(flipkart_rows),
            'meesho': _make_meesho_xlsx(meesho_rows),
            'amazon': None,
        }
        df, _ = parse_all_listings(files)
        # Both rows must share the same internal_sku
        sku_groups = df.groupby('internal_sku').size()
        assert sku_groups['CAP-TRUCKER-PLAIN-GREY'] == 2

    def test_sku_normalization_consistent_across_files(self):
        """
        Different casing / whitespace in different files must normalize to
        the same internal_sku via clean_sku() [15]. This is critical for
        auto-grouping to work correctly.
        """
        flipkart_rows = [{
            "Product Title": "X", "Seller SKU Id": "  cap-trucker-plain-grey  ",
            "Sub-category": "C", "Listing ID": "LST-FK", "Listing Status": "ACTIVE",
            "MRP": 100, "Your Selling Price": 80,
            "Package Length": 10, "Package Breadth": 10,
            "Package Height": 2, "Package Weight": 0.05,
            "Harmonized System Nomenclature": "1234", "Tax Code": "GST_5",
        }]
        meesho_rows = [{
            "SERIAL NO": 1, "CATALOG NAME": "X", "CATALOG ID": 1,
            "PRODUCT NAME": "Test", "PRODUCT ID": 1,
            "STYLE ID": "CAP-TRUCKER-PLAIN-GREY",  # already uppercase
            "VARIATION ID": 1, "VARIATION": "Free Size",
            "STOCK": "ALL", "SYSTEM STOCK COUNT": 100, "YOUR STOCK COUNT": "",
        }]
        files = {
            'flipkart': _make_flipkart_xlsx(flipkart_rows),
            'meesho': _make_meesho_xlsx(meesho_rows),
            'amazon': None,
        }
        df, _ = parse_all_listings(files)
        # All rows should normalize to identical SKU
        assert df['internal_sku'].nunique() == 1
        assert df['internal_sku'].iloc[0] == "CAP-TRUCKER-PLAIN-GREY"

    def test_unified_schema_preserved_across_concat(self):
        """The orchestrator's concat must preserve UNIFIED_COLUMNS order."""
        flipkart_rows = [{
            "Product Title": "X", "Seller SKU Id": "SKU1",
            "Sub-category": "C", "Listing ID": "L1", "Listing Status": "ACTIVE",
            "MRP": 100, "Your Selling Price": 80,
            "Package Length": 10, "Package Breadth": 10,
            "Package Height": 2, "Package Weight": 0.05,
            "Harmonized System Nomenclature": "1234", "Tax Code": "GST_5",
        }]
        files = {
            'flipkart': _make_flipkart_xlsx(flipkart_rows),
            'meesho': None,
            'amazon': None,
        }
        df, _ = parse_all_listings(files)
        assert list(df.columns) == UNIFIED_COLUMNS


# =============================================================================
# CONTRACT TESTS — parser return contract must match parsers.py [15]
# =============================================================================

@pytest.mark.unit
@pytest.mark.parsers
class TestParserContract:
    """
    Each parser must return tuple (df_or_none, error_or_none).
    Mirrors the contract enforced for sales parsers in test_parsers.py [29].
    """

    def test_flipkart_success_contract(self):
        rows = [{
            "Product Title": "X", "Seller SKU Id": "SKU1",
            "Sub-category": "C", "Listing ID": "L1", "Listing Status": "ACTIVE",
            "MRP": 100, "Your Selling Price": 80,
            "Package Length": 10, "Package Breadth": 10,
            "Package Height": 2, "Package Weight": 0.05,
            "Harmonized System Nomenclature": "1234", "Tax Code": "GST_5",
        }]
        df, err = parse_flipkart_listings(_make_flipkart_xlsx(rows))
        assert isinstance(df, pd.DataFrame)
        assert err is None

    def test_flipkart_failure_contract(self):
        df, err = parse_flipkart_listings(io.BytesIO(b"garbage"))
        assert df is None
        assert isinstance(err, str)
        assert len(err) > 0

    def test_meesho_success_contract(self):
        rows = [{
            "SERIAL NO": 1, "CATALOG NAME": "X", "CATALOG ID": 1,
            "PRODUCT NAME": "Test", "PRODUCT ID": 1, "STYLE ID": "SKU1",
            "VARIATION ID": 1, "VARIATION": "Free Size",
            "STOCK": "ALL", "SYSTEM STOCK COUNT": 100, "YOUR STOCK COUNT": "",
        }]
        df, err = parse_meesho_listings(_make_meesho_xlsx(rows))
        assert isinstance(df, pd.DataFrame)
        assert err is None

    def test_meesho_failure_contract(self):
        df, err = parse_meesho_listings(io.BytesIO(b"garbage"))
        assert df is None
        assert isinstance(err, str)
        assert len(err) > 0

    def test_amazon_success_contract(self):
        rows = [{
            "Status": "Active", "Title": "X", "SKU": "SKU1",
            "Product Type": "HAT", "Listing Action": "",
            "Item Name": "Test", "Brand Name": "B",
            "Product Id": "B001", "Product Id Type": "ASIN",
            "Item Type Name": "cap",
            "Your Price INR (Sell on Amazon, IN)": 100,
            "Item Package Length": 10, "Package Length Unit": "centimeters",
            "Item Package Width": 10, "Package Width Unit": "centimeters",
            "Item Package Height": 2, "Package Height Unit": "centimeters",
            "Package Weight": 0.05, "Package Weight Unit": "kilograms",
            "Country of Origin": "IN",
        }]
        df, err = parse_amazon_listings(_make_amazon_xlsx(rows))
        assert isinstance(df, pd.DataFrame)
        assert err is None

    def test_amazon_failure_contract(self):
        df, err = parse_amazon_listings(io.BytesIO(b"garbage"))
        assert df is None
        assert isinstance(err, str)
        assert len(err) > 0

    def test_no_parser_raises_uncaught_exception(self):
        """
        Defensive test: even with completely malformed data, none of the
        parsers should raise — they must always return (None, error_str).
        """
        bad_inputs = [
            io.BytesIO(b""),                # empty bytes
            io.BytesIO(b"\x00\x01\x02"),    # binary garbage
            io.BytesIO(b"<html>not excel</html>"),  # wrong format
        ]
        for buf in bad_inputs:
            buf.name = "bad.xlsx"
            for parser in [parse_flipkart_listings,
                           parse_meesho_listings,
                           parse_amazon_listings]:
                # Must not raise
                df, err = parser(buf)
                assert df is None
                assert err is not None
                buf.seek(0)  # reset for next parser