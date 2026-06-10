"""Database initialization and schema setup module.

Handles database creation, schema deployment, and data migration on application startup.
"""
import os
from sqlalchemy import text, event
from sqlalchemy.exc import OperationalError
from src.infrastructure.database import get_engine, DB_PATH
from src.infrastructure.migration_script import run_migration
from src.infrastructure.config_rules import seed_default_excel_rules
from src.infrastructure.logger import (
    get_logger, DatabaseException, ConfigException, DataValidationException
)

logger = get_logger(__name__)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SCHEMA_PATH = os.path.join(SCRIPT_DIR, "schema.sql")


def init_database():
    """
    Initialize database and ensure data is loaded.
    Handles database creation, schema setup, and migration.

    Returns:
        bool: True if initialization successful, False otherwise
    """
    try:
        # Guarantee Excel rules exist
        seed_default_excel_rules()

        logger.info(f"Starting database initialization. DB Path: {DB_PATH}")

        # Check if database file exists
        db_exists = os.path.exists(DB_PATH)

        if db_exists:
            logger.info(f"Existing database found. Size: {os.path.getsize(DB_PATH)} bytes")
            return _check_and_migrate_existing_db()
        else:
            logger.info("No existing database found. Creating new database...")
            return _create_new_database()

    except DatabaseException as e:
        logger.error(f"Database error during initialization: {str(e)}", exc_info=True)
        return False
    except Exception as e:
        logger.critical(f"Unexpected error during database initialization: {str(e)}", exc_info=True)
        return False


def _apply_table_migrations(engine):
    """Create supplier_master and schema_migrations tables if they do not exist.

    Runs on every startup. Both statements are idempotent — IF NOT EXISTS means
    repeated execution on a database where the tables already exist is a no-op.
    No data is read or written.
    """
    ddl_statements = [
        (
            """CREATE TABLE IF NOT EXISTS schema_migrations (
                migration_name VARCHAR(100) PRIMARY KEY,
                applied_at     DATETIME     DEFAULT CURRENT_TIMESTAMP
            )""",
            "schema_migrations",
        ),
        (
            """CREATE TABLE IF NOT EXISTS supplier_master (
                supplier_code  VARCHAR(100) PRIMARY KEY,
                name           VARCHAR(200) NOT NULL,
                contact_name   VARCHAR(100),
                contact_email  VARCHAR(200),
                contact_phone  VARCHAR(50),
                lead_time_days INT          NOT NULL DEFAULT 10,
                payment_terms  VARCHAR(100),
                notes          TEXT,
                is_active      INTEGER      NOT NULL DEFAULT 1
                               CHECK (is_active IN (0, 1)),
                created_at     DATETIME     DEFAULT CURRENT_TIMESTAMP,
                updated_at     DATETIME     DEFAULT CURRENT_TIMESTAMP
            )""",
            "supplier_master",
        ),
    ]
    with engine.connect() as conn:
        for sql, table_name in ddl_statements:
            try:
                conn.execute(text(sql))
                conn.commit()
                logger.debug(f"Table migration: {table_name} ready")
            except OperationalError as e:
                err_msg = str(e).lower()
                if "already exists" in err_msg:
                    logger.debug(f"Table migration skipped (already exists): {table_name}")
                else:
                    logger.error(f"Table migration FAILED for {table_name}: {e}", exc_info=True)
                    raise


def _seed_supplier_master_from_products(engine):
    """Seed supplier_master from distinct supplier_codes in product_master.

    One-time migration — guarded by a row in schema_migrations. Runs to completion
    exactly once regardless of how many rows are found. Zero-row seeding (all-NULL
    supplier_code databases) is a valid outcome: the guard row is still inserted and
    the migration does not loop on subsequent startups.
    """
    migration_name = 'seed_supplier_master_v1'

    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT COUNT(*) FROM schema_migrations WHERE migration_name = :name"),
            {"name": migration_name},
        )
        if result.scalar() > 0:
            logger.debug("Seed migration already applied — skipping")
            return

    logger.info("Running one-time seed: supplier_master from product_master...")

    try:
        with engine.begin() as conn:
            # Step 1 — Normalise supplier_code casing in product_master
            conn.execute(text("""
                UPDATE product_master
                SET supplier_code = UPPER(TRIM(supplier_code))
                WHERE supplier_code IS NOT NULL AND supplier_code != ''
            """))

            # Step 2 — Insert distinct supplier records; fall back to code as name
            # when the freetext supplier column is NULL or blank
            result = conn.execute(text("""
                INSERT OR IGNORE INTO supplier_master (supplier_code, name)
                SELECT DISTINCT
                    UPPER(TRIM(supplier_code)),
                    COALESCE(NULLIF(TRIM(supplier), ''), UPPER(TRIM(supplier_code)))
                FROM product_master
                WHERE supplier_code IS NOT NULL AND TRIM(supplier_code) != ''
            """))
            seeded_count = result.rowcount
            logger.info(f"Seeded {seeded_count} supplier(s) from product_master")

            # Step 3 — Log products with no supplier_code for manual resolution
            unassigned = conn.execute(text("""
                SELECT sku, supplier FROM product_master
                WHERE supplier_code IS NULL OR TRIM(supplier_code) = ''
            """)).fetchall()
            for row in unassigned:
                logger.warning(
                    f"Product {row[0]} has no supplier_code "
                    f"(supplier freetext: '{row[1]}'). Assign in Supplier Manager."
                )
            if unassigned:
                logger.warning(
                    f"{len(unassigned)} product(s) have no supplier_code. "
                    "They will show as Unassigned until corrected in Supplier Manager."
                )

            # Step 4 — Mark migration complete unconditionally
            conn.execute(
                text("INSERT OR IGNORE INTO schema_migrations (migration_name) VALUES (:name)"),
                {"name": migration_name},
            )

        logger.info("✅ Supplier master seed migration complete.")

    except Exception as e:
        logger.error(f"Supplier master seed migration failed: {e}", exc_info=True)
        raise


