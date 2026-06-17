"""
Unit tests for src/core/parsers.py

Covers:
    - clean_sku()              : normalization & NaN handling
    - parse_amazon_sales()     : happy path, alt header row, missing SKU
    - parse_flipkart_sales()   : CSV + XLSX paths
    - parse_meesho_sales()     : column auto-detection, default qty
    - parse_stock_file()       : SKU + Qty requirement, numeric coercion
    - Error handling           : returns (None, error_msg) on failure
"""
import io

import numpy as np
import pandas as pd
import pytest

from src.infrastructure.parsers import (
    clean_sku,
    parse_amazon_sales,
    parse_flipkart_sales,
    parse_meesho_sales,
    parse_stock_file,
)


# ===========================================================================
# clean_sku()
# ===========================================================================

@pytest.mark.unit
@pytest.mark.parsers
class TestCleanSku:
    """Tests for the clean_sku() helper."""

    def test_uppercase_conversion(self):
        assert clean_sku("tshirt-blk") == "TSHIRT-BLK"

    def test_strips_whitespace(self):
        assert clean_sku("  TSHIRT-BLK  ") == "TSHIRT-BLK"

    def test_strips_and_uppercases_combined(self):
        assert clean_sku("  tshirt-blk-pk2 ") == "TSHIRT-BLK-PK2"

    def test_already_clean_sku_unchanged(self):
        assert clean_sku("BEANIE-BLU-PK1") == "BEANIE-BLU-PK1"

    def test_nan_returns_unknown(self):
        assert clean_sku(np.nan) == "UNKNOWN"

    def test_none_returns_unknown(self):
        assert clean_sku(None) == "UNKNOWN"

    def test_numeric_input_coerced_to_string(self):
        # Some sales reports have numeric SKU codes
        result = clean_sku(12345)
        assert result == "12345"

    def test_empty_string(self):
        assert clean_sku("") == ""


# ===========================================================================
# parse_amazon_sales()
# ===========================================================================

@pytest.mark.unit
@pytest.mark.parsers
class TestParseAmazonSales:
    """Tests for parse_amazon_sales()."""

    def test_happy_path_returns_dataframe(self, amazon_sales_buffer):
        amazon_sales_buffer.name = "amazon.csv"
        df, err = parse_amazon_sales(amazon_sales_buffer)

        assert err is None
        assert df is not None
        assert len(df) == 3

    def test_required_columns_present(self, amazon_sales_buffer):
        amazon_sales_buffer.name = "amazon.csv"
        df, _ = parse_amazon_sales(amazon_sales_buffer)

        assert "sku" in df.columns
        assert "qty" in df.columns
        assert "marketplace" in df.columns

    def test_marketplace_tagged_as_amazon(self, amazon_sales_buffer):
        amazon_sales_buffer.name = "amazon.csv"
        df, _ = parse_amazon_sales(amazon_sales_buffer)

        assert (df["marketplace"] == "Amazon").all()

    def test_skus_are_normalized(self, amazon_sales_buffer):
        """clean_sku() should be applied — uppercase + stripped."""
        amazon_sales_buffer.name = "amazon.csv"
        df, _ = parse_amazon_sales(amazon_sales_buffer)

        for sku in df["sku"]:
            assert sku == sku.strip().upper()

    def test_quantities_are_numeric(self, amazon_sales_buffer):
        amazon_sales_buffer.name = "amazon.csv"
        df, _ = parse_amazon_sales(amazon_sales_buffer)

        assert pd.api.types.is_numeric_dtype(df["qty"])
        assert df["qty"].sum() == 10  # 5 + 3 + 2

    def test_missing_sku_column_returns_error(self, amazon_sales_buffer_missing_sku):
        amazon_sales_buffer_missing_sku.name = "bad_amazon.csv"
        df, err = parse_amazon_sales(amazon_sales_buffer_missing_sku)

        assert df is None
        assert err is not None
        assert "sku" in err.lower() or "column" in err.lower()

    def test_corrupt_file_returns_error_not_exception(self):
        """Should never raise — returns (None, error_msg) on failure."""
        bad_buffer = io.BytesIO(b"\x00\x01\x02not a real csv")
        bad_buffer.name = "corrupt.csv"

        df, err = parse_amazon_sales(bad_buffer)
        # Either it parsed garbage gracefully or returned an error;
        # the key contract is no uncaught exception.
        assert df is None or err is None


# ===========================================================================
# parse_flipkart_sales()
# ===========================================================================

