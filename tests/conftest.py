"""
Shared pytest fixtures for the Ecommerce ERP test suite.

Provides:
    - In-memory SQLite engine seeded with the production schema
    - Sample DataFrames for product_master, pack_master, channel_listings,
      pricing_rules, shipping_rules, and config tables
    - Helper fixtures for file-buffer-based parser tests

All fixtures use function scope so each test gets a fresh, isolated database.
"""
import io
import sys
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import create_engine, text

# ---------------------------------------------------------------------------
# Make `src.*` importable when tests run from the project root
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ===========================================================================
# DATABASE FIXTURES
# ===========================================================================

@pytest.fixture(scope="function")
def in_memory_engine():
    """
    Create a fresh in-memory SQLite engine for each test.

    Schema mirrors src/infrastructure/schema.sql for the tables exercised by
    finance_service, gap_service, and parsers.
    """
    engine = create_engine("sqlite:///:memory:")

    schema_statements = [
        # ---- product_master (matches schema.sql) ----
        """
        CREATE TABLE product_master (
            sku VARCHAR(50) PRIMARY KEY,
            name VARCHAR(255),
            category VARCHAR(100),
            brand VARCHAR(100),
            lifecycle_status VARCHAR(50),
            supplier VARCHAR(100),
            supplier_code VARCHAR(100),
            mfg_cost DECIMAL(10,2) DEFAULT 0,
            packaging_cost DECIMAL(10,2) DEFAULT 0,
            labeling_labor DECIMAL(10,2) DEFAULT 0,
            inbound_transport DECIMAL(10,2) DEFAULT 0,
            total_unit_cogs DECIMAL(10,2) DEFAULT 0,
            hsn VARCHAR(20),
            gst_rate DECIMAL(5,2),
            mrp DECIMAL(10,2),
            godown_stock_packs INT DEFAULT 0,
            shop_stock_pieces INT DEFAULT 0
        )
        """,
        # ---- pack_master ----
        """
        CREATE TABLE pack_master (
            pack_sku VARCHAR(50) PRIMARY KEY,
            master_sku VARCHAR(50),
            quantity INT DEFAULT 1,
            packaging_cogs DECIMAL(10,2) DEFAULT 0,
            final_l_cm DECIMAL(10,2),
            final_w_cm DECIMAL(10,2),
            final_h_cm DECIMAL(10,2),
            final_wt_kg DECIMAL(10,3)
        )
        """,
        # ---- channel_listings ----
        """
        CREATE TABLE channel_listings (
            channel_sku VARCHAR(100),
            marketplace VARCHAR(50),
            internal_sku VARCHAR(50),
            listing_status VARCHAR(50),
            selling_price DECIMAL(10,2) DEFAULT 0.0,
            channel_id VARCHAR(50),
            listing_url TEXT,
            last_updated VARCHAR(50),
            comment TEXT,
            PRIMARY KEY (channel_sku, marketplace)
        )
        """,
        # ---- pricing_rules ----
        """
        CREATE TABLE pricing_rules (
            marketplace VARCHAR(50),
            category_ref VARCHAR(100),
            min_price DECIMAL(10,2),
            max_price DECIMAL(10,2),
            referral_fee_pct DECIMAL(5,4),
            closing_fee_inr DECIMAL(10,2)
        )
        """,
        # ---- shipping_rules ----
        """
        CREATE TABLE shipping_rules (
            marketplace VARCHAR(50),
            weight_slab_max_kg DECIMAL(5,3),
            local_fee DECIMAL(10,2),
            regional_fee DECIMAL(10,2),
            national_fee DECIMAL(10,2)
        )
        """,
        # ---- config ----
        """
        CREATE TABLE config (
            marketplace VARCHAR(50) PRIMARY KEY,
            default_zone VARCHAR(50),
            volumetric_divisor INT,
            gst_on_fees DECIMAL(5,2)
        )
        """,
    ]

    with engine.connect() as conn:
        for stmt in schema_statements:
            conn.execute(text(stmt))
        conn.commit()

    yield engine
    engine.dispose()


