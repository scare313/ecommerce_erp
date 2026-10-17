"""Tests for database safety fixes and two-account catalog import.

Covers:
    - Startup repair of a table whose primary key was lost to a
      pandas to_sql(if_exists='replace') save
    - Startup never wipes data when the database cannot be opened/upgraded
    - Catalog grid saves keep the table schema and refuse unsafe edits
    - Onboarding commit stores account + fulfilment and refuses to overwrite
      another account's listing
"""
import pandas as pd
import pytest
from sqlalchemy import create_engine, text

from src.infrastructure import init_db
from src.infrastructure.logger import DataValidationException


def _pk_columns(engine, table):
    with engine.connect() as conn:
        return [r[1] for r in conn.execute(text(f"PRAGMA table_info({table})")) if r[5]]


@pytest.fixture
def schema_engine(tmp_path):
    """File-backed DB built from the real schema.sql plus startup migrations."""
    engine = create_engine(f"sqlite:///{tmp_path / 'erp.db'}")
    with open(init_db.SCHEMA_PATH, encoding="utf-8") as f:
        statements = [s for s in f.read().split(";") if s.strip()]
    with engine.connect() as conn:
        for stmt in statements:
            conn.execute(text(stmt))
        conn.commit()
    init_db._apply_table_migrations(engine)
    init_db._apply_column_migrations(engine)
    init_db._apply_account_schema(engine)
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO seller_accounts (account_id, display_name) "
            "VALUES ('account1', 'Account 1'), ('account2', 'Account 2')"
        ))
        conn.execute(text(
            "INSERT INTO product_master (sku, name, total_unit_cogs) VALUES "
            "('CAP-BLACK', 'Black cap', 60), ('CAP-WHITE', 'White cap', 60)"
        ))
        conn.execute(text(
            "INSERT INTO pack_master (pack_sku, master_sku) VALUES "
            "('CAP-BLACK', 'CAP-BLACK'), ('CAP-WHITE', 'CAP-WHITE')"
        ))
        conn.execute(text(
            "INSERT INTO channel_listings (channel_sku, marketplace, internal_sku, "
            "selling_price, account_id, fulfilment_type) VALUES "
            "('CAP-BLACK', 'Amazon', 'CAP-BLACK', 299, 'account1', 'easy_ship')"
        ))
        conn.execute(text("INSERT INTO inventory_master (sku) VALUES ('CAP-BLACK')"))
    return engine


def _break_product_master(engine):
    """Reproduce what to_sql(if_exists='replace') did to the live database."""
    with engine.connect() as conn:
        df = pd.read_sql("SELECT * FROM product_master", conn)
    df["legacy_note"] = "kept"  # a column schema.sql does not know about
    with engine.connect() as conn:
        # The live app ran with foreign-key checks off, which is why the
        # drop-and-recreate went through unnoticed.
        conn.exec_driver_sql("PRAGMA foreign_keys = OFF")
        df.to_sql("product_master", conn, if_exists="replace", index=False)
        conn.commit()


# ===========================================================================
# Primary-key repair
# ===========================================================================

@pytest.mark.unit
class TestPrimaryKeyRepair:

    def test_replace_save_really_loses_the_key(self, schema_engine):
        _break_product_master(schema_engine)
        assert _pk_columns(schema_engine, "product_master") == []

    def test_repair_restores_key_and_keeps_every_row_and_column(self, schema_engine):
        _break_product_master(schema_engine)

        init_db._repair_missing_primary_keys(schema_engine)

        assert _pk_columns(schema_engine, "product_master") == ["sku"]
        with schema_engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT sku, name, total_unit_cogs, legacy_note FROM product_master ORDER BY sku"
            )).fetchall()
        assert [tuple(r) for r in rows] == [
            ("CAP-BLACK", "Black cap", 60, "kept"),
            ("CAP-WHITE", "White cap", 60, "kept"),
        ]

    def test_upsert_works_after_repair(self, schema_engine):
        _break_product_master(schema_engine)
        init_db._repair_missing_primary_keys(schema_engine)

        with schema_engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO product_master (sku, name) VALUES ('CAP-BLACK', 'Renamed') "
                "ON CONFLICT(sku) DO UPDATE SET name = excluded.name"
            ))
            count = conn.execute(text("SELECT COUNT(*) FROM product_master")).scalar()
        assert count == 2

    def test_packs_can_be_added_with_foreign_keys_on_after_repair(self, schema_engine):
        _break_product_master(schema_engine)
        init_db._repair_missing_primary_keys(schema_engine)

        with schema_engine.begin() as conn:
            conn.execute(text("PRAGMA foreign_keys = ON"))
            conn.execute(text(
                "INSERT INTO pack_master (pack_sku, master_sku) VALUES ('CAP-BLACK-PK2', 'CAP-BLACK')"
            ))

    def test_duplicates_block_repair_and_leave_data_untouched(self, schema_engine):
        _break_product_master(schema_engine)
        with schema_engine.begin() as conn:
            conn.execute(text("INSERT INTO product_master (sku, name) VALUES ('CAP-BLACK', 'Dup')"))

        init_db._repair_missing_primary_keys(schema_engine)

        assert _pk_columns(schema_engine, "product_master") == []
        with schema_engine.connect() as conn:
            assert conn.execute(text("SELECT COUNT(*) FROM product_master")).scalar() == 3

    def test_healthy_database_is_not_touched(self, schema_engine):
        with schema_engine.connect() as conn:
            before = conn.execute(text("SELECT sql FROM sqlite_master WHERE type='table'")).fetchall()

        init_db._repair_missing_primary_keys(schema_engine)

        with schema_engine.connect() as conn:
            after = conn.execute(text("SELECT sql FROM sqlite_master WHERE type='table'")).fetchall()
        assert before == after


