import pandas as pd
import io

def clean_sku(sku):
    """Standardizes SKU format: Uppercase, stripped of whitespace."""
    if pd.isna(sku):
        return "UNKNOWN"
    return str(sku).strip().upper()

def parse_amazon_sales(file_buffer):
    """Parses Amazon Business Report CSV."""
    try:
        df = pd.read_csv(file_buffer)
        df.columns = [c.strip() for c in df.columns]
        
        # Amazon headers usually: "SKU", "Units Ordered"
        col_map = {"SKU": "sku", "Units Ordered": "qty", "(Child) ASIN": "asin"}
        df = df.rename(columns=col_map)
        
        if "sku" not in df.columns or "qty" not in df.columns:
            # Try parsing with header=1 if header=0 failed (common in some reports)
            file_buffer.seek(0)
            df = pd.read_csv(file_buffer, header=1)
            df.columns = [c.strip() for c in df.columns]
            df = df.rename(columns=col_map)

        if "sku" not in df.columns:
            return None, "Amazon file missing 'SKU' column."

        df["sku"] = df["sku"].apply(clean_sku)
        # Handle commas in numbers (e.g. "1,000")
        df["qty"] = pd.to_numeric(df["qty"].astype(str).str.replace(",", ""), errors='coerce').fillna(0)
        df["marketplace"] = "Amazon"
        
        return df[["sku", "qty", "marketplace"]], None
    except Exception as e:
        return None, f"Error reading Amazon file: {str(e)}"

def parse_flipkart_sales(file_buffer):
    """Parses Flipkart Orders Excel/CSV."""
    try:
        # Check if excel or csv
        if file_buffer.name.endswith('.csv'):
            df = pd.read_csv(file_buffer)
        else:
            df = pd.read_excel(file_buffer)
            
        df.columns = [c.strip().lower() for c in df.columns]
        
        # Look for SKU column
        sku_col = next((c for c in df.columns if 'sku' in c), None)
        qty_col = next((c for c in df.columns if 'qty' in c or 'quantity' in c), None)
        
        if not sku_col or not qty_col:
            return None, "Flipkart file missing 'SKU' or 'Quantity' columns."
            
        df = df.rename(columns={sku_col: "sku", qty_col: "qty"})
        df["sku"] = df["sku"].apply(clean_sku)
        df["qty"] = pd.to_numeric(df["qty"], errors='coerce').fillna(0)
        df["marketplace"] = "Flipkart"
        
        return df[["sku", "qty", "marketplace"]], None
    except Exception as e:
        return None, f"Error reading Flipkart file: {str(e)}"

def parse_meesho_sales(file_buffer):
    """Parses Meesho Orders Excel/CSV."""
    try:
        if file_buffer.name.endswith('.csv'):
            df = pd.read_csv(file_buffer)
        else:
            df = pd.read_excel(file_buffer)
            
        df.columns = [c.strip().lower() for c in df.columns]
        
        sku_col = next((c for c in df.columns if 'sku' in c), None)
        qty_col = next((c for c in df.columns if 'qty' in c or 'quantity' in c), "qty_default")
        
        if not sku_col:
            return None, "Meesho file missing 'SKU' column."
            
        # Meesho sometimes doesn't have qty col for orders (1 row = 1 unit)
        if qty_col == "qty_default":
            df["qty"] = 1
        else:
            df = df.rename(columns={qty_col: "qty"})
            
        df = df.rename(columns={sku_col: "sku"})
        df["sku"] = df["sku"].apply(clean_sku)
        df["qty"] = pd.to_numeric(df["qty"], errors='coerce').fillna(0)
        df["marketplace"] = "Meesho"
        
        return df[["sku", "qty", "marketplace"]], None
    except Exception as e:
        return None, f"Error reading Meesho file: {str(e)}"

def parse_stock_file(file_buffer):
    """Parses Current Stock file (SKU, Qty)."""
    try:
        if file_buffer.name.endswith('.csv'):
            df = pd.read_csv(file_buffer)
        else:
            df = pd.read_excel(file_buffer)
            
        df.columns = [c.strip().lower() for c in df.columns]
        
        sku_col = next((c for c in df.columns if 'sku' in c), None)
        qty_col = next((c for c in df.columns if 'qty' in c or 'stock' in c), None)
        
        if not sku_col or not qty_col:
            return None, "Stock file must have 'SKU' and 'Qty' columns."
            
        df = df.rename(columns={sku_col: "sku", qty_col: "stock_qty"})
        df["sku"] = df["sku"].apply(clean_sku)
        df["stock_qty"] = pd.to_numeric(df["stock_qty"], errors='coerce').fillna(0)
        
        return df[["sku", "stock_qty"]], None
    except Exception as e:
        return None, f"Error reading Stock file: {str(e)}"