-- Disable Foreign Keys temporarily
PRAGMA foreign_keys = OFF;

DROP TABLE IF EXISTS channel_listings;
DROP TABLE IF EXISTS pack_master;
DROP TABLE IF EXISTS product_master;

PRAGMA foreign_keys = ON;

-- 1. PRODUCT MASTER
CREATE TABLE product_master (
    sku VARCHAR(50) PRIMARY KEY,
    name VARCHAR(255),
    category VARCHAR(100),
    brand VARCHAR(100),
    lifecycle_status VARCHAR(50),
    
    -- Supplier Info
    supplier VARCHAR(100),
    supplier_code VARCHAR(100),
    
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

-- 2. PACK MASTER
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

-- 3. CHANNEL LISTINGS
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

-- Track current balances at the Product level
ALTER TABLE product_master ADD COLUMN godown_stock_packs INT DEFAULT 0;
ALTER TABLE product_master ADD COLUMN shop_stock_pieces INT DEFAULT 0;

-- Updated dedicated Inventory table
CREATE TABLE IF NOT EXISTS inventory_master (
    sku VARCHAR(50) PRIMARY KEY REFERENCES product_master(sku),
    godown_stock_packs INT DEFAULT 0,
    shop_stock_pieces INT DEFAULT 0,
    pack_multiplier INT DEFAULT 1, -- Remembers the pieces-per-pack for this SKU
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
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
);