@pytest.fixture(scope="function")
def seeded_engine(in_memory_engine):
    """
    In-memory engine pre-populated with realistic sample data covering:
      - 2 master products (different categories)
      - 3 packs (1 single + 2 multi-pack variants)
      - 3 channel listings across Amazon + Flipkart
        (intentional gap: BEANIE-BLU-PK1 is missing from Flipkart)
      - Pricing rules for typical price bands
      - Shipping rules for multiple weight slabs
      - Config rows for Amazon, Flipkart, Meesho
    """
    engine = in_memory_engine

    # ---- product_master ----
    products = pd.DataFrame([
        {
            "sku": "TSHIRT-BLK", "name": "Black T-Shirt",
            "category": "Apparel", "brand": "BrandX",
            "lifecycle_status": "Active",
            "supplier": "SupplierA", "supplier_code": "SA-01",
            "mfg_cost": 100, "packaging_cost": 10,
            "labeling_labor": 5, "inbound_transport": 5,
            "total_unit_cogs": 120,
            "hsn": "6109", "gst_rate": 5.0, "mrp": 999,
            "godown_stock_packs": 50, "shop_stock_pieces": 0,
        },
        {
            "sku": "BEANIE-BLU", "name": "Blue Beanie",
            "category": "Accessories", "brand": "BrandY",
            "lifecycle_status": "Active",
            "supplier": "SupplierB", "supplier_code": "SB-02",
            "mfg_cost": 40, "packaging_cost": 5,
            "labeling_labor": 2, "inbound_transport": 3,
            "total_unit_cogs": 50,
            "hsn": "6505", "gst_rate": 12.0, "mrp": 499,
            "godown_stock_packs": 30, "shop_stock_pieces": 0,
        },
    ])
    products.to_sql("product_master", engine, if_exists="append", index=False)

    # ---- pack_master ----
    packs = pd.DataFrame([
        {
            "pack_sku": "TSHIRT-BLK-PK1", "master_sku": "TSHIRT-BLK",
            "quantity": 1, "packaging_cogs": 0,
            "final_l_cm": 25, "final_w_cm": 20, "final_h_cm": 3,
            "final_wt_kg": 0.300,
        },
        {
            "pack_sku": "TSHIRT-BLK-PK2", "master_sku": "TSHIRT-BLK",
            "quantity": 2, "packaging_cogs": 5,
            "final_l_cm": 30, "final_w_cm": 25, "final_h_cm": 5,
            "final_wt_kg": 0.600,
        },
        {
            "pack_sku": "BEANIE-BLU-PK1", "master_sku": "BEANIE-BLU",
            "quantity": 1, "packaging_cogs": 0,
            "final_l_cm": 15, "final_w_cm": 12, "final_h_cm": 2,
            "final_wt_kg": 0.100,
        },
    ])
    packs.to_sql("pack_master", engine, if_exists="append", index=False)

    # ---- channel_listings (BEANIE-BLU-PK1 deliberately absent from Flipkart) ----
    listings = pd.DataFrame([
        {
            "channel_sku": "AMZ-TSHIRT-BLK-1", "marketplace": "Amazon",
            "internal_sku": "TSHIRT-BLK-PK1", "listing_status": "LIVE",
            "selling_price": 499.0, "channel_id": "B001",
            "listing_url": "", "last_updated": "2025-01-01", "comment": "",
        },
        {
            "channel_sku": "FK-TSHIRT-BLK-2", "marketplace": "Flipkart",
            "internal_sku": "TSHIRT-BLK-PK2", "listing_status": "LIVE",
            "selling_price": 899.0, "channel_id": "F001",
            "listing_url": "", "last_updated": "2025-01-01", "comment": "",
        },
        {
            "channel_sku": "AMZ-BEANIE-BLU-1", "marketplace": "Amazon",
            "internal_sku": "BEANIE-BLU-PK1", "listing_status": "LIVE",
            "selling_price": 249.0, "channel_id": "B002",
            "listing_url": "", "last_updated": "2025-01-01", "comment": "",
        },
    ])
    listings.to_sql("channel_listings", engine, if_exists="append", index=False)

    # ---- pricing_rules ----
    pricing = pd.DataFrame([
        # Amazon Apparel – two price bands
        {"marketplace": "Amazon", "category_ref": "Apparel",
         "min_price": 0, "max_price": 500,
         "referral_fee_pct": 0.17, "closing_fee_inr": 25},
        {"marketplace": "Amazon", "category_ref": "Apparel",
         "min_price": 500.01, "max_price": 1000,
         "referral_fee_pct": 0.17, "closing_fee_inr": 35},
        # Amazon Accessories
        {"marketplace": "Amazon", "category_ref": "Accessories",
         "min_price": 0, "max_price": 500,
         "referral_fee_pct": 0.15, "closing_fee_inr": 20},
        # Flipkart Apparel
        {"marketplace": "Flipkart", "category_ref": "Apparel",
         "min_price": 0, "max_price": 1000,
         "referral_fee_pct": 0.18, "closing_fee_inr": 30},
    ])
    pricing.to_sql("pricing_rules", engine, if_exists="append", index=False)

    # ---- shipping_rules ----
    shipping = pd.DataFrame([
        # Amazon weight slabs
                # Amazon weight slabs
        {"marketplace": "Amazon", "weight_slab_max_kg": 0.5,
         "local_fee": 35, "regional_fee": 45, "national_fee": 70},
        {"marketplace": "Amazon", "weight_slab_max_kg": 1.0,
         "local_fee": 50, "regional_fee": 65, "national_fee": 95},
        {"marketplace": "Amazon", "weight_slab_max_kg": 2.0,
         "local_fee": 75, "regional_fee": 95, "national_fee": 140},
        # Flipkart weight slabs
        {"marketplace": "Flipkart", "weight_slab_max_kg": 0.5,
         "local_fee": 30, "regional_fee": 40, "national_fee": 65},
        {"marketplace": "Flipkart", "weight_slab_max_kg": 1.0,
         "local_fee": 45, "regional_fee": 60, "national_fee": 90},
    ])
    shipping.to_sql("shipping_rules", engine, if_exists="append", index=False)

    # ---- config ----
    config = pd.DataFrame([
        {"marketplace": "Amazon", "default_zone": "regional",
         "volumetric_divisor": 5000, "gst_on_fees": 0.18},
        {"marketplace": "Flipkart", "default_zone": "regional",
         "volumetric_divisor": 5000, "gst_on_fees": 0.18},
        {"marketplace": "Meesho", "default_zone": "national",
         "volumetric_divisor": 5000, "gst_on_fees": 0.18},
    ])
    config.to_sql("config", engine, if_exists="append", index=False)

    return engine


