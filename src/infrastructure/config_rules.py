"""Excel-driven configuration and marketplace rules manager.

Handles reading, writing, and seeding of the master marketplace rules Excel sheet at data/config/market_rules.xlsx.
"""
import os
import pandas as pd
from src.infrastructure.logger import get_logger

logger = get_logger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONFIG_DIR = os.path.join(BASE_DIR, "data", "config")
EXCEL_PATH = os.path.join(CONFIG_DIR, "market_rules.xlsx")

def ensure_config_dir():
    """Ensure data/config directory exists."""
    os.makedirs(CONFIG_DIR, exist_ok=True)

def seed_default_excel_rules(overwrite=False):
    """Seed the default 2026 e-commerce marketplace rules to Excel if missing."""
    ensure_config_dir()
    if os.path.exists(EXCEL_PATH) and not overwrite:
        logger.info(f"Market rules spreadsheet already exists at {EXCEL_PATH}. Skipping seeding.")
        return False

    logger.info(f"Seeding default high-fidelity 2026 marketplace rules to {EXCEL_PATH}...")

    # 1. Config Sheet (Default shipping zone set to 'national' for Amazon and Flipkart!)
    df_config = pd.DataFrame([
        {"marketplace": "Amazon", "default_zone": "national", "volumetric_divisor": 5000, "gst_on_fees": 0.18},
        {"marketplace": "Flipkart", "default_zone": "national", "volumetric_divisor": 5000, "gst_on_fees": 0.18},
        {"marketplace": "Meesho", "default_zone": "national", "volumetric_divisor": 5000, "gst_on_fees": 0.18},
    ])

    # 2. Pricing Rules Sheet (0% referral fees under 1000 across core categories)
    categories = ["Apparel", "Accessories", "Footwear", "Home", "Grocery", "Beauty", "Toys", "Electronics", "Other"]
    pricing_rows = []

    # --- MEESHO: 100% Free ---
    for cat in categories:
        pricing_rows.append({
            "marketplace": "Meesho", "category_ref": cat, "min_price": 0.0, "max_price": 99999.0,
            "referral_fee_pct": 0.0, "closing_fee_inr": 0.0
        })

    # --- AMAZON: 2026 Rate Card (0% Referral below 1000) ---
    for cat in categories:
        # Under 300: 0% referral, 1.00 closing fee (Easy ship low cost)
        pricing_rows.append({
            "marketplace": "Amazon", "category_ref": cat, "min_price": 0.0, "max_price": 300.0,
            "referral_fee_pct": 0.0, "closing_fee_inr": 1.00
        })
        # 300 to 500: 0% referral, 26.00 closing fee
        pricing_rows.append({
            "marketplace": "Amazon", "category_ref": cat, "min_price": 300.01, "max_price": 500.0,
            "referral_fee_pct": 0.0, "closing_fee_inr": 26.00
        })
        # 500 to 1000: 0% referral, 45.00 closing fee
        pricing_rows.append({
            "marketplace": "Amazon", "category_ref": cat, "min_price": 500.01, "max_price": 1000.0,
            "referral_fee_pct": 0.0, "closing_fee_inr": 45.00
        })
        # Above 1000: Standard referral (13.5% Apparel/Footwear, 15% others), 50.00 closing fee
        ref_fee = 0.135 if cat in ["Apparel", "Footwear"] else 0.15
        pricing_rows.append({
            "marketplace": "Amazon", "category_ref": cat, "min_price": 1000.01, "max_price": 99999.0,
            "referral_fee_pct": ref_fee, "closing_fee_inr": 50.00
        })

    # --- FLIPKART: 2026 Rate Card (0% Referral below 1000) ---
    for cat in categories:
        # Under 300: 0% referral, 5.00 closing fee
        pricing_rows.append({
            "marketplace": "Flipkart", "category_ref": cat, "min_price": 0.0, "max_price": 300.0,
            "referral_fee_pct": 0.0, "closing_fee_inr": 5.00
        })
        # 300 to 500: 0% referral, 10.00 closing fee
        pricing_rows.append({
            "marketplace": "Flipkart", "category_ref": cat, "min_price": 300.01, "max_price": 500.0,
            "referral_fee_pct": 0.0, "closing_fee_inr": 10.00
        })
        # 500 to 1000: 0% referral, 20.00 closing fee
        pricing_rows.append({
            "marketplace": "Flipkart", "category_ref": cat, "min_price": 500.01, "max_price": 1000.0,
            "referral_fee_pct": 0.0, "closing_fee_inr": 20.00
        })
        # Above 1000: Standard 15% referral, 30.00 closing fee
        pricing_rows.append({
            "marketplace": "Flipkart", "category_ref": cat, "min_price": 1000.01, "max_price": 99999.0,
            "referral_fee_pct": 0.15, "closing_fee_inr": 30.00
        })

    df_pricing = pd.DataFrame(pricing_rows)

    # 3. Shipping Rules Sheet (incorporating Amazon ₹55 base national Easy Ship rate)
    df_shipping = pd.DataFrame([
        # Amazon weight slabs
        {"marketplace": "Amazon", "weight_slab_max_kg": 0.5, "local_fee": 38.0, "regional_fee": 46.0, "national_fee": 55.0},
        {"marketplace": "Amazon", "weight_slab_max_kg": 1.0, "local_fee": 52.0, "regional_fee": 68.0, "national_fee": 98.0},
        {"marketplace": "Amazon", "weight_slab_max_kg": 2.0, "local_fee": 78.0, "regional_fee": 98.0, "national_fee": 145.0},
        # Flipkart weight slabs
        {"marketplace": "Flipkart", "weight_slab_max_kg": 0.5, "local_fee": 33.0, "regional_fee": 43.0, "national_fee": 68.0},
        {"marketplace": "Flipkart", "weight_slab_max_kg": 1.0, "local_fee": 48.0, "regional_fee": 63.0, "national_fee": 93.0},
        # Meesho weight slabs
        {"marketplace": "Meesho", "weight_slab_max_kg": 0.5, "local_fee": 25.0, "regional_fee": 35.0, "national_fee": 50.0},
    ])

    try:
        with pd.ExcelWriter(EXCEL_PATH, engine='openpyxl') as writer:
            df_config.to_excel(writer, sheet_name="Config", index=False)
            df_pricing.to_excel(writer, sheet_name="Pricing_Rules", index=False)
            df_shipping.to_excel(writer, sheet_name="Shipping_Rules", index=False)
        logger.info(f"✅ Excel rules successfully seeded at {EXCEL_PATH}")
        return True
    except Exception as e:
        logger.error(f"Failed to write default rules to {EXCEL_PATH}: {str(e)}", exc_info=True)
        return False

