"""Database initialization and schema setup module.

Handles database creation, schema deployment, and data migration on application startup.
"""
import os
from sqlalchemy import text
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


def _check_and_migrate_existing_db():
    """
    Check if existing database has necessary tables and data.
    
    Returns:
        bool: True if database is ready, False if initialization failed
    """
    try:
        logger.debug("Checking existing database integrity...")
        engine = get_engine()
        
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
                logger.error(f"Migration failed: {str(e)}", exc_info=True)
                logger.warning(
                    "Migration error — please use the Onboarding Wizard."
                )
                return True  # Schema works; let user reach the wizard
                
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
            logger.error(f"Migration also failed: {str(migration_error)}", exc_info=True)
            logger.warning("You may still use the Onboarding Wizard.")
            return True


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