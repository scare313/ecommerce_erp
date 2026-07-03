-- Disable Foreign Keys temporarily
PRAGMA foreign_keys = OFF;

DROP TABLE IF EXISTS channel_listings;
DROP TABLE IF EXISTS pack_master;
DROP TABLE IF EXISTS product_master;
DROP TABLE IF EXISTS supplier_master;
DROP TABLE IF EXISTS schema_migrations;

PRAGMA foreign_keys = ON;

-- 1. SCHEMA MIGRATIONS (no dependencies — must be first)
CREATE TABLE IF NOT EXISTS schema_migrations (
    migration_name VARCHAR(100) PRIMARY KEY,
    applied_at     DATETIME     DEFAULT CURRENT_TIMESTAMP
);

-- 2. SUPPLIER MASTER (parent of product_master)
CREATE TABLE IF NOT EXISTS supplier_master (
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
);

-- 3. PRODUCT MASTER
CREATE TABLE product_master (
    sku VARCHAR(50) PRIMARY KEY,
    name VARCHAR(255),
    category VARCHAR(100),
    brand VARCHAR(100),
    lifecycle_status VARCHAR(50),
    
    -- Supplier Info
    supplier VARCHAR(100),
    supplier_code VARCHAR(100),
    supplier_product_code VARCHAR(100),
    
    -- Cost Structure
    mfg_cost DECIMAL(10,2) DEFAULT 0,
    packaging_cost DECIMAL(10,2) DEFAULT 0,  -- Maps to 'Unit_Box_Cost'
    labeling_labor DECIMAL(10,2) DEFAULT 0,
    inbound_transport DECIMAL(10,2) DEFAULT 0,
    total_unit_cogs DECIMAL(10,2) DEFAULT 0, 
    
    -- Tax
    hsn VARCHAR(20),
    gst_rate DECIMAL(5,2),
    mrp DECIMAL(10,2)
);

-- 4. PACK MASTER
CREATE TABLE pack_master (
    pack_sku VARCHAR(50) PRIMARY KEY,
    master_sku VARCHAR(50) REFERENCES product_master(sku),
    quantity INT DEFAULT 1,
    
    -- Pack Specifics
    packaging_cogs DECIMAL(10,2) DEFAULT 0, 
    final_l_cm DECIMAL(10,2),
    final_w_cm DECIMAL(10,2),
    final_h_cm DECIMAL(10,2),
    final_wt_kg DECIMAL(10,3)
);

-- 5. CHANNEL LISTINGS
CREATE TABLE channel_listings (
    channel_sku VARCHAR(100),
    marketplace VARCHAR(50),
    internal_sku VARCHAR(50) REFERENCES pack_master(pack_sku),
    listing_status VARCHAR(50),
    
    -- New Financial Field
    selling_price DECIMAL(10,2) DEFAULT 0.0,
    
    channel_id VARCHAR(50),
    listing_url TEXT,
    last_updated VARCHAR(50),
    comment TEXT,
    
    PRIMARY KEY (channel_sku, marketplace)
);

-- NOTE: product_master.godown_stock_packs / shop_stock_pieces were historically
-- added here via ALTER TABLE but are orphaned — all inventory tracking lives in
-- inventory_master. The columns are removed from the schema and dropped from
-- existing databases by the one-time deprecate_product_master_stock_v1 migration
-- (see init_db.py). Do not reintroduce them.

-- Updated dedicated Inventory table
CREATE TABLE IF NOT EXISTS inventory_master (
    sku VARCHAR(50) PRIMARY KEY REFERENCES product_master(sku),
    godown_stock_packs INT DEFAULT 0,
    shop_stock_pieces INT DEFAULT 0,
    pack_multiplier INT DEFAULT 1, -- Remembers the pieces-per-pack for this SKU
    reorder_point INT DEFAULT 0,   -- Low-stock threshold in packs (0 = not configured)
    reorder_qty INT DEFAULT 0,     -- Suggested reorder quantity in packs
    last_updated DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Simplified Ledger for Godown transactions
CREATE TABLE IF NOT EXISTS stock_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sku VARCHAR(50),
    transaction_type VARCHAR(20),       -- 'ADD', 'REMOVE', 'STOCK_TAKE'
    packs INT,
    multiplier INT,                     -- The multiplier used at that moment
    total_pieces_affected INT,          -- packs * multiplier
    reason_code VARCHAR(50) DEFAULT 'ADJUSTMENT', -- Structured reason (RECEIVED, SALE, DAMAGED, etc.)
    reason TEXT,                        -- Optional freetext notes
    updated_by VARCHAR(100) DEFAULT 'system',     -- Username for audit trail
    running_balance INT,                -- godown_stock_packs balance immediately AFTER this row (NULL for pre-migration history)
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Marketplace fee configuration (E9: single SQL source of truth)
CREATE TABLE IF NOT EXISTS config (
    marketplace VARCHAR(50) PRIMARY KEY,
    default_zone VARCHAR(50) DEFAULT 'national',
    volumetric_divisor INT DEFAULT 5000,
    gst_on_fees DECIMAL(5,4) DEFAULT 0.18
);

-- Per-marketplace referral and closing fee rules by category + price band
CREATE TABLE IF NOT EXISTS pricing_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    marketplace VARCHAR(50) NOT NULL,
    category_ref VARCHAR(100) NOT NULL,
    min_price DECIMAL(10,2) NOT NULL DEFAULT 0.0,
    max_price DECIMAL(10,2) NOT NULL DEFAULT 99999.0,
    referral_fee_pct DECIMAL(5,4) NOT NULL DEFAULT 0.0,
    closing_fee_inr DECIMAL(10,2) NOT NULL DEFAULT 0.0
);

-- Weight-based shipping fee rules by marketplace and zone
CREATE TABLE IF NOT EXISTS shipping_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    marketplace VARCHAR(50) NOT NULL,
    weight_slab_max_kg DECIMAL(5,3) NOT NULL,
    local_fee DECIMAL(10,2) NOT NULL DEFAULT 0.0,
    regional_fee DECIMAL(10,2) NOT NULL DEFAULT 0.0,
    national_fee DECIMAL(10,2) NOT NULL DEFAULT 0.0
);

-- Key-value store for rule-set metadata (e.g. last_verified timestamp)
CREATE TABLE IF NOT EXISTS rules_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);