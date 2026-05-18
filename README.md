# Internal Ecommerce Management System (EMS)

**A custom, internal ERP platform consolidating inventory, listing management, and profit analysis into a Single Source of Truth.**

---

## 📋 Table of Contents

1. [Project Overview](#project-overview)
2. [Core Design Philosophy](#core-design-philosophy)
3. [Architecture & Tech Stack](#architecture--tech-stack)
4. [Project Structure](#project-structure)
5. [Database Schema](#database-schema)
6. [Core Modules & Services](#core-modules--services)
7. [Features & Capabilities](#features--capabilities)
8. [UI Pages](#ui-pages)
9. [Setup Instructions](#setup-instructions)
10. [Usage Guide](#usage-guide)
11. [Development Guidelines](#development-guidelines)
12. [Roadmap](#roadmap)

---

## Project Overview

The **Internal Ecommerce Management System (EMS)** is a comprehensive ERP solution designed to:

- **Consolidate data** from multiple marketplaces (Amazon, Flipkart, Meesho) into a unified platform
- **Automate calculations** for COGS, platform fees, shipping costs, and net profitability
- **Eliminate data silos** by replacing scattered Google Sheets with a structured database
- **Provide real-time visibility** into actual vs. theoretical profit margins
- **Support bulk operations** for managing catalogs across multiple channels

### Key Objectives

✅ Single Source of Truth for product master, inventory, and channel listings  
✅ Accurate profit calculation accounting for all marketplace fees and taxes  
✅ Gap analysis to identify missing marketplace listings  
✅ Bulk import/export capabilities for catalog management  
✅ Streamlit-based intuitive UI for non-technical stakeholders

---

## Core Design Philosophy

1. **Single Source of Truth**
   - All data centralized in one SQLite database
   - No duplicate data across sheets or systems
   - Audit trail for all changes

2. **Strict Typing & Nomenclature**
   - **Channel_SKU**: Marketplace-specific identifier (Amazon/Flipkart)
   - **Internal_SKU**: Pack SKU (sellable unit)
   - **Master_SKU**: Base product SKU
   - Clear relationships maintained via foreign keys

3. **Separation of Concerns**
   - **UI Layer (Streamlit)**: User interface only, no business logic
   - **Core Layer (Services)**: Pure business logic, database-agnostic
   - **Infrastructure Layer**: Database, parsers, and external integrations

4. **Auditability**
   - Inventory never overwritten, only adjusted via transactions
   - All user actions tracked for compliance

---

## Architecture & Tech Stack

### Technology Stack

| Component | Technology | Version |
|-----------|-----------|---------|
| **Language** | Python | 3.10+ |
| **Database** | SQLite | 3.x (Migratable to PostgreSQL) |
| **UI Framework** | Streamlit | Latest |
| **Data Processing** | Pandas | Latest |
| **ORM** | SQLAlchemy | 2.x |
| **Visualization** | Plotly Express | Latest |
| **File Format** | Excel (xlsxwriter) | Latest |

### Architecture Layers

```
┌─────────────────────────────────────────────────────────┐
│  UI Layer (Streamlit)                                   │
│  ├── Profit Dashboard                                   │
│  ├── Gap Analysis Dashboard                             │
│  └── Data Manager (Products, Packs, Listings)           │
├─────────────────────────────────────────────────────────┤
│  Core Layer (Business Logic)                            │
│  ├── Finance Service (Profitability Calculation)        │
│  ├── Catalog Service (Master Data Management)           │
│  ├── Gap Service (Listing Gap Analysis)                 │
│  └── Bulk Service (Import/Export)                       │
├─────────────────────────────────────────────────────────┤
│  Infrastructure Layer                                   │
│  ├── Database Connection                                │
│  ├── Schema Management                                  │
│  └── File Parsers                                       │
├─────────────────────────────────────────────────────────┤
│  Data Layer (SQLite)                                    │
│  └── Relational Database                                │
└─────────────────────────────────────────────────────────┘
```

---

## Project Structure

```
ecommerce_erp/
├── main.py                          # Streamlit entry point
├── requirements.txt                 # Python dependencies
├── README.md                        # This file
├── config/                          # Configuration files (reserved)
├── data/
│   ├── db/
│   │   └── ecommerce.db            # SQLite database
│   └── raw_reports/                 # Raw data imports (reserved)
├── src/
│   ├── __init__.py
│   ├── core/                        # Business logic layer
│   │   ├── __init__.py
│   │   ├── domain/                  # Data models (reserved)
│   │   ├── interfaces/              # Contracts/Protocols (reserved)
│   │   └── services/
│   │       ├── catalog_service.py   # Product/Pack/Listing management
│   │       ├── finance_service.py   # Profitability calculations
│   │       ├── gap_service.py       # Gap analysis logic
│   │       ├── bulk_service.py      # Bulk import/export
│   │       └── __init__.py
│   ├── infrastructure/              # Database & external integrations
│   │   ├── __init__.py
│   │   ├── database.py              # SQLAlchemy engine setup
│   │   ├── init_db.py               # Database initialization
│   │   ├── migration_script.py      # Database migrations
│   │   ├── schema.sql               # Database schema definition
│   │   └── parsers/                 # File parsers (reserved)
│   └── ui/                          # User interface layer
│       ├── components/              # Reusable UI components (reserved)
│       └── pages/
│           ├── profit_dashboard.py  # SKU profitability view
│           ├── gap_dashboard.py     # Listing gap analysis view
│           └── data_manager.py      # Master data management
└── tests/                           # Test suite (reserved)
```

---

## Database Schema

### Entity Relationship Diagram

```
product_master (1) ──→ (M) pack_master ──→ (M) channel_listings
     ↓                                              ↓
[COGS, Tax]                                   [Marketplace, Price]
                                                    ↓
                                         pricing_rules & shipping_rules
```

### Tables Overview

#### 1. `product_master` - Base Products
Stores the fundamental product information and manufacturing costs.

| Column | Type | Description |
|--------|------|-------------|
| `sku` | VARCHAR(50) | Unique product identifier |
| `name` | VARCHAR(255) | Product name |
| `category` | VARCHAR(100) | Product category (links to pricing_rules) |
| `brand` | VARCHAR(100) | Brand name |
| `lifecycle_status` | VARCHAR(50) | Active/Discontinued |
| `supplier` | VARCHAR(100) | Supplier name |
| `supplier_code` | VARCHAR(100) | Supplier reference code |
| `mfg_cost` | DECIMAL(10,2) | Manufacturing cost per unit |
| `packaging_cost` | DECIMAL(10,2) | Unit box/packaging cost |
| `labeling_labor` | DECIMAL(10,2) | Labor cost for labeling |
| `inbound_transport` | DECIMAL(10,2) | Transport from supplier |
| `total_unit_cogs` | DECIMAL(10,2) | **Calculated**: Sum of all unit costs |
| `hsn` | VARCHAR(20) | HSN code for tax classification |
| `gst_rate` | DECIMAL(5,2) | GST percentage |
| `mrp` | DECIMAL(10,2) | Maximum Retail Price |

#### 2. `pack_master` - Sellable Units
Defines how products are packaged for sale (e.g., 1-pack, 2-pack, bulk).

| Column | Type | Description |
|--------|------|-------------|
| `pack_sku` | VARCHAR(50) | Unique pack identifier |
| `master_sku` | VARCHAR(50) | FK to product_master |
| `quantity` | INT | Units of product in this pack |
| `packaging_cogs` | DECIMAL(10,2) | Extra packaging/carton cost for pack |
| `final_l_cm` | DECIMAL(10,2) | Length (cm) |
| `final_w_cm` | DECIMAL(10,2) | Width (cm) |
| `final_h_cm` | DECIMAL(10,2) | Height (cm) |
| `final_wt_kg` | DECIMAL(10,3) | Final weight (kg) |

#### 3. `channel_listings` - Marketplace Listings
Tracks each marketplace listing for a pack.

| Column | Type | Description |
|--------|------|-------------|
| `channel_sku` | VARCHAR(100) | Marketplace-specific SKU |
| `marketplace` | VARCHAR(50) | Amazon, Flipkart, Meesho, etc. |
| `internal_sku` | VARCHAR(50) | FK to pack_master |
| `listing_status` | VARCHAR(50) | Active, Inactive, Delisted |
| `selling_price` | DECIMAL(10,2) | Listed selling price |
| `channel_id` | VARCHAR(50) | Marketplace channel ID |
| `listing_url` | TEXT | Direct URL to listing |
| `last_updated` | VARCHAR(50) | Last update timestamp |
| `comment` | TEXT | Internal notes |

#### 4. `pricing_rules` - Marketplace Fee Rules
Stores referral and closing fees per marketplace and category.

| Column | Type | Description |
|--------|------|-------------|
| `marketplace` | VARCHAR(50) | Amazon, Flipkart, Meesho |
| `category_ref` | VARCHAR(100) | Product category |
| `min_price` | DECIMAL(10,2) | Minimum price for this rule |
| `max_price` | DECIMAL(10,2) | Maximum price for this rule |
| `referral_fee_pct` | DECIMAL(5,4) | Referral fee percentage (0.00-1.00) |
| `closing_fee_inr` | DECIMAL(10,2) | Flat closing fee in INR |

#### 5. `shipping_rules` - Shipping Cost Rules
Defines shipping costs based on weight slabs and zones.

| Column | Type | Description |
|--------|------|-------------|
| `marketplace` | VARCHAR(50) | Amazon, Flipkart, Meesho |
| `weight_slab_max_kg` | DECIMAL(5,3) | Max weight for this slab |
| `local_fee` | DECIMAL(10,2) | Local zone shipping cost |
| `regional_fee` | DECIMAL(10,2) | Regional zone shipping cost |
| `national_fee` | DECIMAL(10,2) | National zone shipping cost |

#### 6. `config` - System Configuration
Global configuration per marketplace.

| Column | Type | Description |
|--------|------|-------------|
| `marketplace` | VARCHAR(50) | Amazon, Flipkart, Meesho (PRIMARY KEY) |
| `default_zone` | VARCHAR(50) | Default shipping zone |
| `volumetric_divisor` | INT | Divisor for volumetric weight calculation |
| `gst_on_fees` | DECIMAL(5,2) | GST percentage applied to platform fees |

---

## Core Modules & Services

### 1. **Catalog Service** (`src/core/services/catalog_service.py`)

Manages product master data, packs, and marketplace listings.

**Key Methods:**

```python
get_product_dropdown()       # Fetch all products for UI dropdown
get_pack_dropdown()          # Fetch all sellable packs
get_category_dropdown()      # Fetch unique product categories
add_product(data)            # Create new product with auto-calculated COGS
add_pack(data)               # Create new sellable pack
add_listing(data)            # Add marketplace listing
```

**Business Logic:**
- Auto-calculates `total_unit_cogs = mfg + packaging + labeling + transport`
- Cleans dropdown display to avoid duplication
- Ensures data consistency via foreign keys

---

### 2. **Finance Service** (`src/core/services/finance_service.py`)

Calculates profitability accounting for all costs and fees.

**Key Methods:**

```python
calculate_profitability(marketplace_filter=None)  # Returns detailed profit breakdown
```

**Profitability Calculation Flow:**

1. **Weight Calculation**
   - Volumetric Weight = (L × W × H) / Divisor
   - Billable Weight = Max(Physical, Volumetric)

2. **COGS Calculation**
   - Total COGS = (Product Unit COGS × Quantity) + Pack Packaging COGS

3. **Fee Calculation**
   - **Referral Fee** = Selling Price × Referral Fee %
   - **Closing Fee** = Fixed amount per marketplace/category
   - **Shipping Fee** = Based on billable weight and zone

4. **Tax Calculation**
   - GST on Fees = (Referral + Closing + Shipping) × GST %

5. **Net Profit**
   - Net Profit = Selling Price - COGS - Total Fees - GST
   - Margin % = (Net Profit / Selling Price) × 100

**Output Columns:**
```
channel_sku, product_name, marketplace, selling_price, total_cogs,
total_platform_fees, gst_on_fees, net_profit, margin_pct
```

---

### 3. **Gap Service** (`src/core/services/gap_service.py`)

Identifies missing marketplace listings.

**Key Methods:**

```python
get_gap_matrix()  # Returns Pack × Marketplace matrix with listing counts
```

**Gap Analysis Logic:**

1. **Ideal State**: Cartesian product of all Packs × all Marketplaces
2. **Actual State**: Count of existing listings per Pack × Marketplace
3. **Gap Identification**: Rows where listing_count = 0

**Output:**
```
pack_sku, marketplace, listing_count
```

---

### 4. **Bulk Service** (`src/core/services/bulk_service.py`)

Handles bulk import/export of all master data.

**Key Methods:**

```python
download_full_catalog()        # Export all data as multi-sheet Excel
upload_full_catalog(buffer)    # Import from multi-sheet Excel
```

**Export Sheets:**
- Product_Master
- Pack_Master
- Channel_Listings
- Pricing_Rules
- Shipping_Rules
- Config

**Import Flow:**
- Dependency order: Config → Products → Packs → Listings
- Automatic column normalization (trimming, lowercasing)

---

## Features & Capabilities

### ✅ Implemented Features

| Feature | Module | Status |
|---------|--------|--------|
| **Master Product Management** | Catalog Service | ✅ Active |
| **Pack Definition** | Catalog Service | ✅ Active |
| **Marketplace Listing Tracking** | Catalog Service | ✅ Active |
| **Profitability Dashboard** | Finance Service + UI | ✅ Active |
| **Gap Analysis** | Gap Service + UI | ✅ Active |
| **Bulk Catalog Import/Export** | Bulk Service | ✅ Active |
| **Multi-Marketplace Support** | Finance Service | ✅ Active (Amazon, Flipkart, Meesho) |
| **Volumetric Weight Calculation** | Finance Service | ✅ Active |
| **Platform Fee Calculation** | Finance Service | ✅ Active |
| **Shipping Cost Calculation** | Finance Service | ✅ Active |
| **GST on Fees** | Finance Service | ✅ Active |
| **Data Editor UI** | Data Manager | ✅ Active |

### 🔄 In Development / Reserved

- Inventory Transaction Tracking
- Order Import from Marketplaces
- Supplier Invoice Management
- Stock Reorder Automation
- Advanced Reporting & Analytics
- API for Third-Party Integration

---

## UI Pages

### 1. **Profit Dashboard** (`src/ui/pages/profit_dashboard.py`)

**Purpose**: Real-time SKU profitability analysis by marketplace

**Components:**
- Marketplace selector dropdown
- Key metrics cards:
  - Average Margin %
  - Average Net Profit (₹)
  - Count of Profitable SKUs
  - Count of Loss-Making SKUs
- Sortable profit breakdown table with color-coded margins
- Red-Yellow-Green gradient visualization

**Data Displayed:**
```
SKU | Product Name | Price | COGS | Fees+GST | Net Profit | Margin %
```

**Filters:**
- By marketplace (Amazon, Flipkart, Meesho)
- Sort by profit/margin

---

### 2. **Gap Analysis Dashboard** (`src/ui/pages/gap_dashboard.py`)

**Purpose**: Identify missing marketplace listings

**Components:**
- Top metrics:
  - Total Opportunities (Packs × Marketplaces)
  - Missing Listings count
  - Catalog Coverage % (Goal: 100%)
- Search/Filter controls:
  - "Show only Missing (0)" checkbox
  - SKU search box
- Pivot table: Packs (rows) × Marketplaces (columns) × Listing Count (values)
- Visualization options (bar chart, heatmap)

**Use Cases:**
- Identify which packs are not listed on which marketplaces
- Prioritize listing gaps
- Track catalog completeness

---

### 3. **Data Manager** (`src/ui/pages/data_manager.py`)

**Purpose**: Complete master data management interface

**Tabs:**

1. **Products Tab**
   - Add new product form (SKU, Name, Category, Costs, GST, Status)
   - Editable grid of all products
   - Auto-calculation of total_unit_cogs
   - Save/Revert functionality

2. **Packs Tab**
   - Add new pack form (Product selection, Pack SKU, Qty, Weight)
   - Editable grid of all packs
   - Link to base products

3. **Listings Tab**
   - Add marketplace listing (Channel SKU, Marketplace, Pack, Price)
   - Editable grid of all listings
   - Multi-marketplace support

4. **Config & Rules Tab**
   - Marketplace configuration (Default Zone, Volumetric Divisor, GST %)
   - Pricing rules (Referral fee %, Closing fee)
   - Shipping rules (Weight slabs, Zone-based costs)
   - Editable grids for all rules

5. **Bulk Operations Tab**
   - Download full catalog (Excel export)
   - Upload full catalog (Excel import)
   - Dependency-aware import with validation logs

---

## Setup Instructions

### Prerequisites

- Python 3.10 or higher
- Git
- pip package manager

### Installation

1. **Clone the Repository**
   ```bash
   git clone <repository_url>
   cd ecommerce_erp
   ```

2. **Create Virtual Environment**
   ```bash
   # Windows
   python -m venv venv
   venv\Scripts\activate

   # macOS/Linux
   python3 -m venv venv
   source venv/bin/activate
   ```

3. **Install Dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Initialize Database**
   ```bash
   python -c "from src.infrastructure.init_db import init_db; init_db()"
   ```

5. **Run the Application**
   ```bash
   streamlit run main.py
   ```

   The app will open at `http://localhost:8501`

### Database Initialization

The database is automatically initialized on first run. To manually reset:

```bash
rm data/db/ecommerce.db
python -c "from src.infrastructure.init_db import init_db; init_db()"
```

---

## Usage Guide

### Typical Workflow

#### Phase 1: Setup Master Data
1. Go to **Data Manager** → **Products Tab**
2. Add all base products (SKU, name, costs, GST)
3. System auto-calculates total_unit_cogs

#### Phase 2: Define Packs
1. Go to **Data Manager** → **Packs Tab**
2. Create sellable packs (1-pack, 2-pack, etc.)
3. Specify dimensions and weight for shipping calculation

#### Phase 3: Create Listings
1. Go to **Data Manager** → **Listings Tab**
2. Map each pack to marketplace channels
3. Enter selling price per marketplace
4. Note: Pack must exist before listing

#### Phase 4: Configure Rules
1. Go to **Data Manager** → **Config & Rules Tab**
2. Set marketplace zones and volumetric divisor
3. Add pricing rules (referral %, closing fee) per category
4. Add shipping rules (weight slabs, zone costs)

#### Phase 5: View Insights
1. **Profit Dashboard**: Analyze profitability by marketplace
2. **Gap Analysis**: Identify missing listings and prioritize

#### Phase 6: Bulk Operations
- **Export**: Download current data as Excel for backup/sharing
- **Import**: Upload updated catalog from Excel (maintains dependencies)

### Common Tasks

#### Add a New Product
```
Data Manager → Products Tab → Add New Product
├─ Enter SKU (e.g., "TSHIRT-BLK-M")
├─ Enter Name
├─ Select Category (must exist in pricing rules)
├─ Enter costs (auto-calculates total COGS)
└─ Click "Create Product"
```

#### Create a 2-Pack
```
Data Manager → Packs Tab → Add New Pack
├─ Select Base Product from dropdown
├─ Enter Pack SKU (e.g., "TSHIRT-BLK-M-PK2")
├─ Enter Quantity = 2
├─ Enter Extra Packaging Cost (if any)
├─ Enter Final Dimensions & Weight
└─ Click "Create Pack"
```

#### List on Amazon
```
Data Manager → Listings Tab → Add New Listing
├─ Enter Amazon SKU
├─ Select Marketplace = "Amazon"
├─ Select Internal SKU (Pack)
├─ Enter Selling Price
├─ Click "Create Listing"
```

#### Check Profit for a SKU
```
Profit Dashboard
├─ Select Marketplace (e.g., "Amazon")
├─ View breakdown table
├─ Identify SKU with highest/lowest margin
└─ Click row for details
```

#### Find Listing Gaps
```
Gap Analysis Dashboard
├─ View "Missing Listings" count
├─ Check "Show only Missing" to filter
├─ Search for specific SKU
└─ Plan listing creation for gaps
```

---

## Development Guidelines

### Code Organization

- **No Business Logic in UI**: All calculations happen in Services
- **Stateless Services**: Services should be independent of each other
- **Database Abstraction**: Use SQLAlchemy for all DB queries
- **Type Hints**: Use Python type hints for clarity

### Adding a New Service

1. Create `src/core/services/new_service.py`
2. Import `get_engine()` from infrastructure
3. Use SQLAlchemy for queries
4. Return Pandas DataFrames for large datasets
5. Import in UI pages as needed

### Adding a New UI Page

1. Create `src/ui/pages/new_page.py` with a `render()` function
2. Import required services
3. Use Streamlit components (st.title, st.dataframe, etc.)
4. Register in `main.py` sidebar navigation

### Database Modifications

1. Edit `src/infrastructure/schema.sql`
2. Create migration script in `src/infrastructure/migration_script.py`
3. Test thoroughly on local database
4. Document changes in this README

### Testing

The project uses **pytest** with an in-memory SQLite database for fast, isolated tests. No real data or files are touched during test runs.

### Test Suite Overview

| Module Tested | Test File | # Tests | Focus |
|---|---|---|---|
| `parsers.py` | `tests/unit/test_parsers.py` | ~45 | Sales/stock file parsing (Amazon, Flipkart, Meesho) |
| `finance_service.py` | `tests/unit/test_finance_service.py` | 42 | Profitability calc: fees, GST, margin, net profit |
| `gap_service.py` | `tests/unit/test_gap_service.py` | 12 | Pack × marketplace gap matrix |

**Total: ~99 unit tests** covering critical business logic.

### Prerequisites

Install testing dependencies (already included in `requirements.txt`):

```bash
pip install -r requirements.txt
```

### This installs:

`pytest` — test runner
`pytest-cov` — coverage reporting
`pytest-mock` — mocking helpers

### Running Tests

```bash
# Run the entire test suite
pytest
# Verbose output (recommended)
pytest -v
# Run a specific test file
pytest tests/unit/test_finance_service.py -v
# Run a specific test class
pytest tests/unit/test_finance_service.py::TestReferralFee -v
# Run a single test
pytest tests/unit/test_finance_service.py::TestReferralFee::test_amazon_tshirt_pk1_referral
```
### Filtering by Marker
Tests are tagged with markers (defined in `pytest.ini`):

```bash
# Run only unit tests
pytest -m unit
# Run only finance service tests
pytest -m finance
# Run only gap analysis tests
pytest -m gap
# Run only parser tests
pytest -m parsers
# Combine markers (e.g., unit AND finance)
pytest -m "unit and finance"
```
### Coverage Reports

```bash
# Console coverage report
pytest --cov=src --cov-report=term-missing
# Coverage for a specific module
pytest --cov=src.core.services.finance_service --cov-report=term-missing
# Generate HTML coverage report (open htmlcov/index.html)
pytest --cov=src --cov-report=html
```

### Test Architecture

#### In-memory SQLite
- Every test gets a fresh `:memory:` database.
- No state leaks between tests.

#### Schema Mirrors Production
- `tests/conftest.py` creates the same tables as `src/infrastructure/schema.sql`:
  - `product_master`
  - `pack_master`
  - `channel_listings`
  - `pricing_rules`
  - `shipping_rules`
  - `config`
- Refer to: [schema.sql](schema.sql)

#### Seeded Data
- The `seeded_engine` fixture provides realistic sample data:
  - 2 products
  - 3 packs
  - 3 listings across Amazon/Flipkart
  - Full pricing/shipping rules

#### No File I/O
- Parser tests use `io.BytesIO` buffers instead of real CSV/XLSX files.

#### Service Patching
- The `patch_get_engine` fixture redirects `get_engine()` calls in [`database.py`](database.py) to the in-memory DB so services run unmodified.

### Adding New Tests

- Place tests under tests/unit/ (or tests/integration/ if hitting real DB).
- Use existing fixtures from tests/conftest.py - don't recreate engines or seed data.
- Tag with appropriate markers: @pytest.mark.unit, @pytest.mark.finance, etc.
- Follow the naming convention: test_*.py for files, Test* for classes, test_* for functions.
- Example:

```bash
import pytest
from src.core.services.finance_service import FinanceService

@pytest.mark.unit
@pytest.mark.finance
def test_my_new_scenario(patch_get_engine, assert_close):
    service = FinanceService()
    df = service.calculate_profitability(marketplace_filter="Amazon")
    assert_close(df.iloc[0]["ref_fee"], 84.83, tol=0.5)
```


---

## Roadmap

### Phase 1: Foundation (Current - Complete)
- [x] Database schema and initialization
- [x] Master product management
- [x] Pack definitions
- [x] Marketplace listings
- [x] Profitability calculation engine
- [x] Gap analysis logic
- [x] Streamlit UI (3 pages)
- [x] Bulk import/export

### Phase 2: Inventory Management (In Progress)
- [ ] Stock tracking (In Stock, In Transit, Reserved)
- [ ] Inbound transactions (Purchase orders)
- [ ] Outbound transactions (Sales deductions)
- [ ] Physical inventory adjustments
- [ ] Inventory by location (Shop vs Godown)
- [ ] Stock aging reports

### Phase 3: Order Integration (Planned)
- [ ] Marketplace API integration (Amazon, Flipkart)
- [ ] Automated order import
- [ ] Sales deduction from inventory
- [ ] Order status tracking
- [ ] Fulfillment workflow

### Phase 4: Advanced Features (Planned)
- [ ] Supplier invoice matching
- [ ] Landed cost calculation
- [ ] Reorder point automation
- [ ] Demand forecasting
- [ ] Performance analytics by supplier/category
- [ ] REST API for third-party integration
- [ ] User authentication & role-based access
- [ ] Audit log dashboard

### Phase 5: Scaling (Future)
- [ ] PostgreSQL migration for multi-user support
- [ ] Real-time sync from marketplaces
- [ ] Advanced permission model
- [ ] Data warehouse & BI integration
- [ ] Mobile app for inventory operations

---

## Troubleshooting

### Database Connection Error
```
Error: Unable to connect to ecommerce.db
Solution: Ensure data/db/ directory exists and has write permissions
```

### Missing Columns in Profitability Report
```
Error: KeyError 'margin_pct'
Solution: Check that selling_price is set for listings. Ensure all rules are configured.
```

### Streamlit Module Not Found
```
Error: ModuleNotFoundError: No module named 'streamlit'
Solution: pip install -r requirements.txt
```

### Empty Dropdown in Data Manager
```
Issue: Category dropdown empty or Products dropdown empty
Solution: Ensure dependent data exists (e.g., add Products before creating Packs)
```

---

## Contributing

1. Create a feature branch: `git checkout -b feature/new-feature`
2. Make changes following code guidelines
3. Test thoroughly
4. Create a pull request with description

---

## License

Internal Use Only - Not for Distribution

---

## Support

For issues or questions, contact the development team.

**Last Updated:** January 2026  
**Version:** 1.0.0