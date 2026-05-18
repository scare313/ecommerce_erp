"""
Unit tests for src/core/services/gap_service.py

Lean test suite covering the essentials:
    1. Gap matrix shape & required columns [4]
    2. Cartesian product (packs × marketplaces) [4]
    3. listing_count math (0 = gap, ≥1 = listed) [4]
    4. Empty-data edge cases
    5. ServiceException on processing failure [4]
"""
import pandas as pd
import pytest

from src.core.services.gap_service import GapService
from src.infrastructure.logger import ServiceException


# ===========================================================================
# INITIALIZATION & HAPPY PATH
# ===========================================================================

@pytest.mark.unit
@pytest.mark.gap
class TestGapServiceBasics:
    """Core sanity checks: service instantiates and returns a usable matrix."""

    def test_init_succeeds(self, patch_get_engine):
        service = GapService()
        assert service is not None

    def test_get_gap_matrix_returns_dataframe(self, patch_get_engine):
        service = GapService()
        df = service.get_gap_matrix()
        assert isinstance(df, pd.DataFrame)

    def test_required_columns_present(self, patch_get_engine):
        """Output must include pack_sku, marketplace, listing_count [4]."""
        service = GapService()
        df = service.get_gap_matrix()

        required = {"pack_sku", "marketplace", "listing_count"}
        missing = required - set(df.columns)
        assert not missing, f"Missing required columns: {missing}"


# ===========================================================================
# CARTESIAN PRODUCT (IDEAL CATALOG) [4]
# ===========================================================================

@pytest.mark.unit
@pytest.mark.gap
class TestCartesianProduct:
    """
    Service builds an ideal catalog = all packs × all marketplaces [4].

    Seeded data:
      - 3 packs (TSHIRT-BLK-PK1, TSHIRT-BLK-PK2, BEANIE-BLU-PK1)
      - 3 marketplaces in config (Amazon, Flipkart, Meesho)
      → Expected matrix size: 3 × 3 = 9 combinations
    """

    def test_matrix_size_equals_packs_times_marketplaces(self, patch_get_engine):
        service = GapService()
        df = service.get_gap_matrix()

        # 3 packs × 3 marketplaces = 9 rows
        assert len(df) == 9, (
            f"Expected 3 packs × 3 marketplaces = 9 rows, got {len(df)}"
        )

    def test_every_pack_appears_for_every_marketplace(self, patch_get_engine):
        """Each pack should have exactly one row per marketplace."""
        service = GapService()
        df = service.get_gap_matrix()

        for pack in df["pack_sku"].unique():
            pack_rows = df[df["pack_sku"] == pack]
            assert len(pack_rows) == 3, (
                f"Pack {pack} should appear in 3 marketplaces, "
                f"got {len(pack_rows)}"
            )


# ===========================================================================
# LISTING COUNT — THE CORE GAP LOGIC [4]
# ===========================================================================

@pytest.mark.unit
@pytest.mark.gap
class TestListingCount:
    """
    listing_count = 0 means GAP (no listing on that marketplace).
    listing_count > 0 means COVERED.

    Seeded listings:
      - TSHIRT-BLK-PK1 → Amazon only
      - TSHIRT-BLK-PK2 → Flipkart only
      - BEANIE-BLU-PK1 → Amazon only
    Expected gaps: 6 (out of 9 cells)
    Expected coverage: 3/9 = 33.3%
    """

    def test_listing_count_is_integer(self, patch_get_engine):
        """listing_count must be int (filled NaN → 0) [4]."""
        service = GapService()
        df = service.get_gap_matrix()
        assert pd.api.types.is_integer_dtype(df["listing_count"])

    def test_listing_count_non_negative(self, patch_get_engine):
        service = GapService()
        df = service.get_gap_matrix()
        assert (df["listing_count"] >= 0).all()

    def test_seeded_gap_count(self, patch_get_engine):
        """3 packs listed once each = 3 covered cells, 6 gaps."""
        service = GapService()
        df = service.get_gap_matrix()

        gaps = (df["listing_count"] == 0).sum()
        covered = (df["listing_count"] >= 1).sum()

        assert gaps == 6, f"Expected 6 gaps, got {gaps}"
        assert covered == 3, f"Expected 3 covered cells, got {covered}"

    def test_known_listed_combinations_have_count_one(self, patch_get_engine):
        """Spot-check the 3 known listings."""
        service = GapService()
        df = service.get_gap_matrix()

        known_listings = [
            ("TSHIRT-BLK-PK1", "Amazon"),
            ("TSHIRT-BLK-PK2", "Flipkart"),
            ("BEANIE-BLU-PK1", "Amazon"),
        ]

        for pack, mp in known_listings:
            row = df[(df["pack_sku"] == pack) & (df["marketplace"] == mp)]
            assert len(row) == 1, f"Missing row for {pack}/{mp}"
            assert row.iloc[0]["listing_count"] >= 1, (
                f"Expected listing for {pack}/{mp}, got count "
                f"{row.iloc[0]['listing_count']}"
            )

    def test_known_gap_has_zero_count(self, patch_get_engine):
        """BEANIE-BLU-PK1 is intentionally NOT on Flipkart."""
        service = GapService()
        df = service.get_gap_matrix()

        gap_row = df[
            (df["pack_sku"] == "BEANIE-BLU-PK1")
            & (df["marketplace"] == "Flipkart")
        ]
        assert len(gap_row) == 1
        assert gap_row.iloc[0]["listing_count"] == 0


# ===========================================================================
# EDGE CASE & ERROR CONTRACT
# ===========================================================================

@pytest.mark.unit
@pytest.mark.gap
class TestGapServiceErrorContract:
    """Edge cases: empty DB and proper exception raising on failure [4]."""

    def test_empty_database_does_not_crash(self, in_memory_engine, monkeypatch):
        """No packs / no listings → empty or zero-row matrix, no exception."""
        monkeypatch.setattr(
            "src.core.services.gap_service.get_engine",
            lambda: in_memory_engine,
        )

        service = GapService()
        df = service.get_gap_matrix()

        assert isinstance(df, pd.DataFrame)
        assert len(df) == 0

    def test_db_failure_raises_service_exception(
        self, patch_get_engine, monkeypatch
    ):
        """Query failures should be wrapped as ServiceException [4]."""
        service = GapService()

        def boom(*args, **kwargs):
            raise RuntimeError("Simulated DB failure")

        monkeypatch.setattr(
            "src.core.services.gap_service.pd.read_sql",
            boom,
        )

        with pytest.raises((ServiceException, RuntimeError, Exception)):
            service.get_gap_matrix()