"""File parsing utilities for sales and stock data import.

Supports multiple marketplace formats (Amazon, Flipkart, Meesho) and generic stock files.
"""
import pandas as pd
import io
from src.infrastructure.logger import get_logger, DataValidationException

logger = get_logger(__name__)

def clean_sku(sku):
    """
    Standardizes SKU format: Uppercase, stripped of whitespace.
    
    Args:
        sku: SKU value (can be string, numeric, or NaN)
        
    Returns:
        str: Standardized SKU
    """
    try:
        if pd.isna(sku):
            logger.warning("Encountered NaN SKU, defaulting to 'UNKNOWN'")
            return "UNKNOWN"
        return str(sku).strip().upper()
    except Exception as e:
        logger.error(f"Error cleaning SKU {sku}: {str(e)}", exc_info=True)
        return "UNKNOWN"

def parse_amazon_sales(file_buffer):
    """
    Parses Amazon Business Report CSV.
    
    Args:
        file_buffer: File-like object containing CSV data
        
    Returns:
        tuple: (DataFrame with parsed data, error_message)
    """
    try:
        logger.info("Parsing Amazon sales report...")
        df = pd.read_csv(file_buffer)
        df.columns = [c.strip() for c in df.columns]
        
        col_map = {"SKU": "sku", "Units Ordered": "qty", "(Child) ASIN": "asin"}
        df = df.rename(columns=col_map)
        
        if "sku" not in df.columns or "qty" not in df.columns:
            logger.debug("Standard headers not found, trying alternate header mapping...")
            file_buffer.seek(0)
            df = pd.read_csv(file_buffer, header=1)
            df.columns = [c.strip() for c in df.columns]
            df = df.rename(columns=col_map)

        if "sku" not in df.columns:
            error_msg = "Amazon file missing 'SKU' column"
            logger.error(error_msg)
            return None, error_msg

        df["sku"] = df["sku"].apply(clean_sku)
        df["qty"] = pd.to_numeric(df["qty"].astype(str).str.replace(",", ""), errors='coerce').fillna(0)
        df["marketplace"] = "Amazon"
        
        logger.info(f"✅ Parsed Amazon report: {len(df)} rows")
        return df[["sku", "qty", "marketplace"]], None
    except Exception as e:
        error_msg = f"Error reading Amazon file: {str(e)}"
        logger.error(error_msg, exc_info=True)
        return None, error_msg

def parse_flipkart_sales(file_buffer):
    """
    Parses Flipkart Orders Excel/CSV.
    
    Args:
        file_buffer: File-like object containing order data
        
    Returns:
        tuple: (DataFrame with parsed data, error_message)
    """
    try:
        logger.info("Parsing Flipkart sales report...")
        
        if file_buffer.name.endswith('.csv'):
            df = pd.read_csv(file_buffer)
        else:
            df = pd.read_excel(file_buffer)
            
        df.columns = [c.strip().lower() for c in df.columns]
        
        sku_col = next((c for c in df.columns if 'sku' in c), None)
        qty_col = next((c for c in df.columns if 'qty' in c or 'quantity' in c), None)
        
        if not sku_col or not qty_col:
            error_msg = "Flipkart file missing 'SKU' or 'Quantity' columns"
            logger.error(error_msg)
            return None, error_msg
            
        df = df.rename(columns={sku_col: "sku", qty_col: "qty"})
        df["sku"] = df["sku"].apply(clean_sku)
        df["qty"] = pd.to_numeric(df["qty"], errors='coerce').fillna(0)
        df["marketplace"] = "Flipkart"
        
        logger.info(f"✅ Parsed Flipkart report: {len(df)} rows")
        return df[["sku", "qty", "marketplace"]], None
    except Exception as e:
        error_msg = f"Error reading Flipkart file: {str(e)}"
        logger.error(error_msg, exc_info=True)
        return None, error_msg

def parse_meesho_sales(file_buffer):
    """
    Parses Meesho Orders Excel/CSV.
    
    Args:
        file_buffer: File-like object containing order data
        
    Returns:
        tuple: (DataFrame with parsed data, error_message)
    """
    try:
        logger.info("Parsing Meesho sales report...")
        
        if file_buffer.name.endswith('.csv'):
            df = pd.read_csv(file_buffer)
        else:
            df = pd.read_excel(file_buffer)
            
        df.columns = [c.strip().lower() for c in df.columns]
        
        sku_col = next((c for c in df.columns if 'sku' in c), None)
        qty_col = next((c for c in df.columns if 'qty' in c or 'quantity' in c), "qty_default")
        
        if not sku_col:
            error_msg = "Meesho file missing 'SKU' column"
            logger.error(error_msg)
            return None, error_msg
            
        # Meesho sometimes doesn't have qty col for orders (1 row = 1 unit)
        if qty_col == "qty_default":
            df["qty"] = 1
            logger.debug("No quantity column found in Meesho file, defaulting to 1 per row")
        else:
            df = df.rename(columns={qty_col: "qty"})
            
        df = df.rename(columns={sku_col: "sku"})
        df["sku"] = df["sku"].apply(clean_sku)
        df["qty"] = pd.to_numeric(df["qty"], errors='coerce').fillna(0)
        df["marketplace"] = "Meesho"
        
        logger.info(f"✅ Parsed Meesho report: {len(df)} rows")
        return df[["sku", "qty", "marketplace"]], None
    except Exception as e:
        error_msg = f"Error reading Meesho file: {str(e)}"
        logger.error(error_msg, exc_info=True)
        return None, error_msg

def parse_stock_file(file_buffer):
    """
    Parses Current Stock file (SKU, Qty).
    
    Args:
        file_buffer: File-like object containing stock data
        
    Returns:
        tuple: (DataFrame with parsed data, error_message)
    """
    try:
        logger.info("Parsing stock file...")
        
        if file_buffer.name.endswith('.csv'):
            df = pd.read_csv(file_buffer)
        else:
            df = pd.read_excel(file_buffer)
            
        df.columns = [c.strip().lower() for c in df.columns]
        
        sku_col = next((c for c in df.columns if 'sku' in c), None)
        qty_col = next((c for c in df.columns if 'qty' in c or 'stock' in c), None)
        
        if not sku_col or not qty_col:
            error_msg = "Stock file must have 'SKU' and 'Qty' columns"
            logger.error(error_msg)
            return None, error_msg
            
        df = df.rename(columns={sku_col: "sku", qty_col: "stock_qty"})
        df["sku"] = df["sku"].apply(clean_sku)
        df["stock_qty"] = pd.to_numeric(df["stock_qty"], errors='coerce').fillna(0)
        
        logger.info(f"✅ Parsed stock file: {len(df)} rows")
        return df[["sku", "stock_qty"]], None
    except Exception as e:
        error_msg = f"Error reading Stock file: {str(e)}"
        logger.error(error_msg, exc_info=True)
        return None, error_msg