@pytest.fixture(scope="function")
def patch_get_engine(seeded_engine, monkeypatch):
    """
    Redirect `get_engine()` calls inside the application to the in-memory
    seeded engine. This lets us test FinanceService / GapService /
    CatalogService without touching the real SQLite file on disk [6].

    Usage in a test:
        def test_something(patch_get_engine):
            service = FinanceService()      # will use seeded in-memory DB
            ...
    """
    # Patch every module that imports get_engine directly
    targets = [
        "src.infrastructure.database.get_engine",
        "src.core.services.finance_service.get_engine",
        "src.core.services.gap_service.get_engine",
        "src.core.services.catalog_service.get_engine",
        "src.core.data_access.get_engine",
    ]
    for target in targets:
        try:
            monkeypatch.setattr(target, lambda: seeded_engine)
        except (AttributeError, ModuleNotFoundError):
            # Module may not import get_engine at top level; skip silently
            pass

    return seeded_engine


# ===========================================================================
# DATAFRAME FIXTURES (for unit tests that don't need a DB)
# ===========================================================================

@pytest.fixture
def sample_pricing_rules_df():
    """Standalone pricing rules DataFrame for unit-testing fee calculations."""
    return pd.DataFrame([
        {"marketplace": "Amazon", "category_ref": "Apparel",
         "min_price": 0, "max_price": 500,
         "referral_fee_pct": 0.17, "closing_fee_inr": 25},
        {"marketplace": "Amazon", "category_ref": "Apparel",
         "min_price": 500.01, "max_price": 1000,
         "referral_fee_pct": 0.17, "closing_fee_inr": 35},
        {"marketplace": "Flipkart", "category_ref": "Apparel",
         "min_price": 0, "max_price": 1000,
         "referral_fee_pct": 0.18, "closing_fee_inr": 30},
    ])


@pytest.fixture
def sample_shipping_rules_df():
    """Standalone shipping rules DataFrame for unit-testing shipping fee logic [3]."""
    return pd.DataFrame([
        {"marketplace": "Amazon", "weight_slab_max_kg": 0.5,
         "local_fee": 35, "regional_fee": 45, "national_fee": 70},
        {"marketplace": "Amazon", "weight_slab_max_kg": 1.0,
         "local_fee": 50, "regional_fee": 65, "national_fee": 95},
        {"marketplace": "Amazon", "weight_slab_max_kg": 2.0,
         "local_fee": 75, "regional_fee": 95, "national_fee": 140},
    ])