def load_excel_sheet(sheet_name):
    """Load a specific sheet from the market_rules.xlsx workbook. Seeds defaults if file is missing."""
    seed_default_excel_rules()
    try:
        return pd.read_excel(EXCEL_PATH, sheet_name=sheet_name)
    except Exception as e:
        logger.error(f"Error reading sheet '{sheet_name}' from {EXCEL_PATH}: {str(e)}", exc_info=True)
        # Fall back to empty DataFrame with expected columns
        if sheet_name == "Config":
            return pd.DataFrame(columns=["marketplace", "default_zone", "volumetric_divisor", "gst_on_fees"])
        elif sheet_name == "Pricing_Rules":
            return pd.DataFrame(columns=["marketplace", "category_ref", "min_price", "max_price", "referral_fee_pct", "closing_fee_inr"])
        elif sheet_name == "Shipping_Rules":
            return pd.DataFrame(columns=["marketplace", "weight_slab_max_kg", "local_fee", "regional_fee", "national_fee"])
        return pd.DataFrame()

def save_excel_sheet(sheet_name, df):
    """Save a DataFrame to a specific sheet in market_rules.xlsx, preserving other sheets."""
    seed_default_excel_rules()
    try:
        # Read existing sheets to preserve them
        sheets = {}
        for name in ["Config", "Pricing_Rules", "Shipping_Rules"]:
            if name == sheet_name:
                sheets[name] = df
            else:
                try:
                    sheets[name] = pd.read_excel(EXCEL_PATH, sheet_name=name)
                except Exception:
                    # If sheet doesn't exist, create an empty one or load defaults
                    sheets[name] = load_excel_sheet(name)

        # Write all sheets back
        with pd.ExcelWriter(EXCEL_PATH, engine='openpyxl') as writer:
            for name, sheet_df in sheets.items():
                sheet_df.to_excel(writer, sheet_name=name, index=False)
        logger.info(f"Successfully saved sheet '{sheet_name}' to {EXCEL_PATH}")
        return True
    except Exception as e:
        logger.error(f"Failed to save sheet '{sheet_name}' to {EXCEL_PATH}: {str(e)}", exc_info=True)
        raise
