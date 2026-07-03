"""
Unit tests for src/core/data_access.py

Covers what matters for Roadmap E7 (decouple cache/core from Streamlit):
    1. The module is genuinely importable with no Streamlit dependency
       (headless-safe — usable from a script, cron job, or test).
    2. get_marketplaces() preserves its SQL-config / Excel-fallback /
       hardcoded-default behavior exactly, since that logic moved here
       verbatim from the old src/core/cache.py.
    3. get_inventory_balances() runs a real query and returns the
       expected shape.
"""
import sys

import pandas as pd
import pytest

from src.core import data_access


@pytest.mark.unit
class TestHeadlessImport:
    """Proves the core layer no longer depends on Streamlit (E7's goal).

    This static source scan is the reliable in-suite guard: pytest's own
    process already has streamlit loaded (other test files import
    streamlit-backed modules), so a dynamic sys.modules check here would be
    order-dependent and unreliable. The real dynamic proof — importing
    data_access in a fresh subprocess and confirming streamlit never enters
    sys.modules — was run manually during implementation (see the Sprint 4
    self-review in the project history) and is what actually matters at
    runtime; this test guards against someone re-adding the import later.
    """

    def test_no_streamlit_import_statement_in_source(self):
        import inspect
        source = inspect.getsource(data_access)
        assert "streamlit" not in source, (
            "src/core/data_access.py must stay framework-free"
        )


@pytest.mark.unit
class TestGetMarketplaces:
    """Value-parity checks for the fallback chain moved from cache.py."""

    def test_uses_sql_config_when_populated(self, patch_get_engine):
        """seeded_engine's config table has Amazon/Flipkart/Meesho rows."""
        result = data_access.get_marketplaces()
        assert result == ["Amazon", "Flipkart", "Meesho"]

    def test_falls_back_to_defaults_when_config_table_missing(self, monkeypatch):
        """No config table at all (query raises) -> hardcoded default, not a crash.

        Uses a bare engine rather than the in_memory_engine fixture, which
        always creates an (empty) config table — this test needs the table
        to be genuinely absent to exercise the except-and-fall-through path.
        """
        from sqlalchemy import create_engine
        bare_engine = create_engine("sqlite:///:memory:")
        monkeypatch.setattr("src.core.data_access.get_engine", lambda: bare_engine)
        result = data_access.get_marketplaces()
        assert result == ["Amazon", "Flipkart", "Meesho"]

    def test_falls_back_to_defaults_when_config_table_empty(self, in_memory_engine, monkeypatch):
        """config table exists (in_memory_engine creates it) but has zero
        rows, unlike seeded_engine -> still falls back to hardcoded default."""
        monkeypatch.setattr("src.core.data_access.get_engine", lambda: in_memory_engine)
        result = data_access.get_marketplaces()
        assert result == ["Amazon", "Flipkart", "Meesho"]


@pytest.mark.unit
class TestRulesMeta:
    """Tests for get_rules_last_verified / set_rules_last_verified (E9)."""

    def test_returns_none_when_table_empty(self, in_memory_engine, monkeypatch):
        """rules_meta table exists but has no rows -> None, not an error."""
        monkeypatch.setattr("src.core.data_access.get_engine", lambda: in_memory_engine)
        result = data_access.get_rules_last_verified()
        assert result is None

    def test_set_and_get_round_trip(self, in_memory_engine, monkeypatch):
        """set_rules_last_verified() writes an ISO datetime; get reads it back."""
        monkeypatch.setattr("src.core.data_access.get_engine", lambda: in_memory_engine)
        data_access.set_rules_last_verified()
        result = data_access.get_rules_last_verified()
        assert result is not None
        from datetime import datetime
        parsed = datetime.fromisoformat(result)
        assert parsed.year >= 2026

    def test_set_is_idempotent(self, in_memory_engine, monkeypatch):
        """Calling set_rules_last_verified() twice replaces, not appends."""
        monkeypatch.setattr("src.core.data_access.get_engine", lambda: in_memory_engine)
        data_access.set_rules_last_verified()
        data_access.set_rules_last_verified()
        # Only one row should exist
        from sqlalchemy import text
        with in_memory_engine.connect() as conn:
            count = conn.execute(
                text("SELECT COUNT(*) FROM rules_meta WHERE key = 'rules_last_verified'")
            ).scalar()
        assert count == 1


@pytest.mark.unit
class TestGetInventoryBalances:
    """Sanity check the moved SQL still returns the right shape."""

    def test_returns_dataframe_with_expected_columns(self, in_memory_engine, monkeypatch):
        from sqlalchemy import text
        with in_memory_engine.connect() as conn:
            conn.execute(text("""
                CREATE TABLE inventory_master (
                    sku VARCHAR(50) PRIMARY KEY,
                    godown_stock_packs INT DEFAULT 0,
                    shop_stock_pieces INT DEFAULT 0,
                    pack_multiplier INT DEFAULT 1,
                    last_updated VARCHAR(50)
                )
            """))
            conn.execute(text(
                "INSERT INTO inventory_master VALUES "
                "('TSHIRT-BLK-PK1', 10, 5, 1, '2026-01-01 00:00:00')"
            ))
            conn.commit()
        monkeypatch.setattr("src.core.data_access.get_engine", lambda: in_memory_engine)

        df = data_access.get_inventory_balances()

        assert isinstance(df, pd.DataFrame)
        assert list(df.columns) == [
            "sku", "name", "godown_stock_packs", "shop_stock_pieces",
            "pack_multiplier", "last_updated",
        ]
        assert len(df) == 1
        assert df.iloc[0]["sku"] == "TSHIRT-BLK-PK1"