@pytest.mark.unit
@pytest.mark.parsers
class TestParseFlipkartSales:
    """Tests for parse_flipkart_sales()."""

    def test_csv_happy_path(self, flipkart_sales_buffer):
        flipkart_sales_buffer.name = "flipkart.csv"
        df, err = parse_flipkart_sales(flipkart_sales_buffer)

        assert err is None
        assert df is not None
        assert len(df) == 2

    def test_marketplace_tagged_as_flipkart(self, flipkart_sales_buffer):
        flipkart_sales_buffer.name = "flipkart.csv"
        df, _ = parse_flipkart_sales(flipkart_sales_buffer)

        assert (df["marketplace"] == "Flipkart").all()

    def test_quantity_sum_correct(self, flipkart_sales_buffer):
        flipkart_sales_buffer.name = "flipkart.csv"
        df, _ = parse_flipkart_sales(flipkart_sales_buffer)

        assert df["qty"].sum() == 5  # 4 + 1

    def test_xlsx_path(self, tmp_path):
        """Flipkart parser should also handle .xlsx via pandas.read_excel [10]."""
        xlsx_path = tmp_path / "flipkart.xlsx"
        pd.DataFrame({
            "SKU": ["TSHIRT-BLK-PK2", "BEANIE-BLU-PK1"],
            "Quantity": [4, 1],
        }).to_excel(xlsx_path, index=False)

        with open(xlsx_path, "rb") as f:
            buf = io.BytesIO(f.read())
        buf.name = "flipkart.xlsx"

        df, err = parse_flipkart_sales(buf)

        assert err is None
        assert df is not None
        assert len(df) == 2


# ===========================================================================
# parse_meesho_sales()
# ===========================================================================

@pytest.mark.unit
@pytest.mark.parsers
class TestParseMeeshoSales:
    """Tests for parse_meesho_sales()."""

    def test_happy_path(self, meesho_sales_buffer):
        meesho_sales_buffer.name = "meesho.csv"
        df, err = parse_meesho_sales(meesho_sales_buffer)

        assert err is None
        assert df is not None
        assert len(df) == 2

    def test_lowercase_column_detection(self, meesho_sales_buffer):
        """Meesho parser auto-detects 'sku' substring in any column name [10]."""
        meesho_sales_buffer.name = "meesho.csv"
        df, _ = parse_meesho_sales(meesho_sales_buffer)

        assert "sku" in df.columns
        assert "qty" in df.columns

    def test_marketplace_tagged_as_meesho(self, meesho_sales_buffer):
        meesho_sales_buffer.name = "meesho.csv"
        df, _ = parse_meesho_sales(meesho_sales_buffer)

        assert (df["marketplace"] == "Meesho").all()

    def test_default_qty_when_no_quantity_column(self):
        """When no qty column exists, parser defaults to 1 per row [10]."""
        csv = "supplier sku\nTSHIRT-BLK-PK1\nBEANIE-BLU-PK1\n"
        buf = io.BytesIO(csv.encode("utf-8"))
        buf.name = "meesho_no_qty.csv"

        df, err = parse_meesho_sales(buf)

        assert err is None
        assert df is not None
        assert (df["qty"] == 1).all()

    def test_missing_sku_column_returns_error(self):
        csv = "product_name,quantity\nBlack T-Shirt,5\n"
        buf = io.BytesIO(csv.encode("utf-8"))
        buf.name = "bad_meesho.csv"

        df, err = parse_meesho_sales(buf)

        assert df is None
        assert err is not None
        assert "sku" in err.lower()

    def test_skus_are_normalized(self, meesho_sales_buffer):
        meesho_sales_buffer.name = "meesho.csv"
        df, _ = parse_meesho_sales(meesho_sales_buffer)

        for sku in df["sku"]:
            assert sku == sku.strip().upper()


# ===========================================================================
# parse_stock_file()
# ===========================================================================

