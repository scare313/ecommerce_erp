"""Supplier master management service.

Handles supplier CRUD, deactivation, bulk reassignment, and dropdown population
for the Supplier Master UI and catalog validation.
"""
import pandas as pd
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from src.infrastructure.database import get_engine
from src.infrastructure.logger import get_logger, DataValidationException

logger = get_logger(__name__)


class SupplierService:
    """Service for managing supplier master data."""

    def __init__(self):
        try:
            self.engine = get_engine()
            logger.debug("SupplierService initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize SupplierService: {str(e)}", exc_info=True)
            raise

    # ── Read operations ────────────────────────────────────────────────────────

    def get_all_suppliers(self, active_only: bool = True) -> pd.DataFrame:
        """Return all supplier_master rows as a DataFrame.

        Args:
            active_only: When True (default), returns only is_active = 1 rows.

        Returns:
            DataFrame with columns: supplier_code, name, contact_name,
            contact_email, lead_time_days, payment_terms, notes,
            is_active, created_at, updated_at.
            Empty DataFrame if no rows match.
        """
        where = "WHERE is_active = 1" if active_only else ""
        sql = f"""
            SELECT supplier_code, name, contact_name, contact_email,
                   lead_time_days, payment_terms, notes,
                   is_active, created_at, updated_at
            FROM supplier_master
            {where}
            ORDER BY name
        """
        with self.engine.connect() as conn:
            return pd.read_sql(text(sql), conn)

    def get_supplier_dropdown(self) -> list:
        """Return active suppliers formatted for selectbox widgets.

        Returns:
            List of dicts: [{'supplier_code': str, 'label': 'CODE — Name'}, ...]
            Ordered by name. Empty list if no active suppliers exist.
        """
        with self.engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT supplier_code, name
                FROM supplier_master
                WHERE is_active = 1
                ORDER BY name
            """)).fetchall()
        return [
            {"supplier_code": r[0], "label": f"{r[0]} — {r[1]}"}
            for r in rows
        ]

    def get_supplier_by_code(self, supplier_code: str) -> dict | None:
        """Return a single supplier row as a dict, or None if not found.

        Args:
            supplier_code: Exact supplier_code PK value (case-sensitive match
                           after service-layer UPPER normalisation).

        Returns:
            Dict of all supplier_master columns, or None.
        """
        with self.engine.connect() as conn:
            row = conn.execute(
                text("""
                    SELECT supplier_code, name, contact_name, contact_email,
                           contact_phone, lead_time_days, payment_terms,
                           notes, is_active, created_at, updated_at
                    FROM supplier_master
                    WHERE supplier_code = :code
                """),
                {"code": supplier_code},
            ).fetchone()
        if row is None:
            return None
        return dict(row._mapping)

    def get_products_for_supplier(self, supplier_code: str) -> pd.DataFrame:
        """Return products assigned to a supplier.

        Args:
            supplier_code: Normalised supplier_code PK value.

        Returns:
            DataFrame with columns: sku, name, category.
            Empty DataFrame if no products are assigned.
        """
        with self.engine.connect() as conn:
            return pd.read_sql(
                text("""
                    SELECT sku, name, category
                    FROM product_master
                    WHERE UPPER(TRIM(supplier_code)) = :code
                    ORDER BY sku
                """),
                conn,
                params={"code": supplier_code.strip().upper()},
            )

    # ── Write operations ───────────────────────────────────────────────────────

    def add_supplier(self, data: dict) -> None:
        """Add a new supplier to supplier_master.

        Validates, normalises supplier_code to UPPER, then inserts.

        Args:
            data: Dict with keys: supplier_code (required), name (required),
                  contact_name, contact_email, contact_phone, lead_time_days,
                  payment_terms, notes.

        Raises:
            DataValidationException: On empty code/name, invalid lead_time_days,
                                     or duplicate supplier_code.
        """
        # Validation
        raw_code = str(data.get("supplier_code", "")).strip()
        if not raw_code:
            raise DataValidationException("Supplier code is required.")

        name = str(data.get("name", "")).strip()
        if not name:
            raise DataValidationException("Supplier name is required.")

        try:
            lead_time = int(data.get("lead_time_days", 10))
        except (TypeError, ValueError):
            raise DataValidationException("Lead time must be at least 1 day.")
        if lead_time < 1:
            raise DataValidationException("Lead time must be at least 1 day.")

        supplier_code = raw_code.upper()

        try:
            with self.engine.begin() as conn:
                conn.execute(
                    text("""
                        INSERT INTO supplier_master
                            (supplier_code, name, contact_name, contact_email,
                             contact_phone, lead_time_days, payment_terms, notes)
                        VALUES
                            (:supplier_code, :name, :contact_name, :contact_email,
                             :contact_phone, :lead_time_days, :payment_terms, :notes)
                    """),
                    {
                        "supplier_code": supplier_code,
                        "name": name,
                        "contact_name": data.get("contact_name") or None,
                        "contact_email": data.get("contact_email") or None,
                        "contact_phone": data.get("contact_phone") or None,
                        "lead_time_days": lead_time,
                        "payment_terms": data.get("payment_terms") or None,
                        "notes": data.get("notes") or None,
                    },
                )
            logger.info(f"Supplier added: {supplier_code} — {name}")
        except IntegrityError:
            raise DataValidationException(f"Supplier code '{supplier_code}' already exists.")

    def update_supplier(self, supplier_code: str, data: dict) -> None:
        """Update an existing supplier's editable fields.

        The supplier_code PK is immutable — it is passed separately and never
        included in the UPDATE. product_master.supplier freetext is NOT synced;
        generate_purchase_plan() gets the canonical name from supplier_master JOIN.

        Args:
            supplier_code: Existing supplier_code PK (normalised by caller).
            data: Dict with keys: name (required), contact_name, contact_email,
                  contact_phone, lead_time_days, payment_terms, notes.

        Raises:
            DataValidationException: On empty name or invalid lead_time_days.
        """
        name = str(data.get("name", "")).strip()
        if not name:
            raise DataValidationException("Supplier name is required.")

        try:
            lead_time = int(data.get("lead_time_days", 10))
        except (TypeError, ValueError):
            raise DataValidationException("Lead time must be at least 1 day.")
        if lead_time < 1:
            raise DataValidationException("Lead time must be at least 1 day.")

        with self.engine.begin() as conn:
            conn.execute(
                text("""
                    UPDATE supplier_master
                    SET name          = :name,
                        contact_name  = :contact_name,
                        contact_email = :contact_email,
                        contact_phone = :contact_phone,
                        lead_time_days = :lead_time_days,
                        payment_terms = :payment_terms,
                        notes         = :notes,
                        updated_at    = CURRENT_TIMESTAMP
                    WHERE supplier_code = :code
                """),
                {
                    "code": supplier_code,
                    "name": name,
                    "contact_name": data.get("contact_name") or None,
                    "contact_email": data.get("contact_email") or None,
                    "contact_phone": data.get("contact_phone") or None,
                    "lead_time_days": lead_time,
                    "payment_terms": data.get("payment_terms") or None,
                    "notes": data.get("notes") or None,
                },
            )
        logger.info(f"Supplier updated: {supplier_code}")

    def deactivate_supplier(self, supplier_code: str) -> None:
        """Deactivate a supplier that has no assigned products.

        This is the direct deactivation path for unassigned suppliers.
        For suppliers with assigned products, use bulk_reassign_and_deactivate().

        Args:
            supplier_code: Normalised supplier_code PK value.

        Raises:
            DataValidationException: If any products are still assigned.
        """
        with self.engine.connect() as conn:
            count = conn.execute(
                text("""
                    SELECT COUNT(*) FROM product_master
                    WHERE UPPER(TRIM(supplier_code)) = :code
                """),
                {"code": supplier_code.strip().upper()},
            ).scalar()

        if count > 0:
            raise DataValidationException(
                f"Cannot deactivate {supplier_code}: {count} product(s) still assigned. "
                "Use bulk reassign first."
            )

        with self.engine.begin() as conn:
            conn.execute(
                text("""
                    UPDATE supplier_master
                    SET is_active  = 0,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE supplier_code = :code
                """),
                {"code": supplier_code},
            )
        logger.info(f"Supplier deactivated: {supplier_code}")

    def bulk_reassign_and_deactivate(self, from_code: str, to_code: str) -> int:
        """Reassign all products from one supplier to another, then deactivate the source.

        Both operations execute in a single transaction — if either fails, both
        roll back. The deactivated supplier row is retained in supplier_master
        with is_active = 0.

        Args:
            from_code: supplier_code to deactivate (must exist).
            to_code:   supplier_code to receive reassigned products (must be active).

        Returns:
            int: Number of products reassigned.

        Raises:
            DataValidationException: If from_code == to_code, or to_code does not
                                     exist / is inactive.
        """
        from_code = from_code.strip().upper()
        to_code = to_code.strip().upper()

        if from_code == to_code:
            raise DataValidationException("Source and target supplier cannot be the same.")

        with self.engine.connect() as conn:
            target_active = conn.execute(
                text("""
                    SELECT COUNT(*) FROM supplier_master
                    WHERE supplier_code = :code AND is_active = 1
                """),
                {"code": to_code},
            ).scalar()

        if not target_active:
            raise DataValidationException(
                f"Target supplier '{to_code}' does not exist or is inactive."
            )

        with self.engine.begin() as conn:
            result = conn.execute(
                text("""
                    UPDATE product_master
                    SET supplier_code = :to_code
                    WHERE UPPER(TRIM(supplier_code)) = :from_code
                """),
                {"to_code": to_code, "from_code": from_code},
            )
            reassigned_count = result.rowcount

            conn.execute(
                text("""
                    UPDATE supplier_master
                    SET is_active  = 0,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE supplier_code = :code
                """),
                {"code": from_code},
            )

        logger.info(
            f"Bulk reassign: {reassigned_count} product(s) moved from "
            f"{from_code} to {to_code}. {from_code} deactivated."
        )
        return reassigned_count