# ===========================================================================
# Startup never wipes data on error
# ===========================================================================

@pytest.mark.unit
class TestStartupNeverWipes:

    def test_error_during_upgrade_returns_false_without_reimport(self, schema_engine, monkeypatch):
        calls = []
        monkeypatch.setattr(init_db, "get_engine", lambda: schema_engine)
        monkeypatch.setattr(init_db, "run_migration", lambda: calls.append("wipe"))

        def _disk_error(engine):
            raise RuntimeError("disk I/O error")
        monkeypatch.setattr(init_db, "_apply_table_migrations", _disk_error)

        assert init_db._check_and_migrate_existing_db() is False
        assert calls == []
        with schema_engine.connect() as conn:
            assert conn.execute(text("SELECT COUNT(*) FROM product_master")).scalar() == 2
            assert conn.execute(text("SELECT COUNT(*) FROM inventory_master")).scalar() == 1


# ===========================================================================
# Catalog grid save
# ===========================================================================

@pytest.mark.unit
class TestSaveTableEdits:

    @pytest.fixture
    def service(self, schema_engine, monkeypatch):
        monkeypatch.setattr("src.core.services.catalog_service.get_engine", lambda: schema_engine)
        from src.core.services.catalog_service import CatalogService
        return CatalogService()

    def _grid(self, engine, table):
        with engine.connect() as conn:
            return pd.read_sql(f"SELECT * FROM {table}", conn)

    def test_save_keeps_primary_key_and_defaults(self, service, schema_engine):
        df = self._grid(schema_engine, "channel_listings")
        df.loc[0, "selling_price"] = 349

        service.save_table_edits("channel_listings", df)

        assert _pk_columns(schema_engine, "channel_listings") == ["channel_sku", "marketplace"]
        with schema_engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO channel_listings (channel_sku, marketplace) VALUES ('NEW', 'Meesho')"
            ))
            row = conn.execute(text(
                "SELECT account_id, fulfilment_type FROM channel_listings WHERE channel_sku='NEW'"
            )).fetchone()
            price = conn.execute(text(
                "SELECT selling_price FROM channel_listings WHERE channel_sku='CAP-BLACK'"
            )).scalar()
        assert tuple(row) == ("account1", "easy_ship")
        assert price == 349

    def test_sku_is_uppercased(self, service, schema_engine):
        df = self._grid(schema_engine, "product_master")
        df.loc[len(df)] = {"sku": " cap-navy ", "name": "Navy cap"}

        service.save_table_edits("product_master", df)

        with schema_engine.connect() as conn:
            skus = {r[0] for r in conn.execute(text("SELECT sku FROM product_master"))}
        assert "CAP-NAVY" in skus

    def test_blank_key_is_refused(self, service, schema_engine):
        df = self._grid(schema_engine, "product_master")
        df.loc[len(df)] = {"sku": None, "name": "No SKU"}
        with pytest.raises(DataValidationException, match="no sku"):
            service.save_table_edits("product_master", df)

    def test_duplicate_key_is_refused(self, service, schema_engine):
        df = self._grid(schema_engine, "product_master")
        df.loc[len(df)] = {"sku": "cap-black", "name": "Again"}
        with pytest.raises(DataValidationException, match="Duplicate"):
            service.save_table_edits("product_master", df)

    def test_removing_a_product_still_used_is_refused(self, service, schema_engine):
        df = self._grid(schema_engine, "product_master")
        df = df[df["sku"] != "CAP-BLACK"]
        with pytest.raises(DataValidationException, match="still used"):
            service.save_table_edits("product_master", df)
        with schema_engine.connect() as conn:
            assert conn.execute(text("SELECT COUNT(*) FROM product_master")).scalar() == 2

    def test_emptying_the_table_is_refused(self, service, schema_engine):
        df = self._grid(schema_engine, "channel_listings").iloc[0:0]
        with pytest.raises(DataValidationException, match="Every row"):
            service.save_table_edits("channel_listings", df)