def _enable_foreign_keys_if_clean(engine):
    """Enable PRAGMA foreign_keys = ON if no product_master rows have dangling supplier_codes.

    Runs on every startup. If any product has a supplier_code that is not present in
    supplier_master, FK enforcement is deferred and each orphan is logged at WARNING.
    Once the data is clean, the pragma listener is registered for all future connections
    on this engine and takes effect on the next restart.
    """
    with engine.connect() as conn:
        # Count products whose supplier_code does not exist in supplier_master
        result = conn.execute(text("""
            SELECT sku, supplier_code FROM product_master
            WHERE supplier_code IS NOT NULL
              AND TRIM(supplier_code) != ''
              AND UPPER(TRIM(supplier_code)) NOT IN (
                  SELECT supplier_code FROM supplier_master
              )
        """))
        orphans = result.fetchall()

    if orphans:
        for row in orphans:
            logger.warning(
                f"FK orphan: product '{row[0]}' has supplier_code '{row[1]}' "
                "not found in supplier_master."
            )
        logger.warning(
            f"Foreign key enforcement deferred: {len(orphans)} product(s) have "
            "unmatched supplier_code. Assign suppliers in Supplier Manager to "
            "enable FK enforcement."
        )
        return

    # Data is clean — register a connection-level listener so every new SQLAlchemy
    # connection sets PRAGMA foreign_keys = ON for this engine instance.
    @event.listens_for(engine, "connect")
    def _set_foreign_keys(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.close()

    logger.info("Foreign key enforcement enabled.")


def _apply_column_migrations(engine):
    """Apply additive ALTER TABLE column migrations idempotently.

    Each statement is wrapped in its own try/except. SQLite raises when you
    attempt to add a column that already exists; that exception is silently
    ignored so this function is safe to call on every application startup.
    """
    migrations = [
        (
            "ALTER TABLE stock_ledger ADD COLUMN "
            "reason_code VARCHAR(50) DEFAULT 'ADJUSTMENT'",
            "stock_ledger.reason_code",
        ),
        (
            "ALTER TABLE stock_ledger ADD COLUMN "
            "updated_by VARCHAR(100) DEFAULT 'system'",
            "stock_ledger.updated_by",
        ),
    ]
    with engine.connect() as conn:
        for sql, description in migrations:
            try:
                conn.execute(text(sql))
                conn.commit()
                logger.info(f"Migration applied: added {description}")
            except OperationalError as e:
                err_msg = str(e).lower()
                if "duplicate column name" in err_msg:
                    # Expected: column already exists from a previous run — safe to skip
                    logger.debug(f"Migration skipped (already applied): {description}")
                else:
                    # Unexpected DB error (locked, corrupt, permission) — do not hide it
                    logger.error(
                        f"Migration FAILED for {description}: {e}",
                        exc_info=True,
                    )
                    raise


def _check_and_migrate_existing_db():
    """
    Check if existing database has necessary tables and data.

    Returns:
        bool: True if database is ready, False if initialization failed
    """
    try:
        logger.debug("Checking existing database integrity...")
        engine = get_engine()

        # 1. Ensure supplier_master and schema_migrations tables exist
        _apply_table_migrations(engine)

        # 2. Additive column migrations (idempotent ALTER TABLE statements)
        _apply_column_migrations(engine)

        # 3. One-time supplier master seed from existing product_master data
        _seed_supplier_master_from_products(engine)

        # 4. Enable FK enforcement if all supplier_code references are clean
        _enable_foreign_keys_if_clean(engine)

        with engine.connect() as conn:
            # Check core transactional tables
            result = conn.execute(text("SELECT COUNT(*) FROM product_master"))
            product_count = result.scalar()

        if product_count > 0:
            logger.info(f"✅ Database is healthy. Found {product_count} products ready for use.")
            return True
        else:
            logger.warning("Database exists but is empty. Attempting migration...")
            try:
                run_migration()
                logger.info("✅ Database migration completed successfully.")
                return True
            except DataValidationException as e:
                err_msg = str(e).lower()
                if "no excel" in err_msg or "not found" in err_msg:
                    logger.warning(
                        "⚠️ Database is empty and no Master Catalog Excel found. "
                        "Use the Onboarding Wizard to bootstrap your catalog."
                    )
                    return True  # Empty DB is OK — wizard will handle
                logger.error(f"Migration validation failed: {str(e)}", exc_info=True)
                return False
            except Exception as e:
                logger.error(
                    f"Migration failed with an unexpected error: {str(e)}. "
                    "Database may be in an inconsistent state.",
                    exc_info=True
                )
                return False

    except Exception as e:
        logger.warning(f"Error checking existing database: {str(e)}. Attempting migration...", exc_info=True)
        try:
            run_migration()
            logger.info("✅ Database migration completed after error recovery.")
            return True
        except DataValidationException as migration_error:
            err_msg = str(migration_error).lower()
            if "no excel" in err_msg or "not found" in err_msg:
                logger.warning(
                    "⚠️ Recovery skipped: no Master Catalog Excel. "
                    "Use the Onboarding Wizard to bootstrap your catalog."
                )
                return True
            logger.error(f"Migration validation failed: {str(migration_error)}", exc_info=True)
            return False
        except Exception as migration_error:
            logger.error(
                f"Migration also failed with an unexpected error: {str(migration_error)}. "
                "Database cannot be recovered automatically.",
                exc_info=True
            )
            return False


def _create_new_database():
    """
    Create a new database with schema and initial data.

    Returns:
        bool: True if database creation successful, False otherwise

    Raises:
        ConfigException: If schema file not found or cannot be read
        DatabaseException: If database creation or schema execution fails
    """
    try:
        # Verify schema file exists
        if not os.path.exists(SCHEMA_PATH):
            error_msg = f"Schema file not found at {SCHEMA_PATH}"
            logger.error(error_msg)
            raise ConfigException(error_msg)

        logger.info(f"Reading schema from: {SCHEMA_PATH}")

        # Create engine and read schema
        engine = get_engine()

        with open(SCHEMA_PATH, 'r', encoding='utf-8') as f:
            sql_script = f.read()

        logger.info("Creating database schema...")

        # Execute schema
        with engine.connect() as conn:
            statements = sql_script.split(';')
            executed_successfully = 0

            for idx, statement in enumerate(statements):
                if statement.strip():
                    try:
                        conn.execute(text(statement))
                        executed_successfully += 1
                    except Exception as e:
                        logger.warning(f"Error executing schema statement {idx + 1}: {str(e)}")

            conn.commit()
            logger.info(f"✅ Database schema created successfully. Executed {executed_successfully} statements.")

        # 1. Ensure supplier_master and schema_migrations tables exist
        #    (schema.sql already creates them, but this is idempotent insurance)
        _apply_table_migrations(engine)

        # 2. Enable FK enforcement — fresh DB has no products, so orphan count is 0
        _enable_foreign_keys_if_clean(engine)

        # Load initial data via migration (OPTIONAL — wizard can bootstrap instead)
        logger.info("Attempting to load initial data from Master Catalog...")
        try:
            run_migration()
            logger.info("✅ Initial data migration completed successfully.")
            return True
        except DataValidationException as e:
            err_msg = str(e).lower()
            if "no excel" in err_msg or "not found" in err_msg:
                logger.warning(
                    "⚠️ No Master Catalog Excel found in data/raw_reports/. "
                    "Schema is ready — please use the Onboarding Wizard to "
                    "bootstrap your catalog from marketplace files."
                )
                return True  # Schema exists; wizard will populate it
            logger.error(f"Migration validation failed: {str(e)}", exc_info=True)
            return False
        except Exception as e:
            logger.error(f"Initial data migration failed: {str(e)}", exc_info=True)
            logger.warning(
                "Database schema created but migration encountered an error. "
                "You may still use the Onboarding Wizard to bootstrap the catalog."
            )
            return True

    except ConfigException:
        raise
    except Exception as e:
        logger.error(f"Database creation failed: {str(e)}", exc_info=True)
        raise DatabaseException(f"Database creation failed: {str(e)}") from e


if __name__ == "__main__":
    try:
        success = init_database()
        exit(0 if success else 1)
    except Exception as e:
        logger.critical(f"Critical error in database initialization: {str(e)}", exc_info=True)
        exit(1)