@pytest.fixture
def sample_listings_df():
    """
    Sample joined listings DataFrame as it would appear after the SQL join
    in FinanceService.calculate_profitability() [3].
    """
    return pd.DataFrame([
        {
            "channel_sku": "AMZ-TSHIRT-BLK-1",
            "marketplace": "Amazon",
            "internal_sku": "TSHIRT-BLK-PK1",
            "selling_price": 499.0,
            "category": "Apparel",
            "total_unit_cogs": 120.0,
            "packaging_cogs": 0.0,
            "quantity": 1,
            "final_l_cm": 25.0, "final_w_cm": 20.0, "final_h_cm": 3.0,
            "final_wt_kg": 0.300,
            "default_zone": "regional",
            "volumetric_divisor": 5000,
            "gst_on_fees": 0.18,
        },
        {
            "channel_sku": "AMZ-BEANIE-BLU-1",
            "marketplace": "Amazon",
            "internal_sku": "BEANIE-BLU-PK1",
            "selling_price": 249.0,
            "category": "Accessories",
            "total_unit_cogs": 50.0,
            "packaging_cogs": 0.0,
            "quantity": 1,
            "final_l_cm": 15.0, "final_w_cm": 12.0, "final_h_cm": 2.0,
            "final_wt_kg": 0.100,
            "default_zone": "regional",
            "volumetric_divisor": 5000,
            "gst_on_fees": 0.18,
        },
    ])


# ===========================================================================
# FILE-BUFFER FIXTURES (for parsers tests) [10]
# ===========================================================================

@pytest.fixture
def amazon_sales_buffer():
    """In-memory Amazon Business Report CSV buffer."""
    csv_content = (
        "SKU,Units Ordered,(Child) ASIN\n"
        "TSHIRT-BLK-PK1,5,B001\n"
        "tshirt-blk-pk2,3,B002\n"
        "  BEANIE-BLU-PK1  ,2,B003\n"
    )
    return io.BytesIO(csv_content.encode("utf-8"))


@pytest.fixture
def amazon_sales_buffer_alt_header():
    """Amazon CSV where real headers live on row 2 (alternate format) [10]."""
    csv_content = (
        "Report generated on 2025-01-01\n"
        "SKU,Units Ordered,(Child) ASIN\n"
        "TSHIRT-BLK-PK1,5,B001\n"
    )
    return io.BytesIO(csv_content.encode("utf-8"))


@pytest.fixture
def amazon_sales_buffer_missing_sku():
    """Amazon CSV missing the SKU column — should produce an error."""
    csv_content = (
        "Product,Units Ordered\n"
        "Black T-Shirt,5\n"
    )
    return io.BytesIO(csv_content.encode("utf-8"))


@pytest.fixture
def flipkart_sales_buffer():
    """In-memory Flipkart orders CSV buffer."""
    csv_content = (
        "SKU,Quantity\n"
        "TSHIRT-BLK-PK2,4\n"
        "BEANIE-BLU-PK1,1\n"
    )
    return io.BytesIO(csv_content.encode("utf-8"))


@pytest.fixture
def meesho_sales_buffer():
    """In-memory Meesho orders CSV buffer (lowercase, varied column names) [10]."""
    csv_content = (
        "supplier sku,quantity\n"
        "TSHIRT-BLK-PK1,2\n"
        "BEANIE-BLU-PK1,3\n"
    )
    return io.BytesIO(csv_content.encode("utf-8"))


@pytest.fixture
def stock_file_buffer():
    """In-memory stock file CSV buffer (SKU + Qty)."""
    csv_content = (
        "SKU,Qty\n"
        "TSHIRT-BLK,50\n"
        "BEANIE-BLU,30\n"
    )
    return io.BytesIO(csv_content.encode("utf-8"))


@pytest.fixture
def empty_csv_buffer():
    """Empty CSV — for testing graceful failure."""
    return io.BytesIO(b"")


# ===========================================================================
# UTILITY FIXTURES
# ===========================================================================

@pytest.fixture
def assert_close():
    """
    Helper to compare floats with tolerance.

    Usage:
        def test_x(assert_close):
            assert_close(result, 123.45, tol=0.01)
    """
    def _compare(actual, expected, tol=0.01):
        assert abs(float(actual) - float(expected)) < tol, (
            f"Expected {expected} ± {tol}, got {actual}"
        )
    return _compare