# ===========================================================================
# Two-account import
# ===========================================================================

def _staging(rows):
    return pd.DataFrame([
        {"channel_sku": sku, "marketplace": mkt, "internal_sku": sku,
         "selling_price": 299.0, "listing_status": "ACTIVE", "channel_id": ""}
        for sku, mkt in rows
    ])


def _grouped(skus):
    return pd.DataFrame([
        {"internal_sku": s, "name": s, "category": None, "brand": None, "hsn": None,
         "gst_rate": 5.0, "mrp": None, "length_cm": None, "width_cm": None,
         "height_cm": None, "weight_kg": None}
        for s in skus
    ])


@pytest.mark.unit
class TestTwoAccountImport:

    @pytest.fixture
    def service(self, schema_engine, monkeypatch):
        monkeypatch.setattr("src.core.services.onboarding_service.get_engine", lambda: schema_engine)
        from src.core.services.onboarding_service import OnboardingService
        return OnboardingService()

    def test_listings_saved_under_chosen_account_and_fulfilment(self, service, schema_engine):
        staging = _staging([("L-CAP-BLACK", "Amazon"), ("L-CAP-BLACK", "Flipkart")])

        service.commit_catalog(staging, _grouped(["L-CAP-BLACK"]),
                               account_id="account2", amazon_fulfilment="fba")

        with schema_engine.connect() as conn:
            rows = dict(conn.execute(text(
                "SELECT marketplace, account_id || '/' || fulfilment_type "
                "FROM channel_listings WHERE channel_sku = 'L-CAP-BLACK'"
            )).fetchall())
        assert rows == {"Amazon": "account2/fba", "Flipkart": "account2/easy_ship"}

    def test_clash_with_other_account_saves_nothing(self, service, schema_engine):
        staging = _staging([("CAP-BLACK", "Amazon"), ("NEW-SKU", "Amazon")])

        with pytest.raises(DataValidationException, match="another seller account"):
            service.commit_catalog(staging, _grouped(["CAP-BLACK", "NEW-SKU"]),
                                   account_id="account2")

        with schema_engine.connect() as conn:
            owner = conn.execute(text(
                "SELECT account_id FROM channel_listings WHERE channel_sku='CAP-BLACK'"
            )).scalar()
            new = conn.execute(text("SELECT COUNT(*) FROM product_master WHERE sku='NEW-SKU'")).scalar()
        assert owner == "account1"
        assert new == 0

    def test_diff_reports_clashes(self, service):
        staging = _staging([("CAP-BLACK", "Amazon")])
        diff = service.compute_diff(staging, _grouped(["CAP-BLACK"]), account_id="account2")
        assert diff["account_clashes"] == [
            {"channel_sku": "CAP-BLACK", "marketplace": "Amazon", "owned_by": "account1"}
        ]

    def test_rerun_keeps_fulfilment_edited_in_catalog(self, service, schema_engine):
        with schema_engine.begin() as conn:
            conn.execute(text(
                "UPDATE channel_listings SET fulfilment_type='fba' WHERE channel_sku='CAP-BLACK'"
            ))
        staging = _staging([("CAP-BLACK", "Amazon")])

        service.commit_catalog(staging, _grouped(["CAP-BLACK"]),
                               account_id="account1", amazon_fulfilment="easy_ship")

        with schema_engine.connect() as conn:
            assert conn.execute(text(
                "SELECT fulfilment_type FROM channel_listings WHERE channel_sku='CAP-BLACK'"
            )).scalar() == "fba"

    def test_add_listing_refuses_other_accounts_listing(self, schema_engine, monkeypatch):
        monkeypatch.setattr("src.core.services.catalog_service.get_engine", lambda: schema_engine)
        from src.core.services.catalog_service import CatalogService

        with pytest.raises(DataValidationException, match="already belongs"):
            CatalogService().add_listing({
                "channel_sku": "cap-black", "marketplace": "Amazon",
                "internal_sku": "CAP-BLACK", "selling_price": 299,
                "account_id": "account2", "fulfilment_type": "fba",
            })