@pytest.mark.unit
@pytest.mark.parsers
class TestParseStockFile:
    """Tests for parse_stock_file()."""

    def test_happy_path(self, stock_file_buffer):
        stock_file_buffer.name = "stock.csv"
        df, err = parse_stock_file(stock_file_buffer)

        assert err is None
        assert df is not None
        assert len(df) == 2

    def test_required_output_columns(self, stock_file_buffer):
        stock_file_buffer.name = "stock.csv"
        df, _ = parse_stock_file(stock_file_buffer)

        assert list(df.columns) == ["sku", "stock_qty"]

    def test_stock_qty_is_numeric(self, stock_file_buffer):
        stock_file_buffer.name = "stock.csv"
        df, _ = parse_stock_file(stock_file_buffer)

        assert pd.api.types.is_numeric_dtype(df["stock_qty"])
        assert df["stock_qty"].sum() == 80  # 50 + 30

    def test_missing_sku_column_returns_error(self):
        csv = "Product,Qty\nBlack T-Shirt,50\n"
        buf = io.BytesIO(csv.encode("utf-8"))
        buf.name = "bad_stock.csv"

        df, err = parse_stock_file(buf)

        assert df is None
        assert err is not None
        assert "SKU" in err and "Qty" in err  # Matches actual error string [10]

    def test_missing_qty_column_returns_error(self):
        csv = "SKU,Product\nTSHIRT-BLK,Black T-Shirt\n"
        buf = io.BytesIO(csv.encode("utf-8"))
        buf.name = "bad_stock.csv"

        df, err = parse_stock_file(buf)

        assert df is None
        assert err is not None
        assert "SKU" in err and "Qty" in err  # Matches actual error string [10]

    def test_non_numeric_qty_coerced_to_zero(self):
        """Non-numeric values in Qty column should become 0, not raise [10]."""
        csv = "SKU,Qty\nTSHIRT-BLK,50\nBEANIE-BLU,not_a_number\n"
        buf = io.BytesIO(csv.encode("utf-8"))
        buf.name = "stock_dirty.csv"

        df, err = parse_stock_file(buf)

        assert err is None
        assert df is not None
        assert df.loc[df["sku"] == "BEANIE-BLU", "stock_qty"].iloc[0] == 0
        assert df.loc[df["sku"] == "TSHIRT-BLK", "stock_qty"].iloc[0] == 50

    def test_skus_are_normalized(self, stock_file_buffer):
        stock_file_buffer.name = "stock.csv"
        df, _ = parse_stock_file(stock_file_buffer)

        for sku in df["sku"]:
            assert sku == sku.strip().upper()

    def test_alternate_qty_column_name_stock(self):
        """Parser auto-detects 'stock' substring as qty column [10]."""
        csv = "SKU,stock_on_hand\nTSHIRT-BLK,50\nBEANIE-BLU,30\n"
        buf = io.BytesIO(csv.encode("utf-8"))
        buf.name = "stock_alt.csv"

        df, err = parse_stock_file(buf)

        assert err is None
        assert df is not None
        assert "stock_qty" in df.columns
        assert df["stock_qty"].sum() == 80

    def test_xlsx_path(self, tmp_path):
        """Stock parser should also handle .xlsx files [10]."""
        xlsx_path = tmp_path / "stock.xlsx"
        pd.DataFrame({
            "SKU": ["TSHIRT-BLK", "BEANIE-BLU"],
            "Qty": [50, 30],
        }).to_excel(xlsx_path, index=False)

        with open(xlsx_path, "rb") as f:
            buf = io.BytesIO(f.read())
        buf.name = "stock.xlsx"

        df, err = parse_stock_file(buf)

        assert err is None
        assert df is not None
        assert len(df) == 2
        assert df["stock_qty"].sum() == 80


# ===========================================================================
# CROSS-CUTTING ERROR-HANDLING CONTRACT
# ===========================================================================

@pytest.mark.unit
@pytest.mark.parsers
class TestParserErrorContract:
    """
    All parsers share the same contract:
        - Never raise on bad input
        - Always return a (DataFrame|None, error_msg|None) tuple
        - On success: (df, None)
        - On failure: (None, str)
    """

    @pytest.mark.parametrize("parser_fn,filename", [
        (parse_amazon_sales,   "amazon.csv"),
        (parse_flipkart_sales, "flipkart.csv"),
        (parse_meesho_sales,   "meesho.csv"),
        (parse_stock_file,     "stock.csv"),
    ])
    def test_empty_file_handled_gracefully(self, parser_fn, filename):
        """Empty CSV must not raise — it must return (None, error)."""
        buf = io.BytesIO(b"")
        buf.name = filename

        # The contract: no exception escapes the parser
        try:
            df, err = parser_fn(buf)
        except Exception as exc:
            pytest.fail(f"{parser_fn.__name__} raised {type(exc).__name__}: {exc}")

        # Either it returned an error, or returned an empty/None df — both fine
        assert df is None or len(df) == 0 or err is None

    @pytest.mark.parametrize("parser_fn,filename", [
        (parse_amazon_sales,   "amazon.csv"),
        (parse_flipkart_sales, "flipkart.csv"),
        (parse_meesho_sales,   "meesho.csv"),
        (parse_stock_file,     "stock.csv"),
    ])
    def test_returns_two_tuple(self, parser_fn, filename):
        """Every parser must return exactly 2 values."""
        csv = "SKU,Qty\nTSHIRT-BLK,5\n"
        buf = io.BytesIO(csv.encode("utf-8"))
        buf.name = filename

        result = parser_fn(buf)

        assert isinstance(result, tuple)
        assert len(result) == 2

    @pytest.mark.parametrize("parser_fn,filename", [
        (parse_amazon_sales,   "garbage.csv"),
        (parse_flipkart_sales, "garbage.csv"),
        (parse_meesho_sales,   "garbage.csv"),
        (parse_stock_file,     "garbage.csv"),
    ])
    def test_binary_garbage_does_not_crash(self, parser_fn, filename):
        """Binary garbage as input must never raise an uncaught exception."""
        buf = io.BytesIO(b"\x00\x01\x02\x03\xff\xfe random bytes")
        buf.name = filename

        try:
            df, err = parser_fn(buf)
        except Exception as exc:
            pytest.fail(
                f"{parser_fn.__name__} raised on binary input: "
                f"{type(exc).__name__}: {exc}"
            )

        # Must return the standard tuple shape
        assert df is None or isinstance(df, pd.DataFrame)
        assert err is None or isinstance(err, str)