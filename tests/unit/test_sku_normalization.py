"""Unit tests for E11 — SKU case normalization.

Covers:
    S1: Write paths store canonical uppercase SKUs (catalog_service, inventory_service)
    S2: One-time migration normalizes existing rows and is idempotent
    S3: (Structural guard) No UPPER( in src/core/services source files
"""
import pytest
from sqlalchemy import create_engine, text


# ---------------------------------------------------------------------------
# Minimal in-memory DB with all tables needed for write-path tests
# ---------------------------------------------------------------------------

def _make_full_engine():
    """In-memory SQLite with product, pack, listing, inventory, ledger, and
    migration guard tables — sufficient for all E11 write and migration tests."""
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE schema_migrations (
                migration_name VARCHAR(100) PRIMARY KEY,
                applied_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """))
        conn.execute(text("""
            CREATE TABLE product_master (
                sku VARCHAR(50) PRIMARY KEY,
                name VARCHAR(255),
                category VARCHAR(100),
                brand VARCHAR(100),
                lifecycle_status VARCHAR(50),
                supplier VARCHAR(100),
                supplier_code VARCHAR(100),
                supplier_product_code VARCHAR(100),
                mfg_cost DECIMAL(10,2) DEFAULT 0,
                packaging_cost DECIMAL(10,2) DEFAULT 0,
                labeling_labor DECIMAL(10,2) DEFAULT 0,
                inbound_transport DECIMAL(10,2) DEFAULT 0,
                total_unit_cogs DECIMAL(10,2) DEFAULT 0,
                hsn VARCHAR(20),
                gst_rate DECIMAL(5,2),
                mrp DECIMAL(10,2)
            )
        """))
        conn.execute(text("""
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
        """))
        conn.execute(text("""
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
        """))
        conn.execute(text("""
            CREATE TABLE inventory_master (
                sku VARCHAR(50) PRIMARY KEY,
                godown_stock_packs INT DEFAULT 0,
                shop_stock_pieces INT DEFAULT 0,
                pack_multiplier INT DEFAULT 1,
                reorder_point INT DEFAULT 0,
                reorder_qty INT DEFAULT 0,
                last_updated DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """))
        conn.execute(text("""
            CREATE TABLE stock_ledger (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sku VARCHAR(50),
                transaction_type VARCHAR(20),
                packs INT,
                multiplier INT,
                total_pieces_affected INT,
                reason_code VARCHAR(50) DEFAULT 'ADJUSTMENT',
                reason TEXT,
                updated_by VARCHAR(100) DEFAULT 'system',
                running_balance INT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """))
    return engine


# ===========================================================================
# S1 — Write paths produce uppercase SKUs
# ===========================================================================

@pytest.mark.unit
class TestSkuWriteNormalization:
    """All service write methods must store SKUs in canonical uppercase."""

    def test_add_product_uppercases_sku(self, monkeypatch):
        engine = _make_full_engine()
        monkeypatch.setattr("src.core.services.catalog_service.get_engine", lambda: engine)
        from src.core.services.catalog_service import CatalogService

        CatalogService().add_product({"sku": "tshirt-blk", "name": "Tee", "mfg_cost": 0})

        with engine.connect() as conn:
            row = conn.execute(text("SELECT sku FROM product_master")).fetchone()
        assert row[0] == "TSHIRT-BLK"

    def test_add_product_strips_whitespace_from_sku(self, monkeypatch):
        engine = _make_full_engine()
        monkeypatch.setattr("src.core.services.catalog_service.get_engine", lambda: engine)
        from src.core.services.catalog_service import CatalogService

        CatalogService().add_product({"sku": "  Widget-01  ", "name": "Widget", "mfg_cost": 0})

        with engine.connect() as conn:
            row = conn.execute(text("SELECT sku FROM product_master")).fetchone()
        assert row[0] == "WIDGET-01"

    def test_add_pack_uppercases_pack_and_master_sku(self, monkeypatch):
        engine = _make_full_engine()
        monkeypatch.setattr("src.core.services.catalog_service.get_engine", lambda: engine)
        from src.core.services.catalog_service import CatalogService

        with engine.begin() as conn:
            conn.execute(text("INSERT INTO product_master (sku, name) VALUES ('TSHIRT-BLK', 'Tee')"))

        CatalogService().add_pack({
            "pack_sku": "tshirt-blk-pk1",
            "master_sku": "tshirt-blk",
            "quantity": 1,
        })

        with engine.connect() as conn:
            row = conn.execute(text("SELECT pack_sku, master_sku FROM pack_master")).fetchone()
        assert row[0] == "TSHIRT-BLK-PK1"
        assert row[1] == "TSHIRT-BLK"

    def test_add_listing_uppercases_channel_and_internal_sku(self, monkeypatch):
        engine = _make_full_engine()
        monkeypatch.setattr("src.core.services.catalog_service.get_engine", lambda: engine)
        from src.core.services.catalog_service import CatalogService

        with engine.begin() as conn:
            conn.execute(text("INSERT INTO product_master (sku, name) VALUES ('TSHIRT-BLK', 'Tee')"))
            conn.execute(text(
                "INSERT INTO pack_master (pack_sku, master_sku) "
                "VALUES ('TSHIRT-BLK-PK1', 'TSHIRT-BLK')"
            ))

        CatalogService().add_listing({
            "channel_sku": "amz-tshirt-blk-1",
            "marketplace": "Amazon",
            "internal_sku": "tshirt-blk-pk1",
            "selling_price": 499.0,
        })

        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT channel_sku, internal_sku FROM channel_listings")
            ).fetchone()
        assert row[0] == "AMZ-TSHIRT-BLK-1"
        assert row[1] == "TSHIRT-BLK-PK1"

    def test_inventory_add_stock_uppercases_sku(self, monkeypatch):
        engine = _make_full_engine()
        monkeypatch.setattr("src.core.services.inventory_service.get_engine", lambda: engine)
        from src.core.services.inventory_service import InventoryService

        InventoryService().add_stock("tshirt-blk", 5, "GODOWN", "PACK", "test")

        with engine.connect() as conn:
            im_row = conn.execute(text("SELECT sku FROM inventory_master")).fetchone()
            sl_row = conn.execute(text("SELECT sku FROM stock_ledger")).fetchone()
        assert im_row[0] == "TSHIRT-BLK"
        assert sl_row[0] == "TSHIRT-BLK"

    def test_inventory_manage_godown_stock_uppercases_sku(self, monkeypatch):
        engine = _make_full_engine()
        monkeypatch.setattr("src.core.services.inventory_service.get_engine", lambda: engine)
        from src.core.services.inventory_service import InventoryService

        InventoryService().manage_godown_stock(
            "beanie-blu", 3, 1, "ADD", reason="test", updated_by="user"
        )

        with engine.connect() as conn:
            row = conn.execute(text("SELECT sku FROM inventory_master")).fetchone()
        assert row[0] == "BEANIE-BLU"


# ===========================================================================
# S2 — One-time normalization migration
# ===========================================================================

@pytest.mark.unit
class TestSkuNormalizationMigration:
    """_normalize_sku_case upgrades stored rows, guards idempotency, catches collisions."""

    def test_migration_uppercases_all_sku_columns(self):
        engine = _make_full_engine()
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO product_master (sku, name) VALUES ('tshirt-blk', 'Tee')"))
            conn.execute(text(
                "INSERT INTO pack_master (pack_sku, master_sku) "
                "VALUES ('tshirt-blk-pk1', 'tshirt-blk')"
            ))
            conn.execute(text(
                "INSERT INTO channel_listings (channel_sku, marketplace, internal_sku) "
                "VALUES ('amz-001', 'Amazon', 'tshirt-blk-pk1')"
            ))
            conn.execute(text("INSERT INTO inventory_master (sku) VALUES ('tshirt-blk')"))
            conn.execute(text(
                "INSERT INTO stock_ledger "
                "(sku, transaction_type, packs, multiplier, total_pieces_affected) "
                "VALUES ('tshirt-blk', 'ADD', 1, 1, 1)"
            ))

        from src.infrastructure.init_db import _normalize_sku_case
        _normalize_sku_case(engine)

        with engine.connect() as conn:
            pm  = conn.execute(text("SELECT sku FROM product_master")).fetchone()
            pkm = conn.execute(text("SELECT pack_sku, master_sku FROM pack_master")).fetchone()
            cl  = conn.execute(text("SELECT channel_sku, internal_sku FROM channel_listings")).fetchone()
            im  = conn.execute(text("SELECT sku FROM inventory_master")).fetchone()
            sl  = conn.execute(text("SELECT sku FROM stock_ledger")).fetchone()

        assert pm[0] == "TSHIRT-BLK"
        assert pkm[0] == "TSHIRT-BLK-PK1"
        assert pkm[1] == "TSHIRT-BLK"
        assert cl[0] == "AMZ-001"
        assert cl[1] == "TSHIRT-BLK-PK1"
        assert im[0] == "TSHIRT-BLK"
        assert sl[0] == "TSHIRT-BLK"

    def test_migration_is_idempotent(self):
        engine = _make_full_engine()
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO product_master (sku, name) VALUES ('WIDGET-01', 'Widget')"))

        from src.infrastructure.init_db import _normalize_sku_case
        _normalize_sku_case(engine)
        _normalize_sku_case(engine)  # second call is a no-op via guard

        with engine.connect() as conn:
            count = conn.execute(
                text("SELECT COUNT(*) FROM schema_migrations "
                     "WHERE migration_name = 'normalize_sku_case_v1'")
            ).scalar()
        assert count == 1

    def test_migration_skips_on_product_master_case_collision(self):
        """If two rows would UPPER() to the same SKU, migration must not apply."""
        engine = _make_full_engine()
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO product_master (sku, name) VALUES ('widget-01', 'Widget A')"))
            conn.execute(text("INSERT INTO product_master (sku, name) VALUES ('WIDGET-01', 'Widget B')"))

        from src.infrastructure.init_db import _normalize_sku_case
        _normalize_sku_case(engine)  # should not raise; should skip

        with engine.connect() as conn:
            # Guard row must NOT be inserted — migration was skipped
            count = conn.execute(
                text("SELECT COUNT(*) FROM schema_migrations "
                     "WHERE migration_name = 'normalize_sku_case_v1'")
            ).scalar()
            # Data must be unchanged
            skus = {r[0] for r in conn.execute(text("SELECT sku FROM product_master")).fetchall()}
        assert count == 0
        assert skus == {"widget-01", "WIDGET-01"}

    def test_migration_sets_guard_row_in_schema_migrations(self):
        engine = _make_full_engine()

        from src.infrastructure.init_db import _normalize_sku_case
        _normalize_sku_case(engine)

        with engine.connect() as conn:
            count = conn.execute(
                text("SELECT COUNT(*) FROM schema_migrations "
                     "WHERE migration_name = 'normalize_sku_case_v1'")
            ).scalar()
        assert count == 1


# ===========================================================================
# S3 — Structural guard: no UPPER( in src/core/services
# ===========================================================================

@pytest.mark.unit
class TestNoUpperInServiceLayer:
    """Confirm no UPPER() predicate remains in the service layer source (E11-S3)."""

    def test_no_upper_in_inventory_service(self):
        import inspect
        from src.core.services import inventory_service
        source = inspect.getsource(inventory_service)
        # UPPER() in SQL predicates — exclude legitimate .str.upper() Python calls
        # by checking for the SQL form UPPER(
        sql_upper_uses = [
            line.strip() for line in source.splitlines()
            if "UPPER(" in line and not line.strip().startswith("#")
        ]
        assert sql_upper_uses == [], (
            f"inventory_service still contains UPPER() SQL calls: {sql_upper_uses}"
        )

    def test_no_upper_in_supplier_service(self):
        import inspect
        from src.core.services import supplier_service
        source = inspect.getsource(supplier_service)
        sql_upper_uses = [
            line.strip() for line in source.splitlines()
            if "UPPER(" in line and not line.strip().startswith("#")
        ]
        assert sql_upper_uses == [], (
            f"supplier_service still contains UPPER() SQL calls: {sql_upper_uses}"
        )
