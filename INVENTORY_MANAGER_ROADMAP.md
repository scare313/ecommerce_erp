# Inventory Manager — Evaluation & Roadmap

**Evaluator Role:** Principal Product Manager / Inventory Systems Architect  
**Date:** 2026-06-10  
**Scope:** Full inventory subsystem evaluation against modern IMS benchmarks  
**Context:** Internal ERP · Single company · 1–10 users · Ecommerce (Amazon, Flipkart, Meesho)

---

## EXECUTIVE SUMMARY

This system is not an inventory management system. It is a stock counter with a ledger attached. It tracks one number per SKU (godown packs) and provides manual adjustment UI. Every workflow that a business actually runs — receiving stock, cycle counts, returns, reorder alerts, purchase orders — is absent. The Demand Planner, the most sophisticated inventory-adjacent feature, does not read from the inventory database. The bulk import, which most users will use for stock takes, writes no audit trail whatsoever.

The score against modern IMS benchmarks is **2.1/10 overall**. That is not a rounding error.

---

# CURRENT INVENTORY CAPABILITIES

## What Actually Exists Today

This section documents only what is confirmed in the code. Nothing is assumed.

---

### Stock Tracking

**What exists:**

`inventory_master` table stores four columns per SKU:
- `godown_stock_packs` — number of packs in the warehouse
- `shop_stock_pieces` — number of individual pieces at the retail counter
- `pack_multiplier` — pieces per pack (single value, remembered from last stock operation)
- `last_updated` — timestamp of last modification

The UI (Tab 1 "Current Balances") displays: SKU, category, godown_stock_packs, pack_multiplier, and a calculated "Total Pieces" column (`godown_stock_packs × pack_multiplier`).

The Home Dashboard shows three aggregate numbers: SKUs Tracked (COUNT(*) from inventory_master), Out of Stock (godown_stock_packs ≤ 0), Low Stock (0 < godown_stock_packs < 5). The threshold of 5 is hardcoded in `dashboard_service.py` and is not configurable.

**What does not exist:**
- Available stock vs reserved stock
- In-transit stock
- Stock by bin, shelf, or zone within the godown
- Multi-warehouse or multi-location
- Lot or batch tracking
- Serial number tracking
- Stock on purchase order (committed inbound)
- Stock committed to open orders (committed outbound)
- Minimum or maximum stock levels (only the hardcoded <5 threshold)

**Critical structural problem:** `product_master` has `godown_stock_packs` and `shop_stock_pieces` columns added via `ALTER TABLE` in `schema.sql` (lines 68–69). These columns exist in the schema alongside the dedicated `inventory_master` table that tracks the same fields. The `product_master` columns appear to be orphaned legacy fields — all service code reads from `inventory_master` — but they exist, hold stale data, and create a silent second source of truth that will mislead any developer or direct database query.

---

### Transfers

**What exists:**

`transfer_to_shop()` in `InventoryService` moves packs from godown to shop pieces. It is atomic (uses `engine.begin()`), decrements `godown_stock_packs`, increments `shop_stock_pieces`, and writes two ledger rows (REMOVE + ADD).

**This method is not accessible from the UI.** It exists in the service layer but there is no tab, form, or button in `inventory_manager.py` that calls it. Users cannot perform a godown-to-shop transfer through the application.

There is no shop-to-godown reverse transfer. There is no inter-godown transfer concept.

---

### Stock Adjustments

**What exists:**

Tab 2 "Update Stock": select a SKU from a dropdown, choose ADD or REMOVE, enter number of packs and pack size (multiplier), optionally type a free-text reason. Submits via `manage_godown_stock()`.

This calls `engine.connect()` with manual `conn.commit()` (note: unlike `add_stock()` and `transfer_to_shop()` which were fixed to use `engine.begin()`, `manage_godown_stock()` still uses the older pattern — it was not part of the Group 1 stabilization fixes).

The ledger entry records: sku, transaction_type (ADD or REMOVE), packs, multiplier, total_pieces_affected, reason.

**What does not exist:**
- Reason codes or categories (only freetext)
- Negative stock prevention — entering REMOVE 100 when only 5 are in stock executes without error and drives `godown_stock_packs` to -95
- Approval workflow for large adjustments
- ADJUSTMENT as a distinct transaction type (separate from deliberate ADD/REMOVE)
- Shop stock adjustments (no UI for shop_stock_pieces)

---

### Pack Handling

**What exists:**

`pack_master` defines sellable units: a pack SKU maps to a master SKU with a quantity (pieces per pack), dimensions, weight, and packaging cost. One product can have multiple pack configurations (1-pack, 2-pack, etc.).

`inventory_master.pack_multiplier` stores a single integer — the pieces-per-pack value remembered from the last `manage_godown_stock()` call. This is used to calculate total pieces in the display.

**What does not exist:**
- Barcode or EAN on packs (pack_master has no barcode column)
- Case quantities (units per shipping carton)
- Separate inventory rows per pack size — the single `pack_multiplier` means you cannot simultaneously track 50 units of the 1-pack and 20 units of the 6-pack as distinct stock positions
- Pack-level reorder points
- Pack weight class for shipping tier classification in inventory context

---

### Inventory Valuation

**What exists:** Nothing.

`product_master` stores manufacturing cost components (mfg_cost, packaging_cost, labeling_labor, inbound_transport, total_unit_cogs). These are used in the Profit Dashboard for margin calculation.

**There is no purchase cost tracking at the time of receipt.** The stock_ledger has no `unit_cost` column. When 100 units arrive from a supplier at ₹45/unit and next month another 100 arrive at ₹52/unit, the system has no record of either purchase price. The total_unit_cogs in product_master is a manually-entered manufacturing cost estimate, not the actual landed cost of stock on hand.

This means:
- No Weighted Average Cost (WAC) calculation
- No FIFO or LIFO valuation
- No inventory value on the balance sheet
- No landed cost (supplier price + freight + customs) tracking
- No purchase price variance analysis

Every competitor in the benchmark has this as a table-stakes feature.

---

### Reporting

**What exists:**

- Tab 1: A flat dataframe of current godown stock (sku, category, godown_stock_packs, pack_multiplier, Total Pieces). No filtering, no sorting controls, no export.
- Tab 3: A raw dump of the stock_ledger table ordered by timestamp DESC. Color-coded ADD (green) / REMOVE (red). No filtering by SKU, date range, or transaction type. No export.
- Home Dashboard: Three aggregate numbers (tracked, out of stock, low stock with hardcoded threshold).

**What does not exist:**
- Stock movement report (inflows vs outflows over a period)
- Stock aging report (how long has current stock been sitting)
- Inventory valuation report
- ABC analysis (fast/slow movers)
- Reorder alert report
- Stockout frequency report
- Adjustment analysis (how much stock is being written off and why)
- Supplier performance report
- Purchase order status report
- Any date-range filtering anywhere

---

### Purchasing

**What exists:**

The Demand Planner page (`demand_planner.py`) uploads sales files from Amazon, Flipkart, and Meesho and generates a purchase quantity recommendation using this formula:

```
Required = ADS × (lead_time_days + safety_days + purchase_period_days) - current_stock
```

Where ADS = total_units_sold / sales_days. The result is downloadable as Excel grouped by supplier.

**Critical gap:** Current stock in this formula comes from an **uploaded stock file** (a CSV/Excel the user uploads separately), not from `inventory_master`. The Demand Planner is completely disconnected from the live inventory database. A user must manually export their current stock and re-upload it. If they forget, the system calculates as if stock = 0 and over-orders everything.

No purchase orders are created in the system. The Excel download is the end of the workflow. There is no way to track: was this PO sent? What was received? What is still outstanding?

---

### Replenishment

**What exists:**

The hardcoded "Low Stock (<5)" metric on the Home Dashboard. That is the entirety of replenishment support.

**What does not exist:**
- Configurable reorder points per SKU
- Configurable reorder quantities per SKU
- Automatic reorder suggestions
- Email or in-app notifications when stock falls below threshold
- Safety stock calculations stored against SKUs
- Min/max replenishment model
- Any link between the low stock signal and the ability to take action

---

### Auditing

**What exists:**

`stock_ledger` records a row for every `manage_godown_stock()` call and every `add_stock()` / `transfer_to_shop()` call (the latter two are not accessible via UI). Each row has: sku, transaction_type, packs, multiplier, total_pieces_affected, reason, timestamp.

**Critical audit gap:** The Bulk Update (Tab 4) executes direct `UPDATE inventory_master SET godown_stock_packs = ?, pack_multiplier = ?` via raw SQLite cursor and commits with **zero ledger entries**. This is the most likely path for a stock take or initial stock load. Any stock count imported via bulk update is completely invisible in the audit trail. The "Recent History" tab will show nothing corresponding to those changes.

**Additional audit gaps:**
- No `user_id` or username on ledger entries — cannot tell who made a change
- No `reference_id` or `reference_type` — cannot link a ledger entry to a purchase order, return, or sale
- No before/after balance snapshot on each ledger row — reconstructing balance at a point in time requires scanning the entire ledger from the beginning
- No reconciliation report comparing ledger sum to current inventory_master balance
- Bulk import shows debug output (`st.write("🔍 **Debug Info:**")` with full SKU list) to the production UI — this is not appropriate for any user-facing interface

---

# FEATURE GAP ANALYSIS

## Critical Missing Features

These are expected in any system calling itself inventory management. Their absence means the business cannot trust the numbers.

---

**1. Negative Stock Prevention**

The system allows REMOVE operations that drive stock below zero with no warning or block. This produces meaningless negative inventory numbers that cascade into wrong purchase plans and wrong stock displays.

---

**2. Bulk Import Audit Trail**

Bulk Update (Tab 4) bypasses the ledger entirely. Any stock take, initial load, or correction via bulk upload is unauditable. This is the worst audit gap in the system — it actively destroys the integrity of the ledger.

---

**3. Godown-to-Shop Transfer in UI**

`transfer_to_shop()` exists and works correctly. It is not accessible from the UI. Users who need to move stock from godown to shop have no supported path.

---

**4. Purchase Order Lifecycle**

No ability to record: we ordered X units of SKU Y from Supplier Z at ₹W each, expected on date D. No receiving workflow. No PO vs received variance. Every receiving event is an anonymous stock adjustment with freetext reason.

---

**5. Demand Planner Connected to Live Inventory**

The purchase planning engine is the most valuable feature in this module. It is broken by design: it does not read from `inventory_master`. Users must maintain a separate stock file and upload it manually. The actual inventory they've been tracking is ignored.

---

**6. Inventory Valuation**

Without a cost at time of receipt, there is no inventory asset value. The business cannot answer "what is our current stock worth" — a basic financial question.

---

**7. Configurable Low Stock Thresholds**

The "Low Stock" threshold is 5 packs hardcoded in `dashboard_service.py`. A product with 4 packs could represent months of stock or hours of stock depending on velocity. There is no per-SKU reorder point.

---

**8. Shop Stock Adjustments in UI**

`shop_stock_pieces` exists in the schema and is tracked. There is no UI to adjust it. Only godown operations are accessible. Shop stock can only be set via bulk import (no audit trail).

---

## High Value Features

These would materially improve day-to-day operations.

---

**9. Reason Codes for Adjustments**

Free-text reason fields produce noise, not insight. Structured reason codes (Received, Sold, Damaged, Lost, Count Correction, Return, Transfer) enable adjustment pattern analysis and reveal shrinkage.

---

**10. Stock Aging / Slow Mover Identification**

Using the stock_ledger receipt timestamps, the system could identify stock that has been sitting for 60, 90, or 180+ days. This is the most direct indicator of working capital tied up in dead stock.

---

**11. Supplier Master**

Supplier is a free-text VARCHAR in `product_master`. There is no supplier entity with lead time, MOQ, payment terms, or contact information. The Demand Planner groups by this freetext field — any typo creates a phantom supplier.

---

**12. User Attribution on Ledger**

No record of who made a stock change. For a 10-person team this is a significant accountability gap. When stock is wrong, there is no way to trace responsibility.

---

**13. Running Balance in Ledger**

Computing the stock balance at any historical date requires summing all ledger entries from the beginning. A `closing_balance` column on the ledger makes point-in-time queries instant and makes data corruption immediately visible.

---

**14. Stock Take / Cycle Count Workflow**

A structured workflow: export current system stock, enter physical counts, system computes variances, user approves variances, approved variances post as ADJUSTMENT entries to the ledger. Currently there is no supported path that preserves audit integrity during a count.

---

**15. Returns Handling**

Customer returns from marketplaces are a daily operational reality for ecommerce. There is no workflow for receiving returned stock, inspecting it (resaleable vs damaged), and adjusting inventory accordingly.

---

## Nice to Have Features

Useful but not blocking current operations.

---

**16. Barcode on Pack Master**

EAN/barcode column on `pack_master`. No scanning capability exists in Streamlit but the field is needed as a reference.

**17. Inventory Forecasting Beyond Simple ADS**

The current ADS × days formula doesn't account for seasonality, trends, or promotional uplift. For a small ecommerce business this is acceptable initially.

**18. Multi-location Bin Tracking**

Sub-locations within the godown (shelves, zones, bins). Relevant when the godown grows beyond a single room.

**19. Landed Cost Calculator**

Extend the purchase order cost with freight, customs, handling fees to arrive at true landed COGS per unit.

**20. Batch / Lot Tracking**

Manufacturing lot codes for quality tracing. Relevant if the business sells food, cosmetics, or other regulated categories.

---

# WORKFLOW GAP ANALYSIS

## Receiving Inventory

**What exists:** The user navigates to Tab 2, selects a SKU, chooses ADD, enters packs and multiplier, types a freetext note, and clicks submit. There is no concept of a supplier delivery.

**What is missing:**
- No way to link a receipt to a purchase order
- No expected quantity vs received quantity comparison
- No quality inspection step at receipt
- No partial delivery support
- No way to record the purchase price at time of receipt
- No GRN (Goods Received Note) document
- No receiving against a specific delivery date

**Rating: Non-functional.** Users can increment a number. That is all.

---

## Storage

**What exists:** A single "godown" concept. All stock is in the godown. No internal structure.

**What is missing:**
- No zones, aisles, shelves, or bins
- No put-away workflow
- No space utilisation tracking
- No FIFO bin assignment

**Rating: Not applicable.** For a small operation, a single godown is acceptable. This is not a blocking gap for Phase 1.

---

## Transfers

**What exists:** `transfer_to_shop()` in the service layer. No UI.

**What is missing:** A UI form for the transfer. Shop-to-godown reverse transfer. The entire transfer workflow is invisible to users.

**Rating: Partially implemented but inaccessible.**

---

## Cycle Counts

**What exists:** Nothing.

**What is missing:** The entire workflow. Export counts, enter actuals, compute variances, approve, post adjustments with CYCLE_COUNT transaction type.

The current bulk import path (Tab 4) is what users will use as a substitute. It writes no ledger entries, records no variances, and provides no approval gate. It is the worst possible implementation of a stock take.

**Rating: Absent.**

---

## Returns

**What exists:** Nothing.

**What is missing:** Receive return from marketplace, inspect condition (resaleable / damaged / dispose), return resaleable stock to inventory, write off damaged stock. No workflow, no status, no documentation.

**Rating: Absent.** For an active ecommerce business, marketplace returns are a daily workflow. This is a meaningful operational gap.

---

## Damaged Goods

**What exists:** A user can do a REMOVE adjustment with freetext reason "damaged". The stock goes down. There is no record that it was damaged specifically, no damaged stock location, no write-off approval.

**What is missing:**
- Damaged goods status (stock in quarantine, pending inspection)
- Disposal workflow with authorisation
- Damage reporting for insurance or supplier claims
- Reason code DAMAGE vs LOSS vs COUNT_CORRECTION

**Rating: Partially handled by freetext. Not structured.**

---

## Stock Adjustments

**What exists:** ADD and REMOVE with freetext reason. Atomic, ledger-logged.

**What is missing:**
- Reason codes for structured analysis
- Negative stock prevention
- Approval workflow for adjustments above a threshold
- ADJUSTMENT transaction type distinct from ADD/REMOVE
- Shop stock adjustments via UI

**Rating: Basic functionality exists. Lacks controls.**

---

## Purchase Planning

**What exists:** Demand Planner generates ADS-based purchase quantities from uploaded sales files. Downloadable as Excel. Parameters: sales days, purchase period, lead time, safety stock.

**What is missing:**
- Connection to live inventory_master (currently reads from uploaded file or assumes 0)
- Purchase order creation from the plan
- Supplier-specific lead times (single global lead time parameter)
- MOQ constraints per supplier
- Budget constraints
- Multi-period planning

**Rating: Good calculation engine. Broken data input. No output workflow.**

---

## Reorder Management

**What exists:** A hardcoded "Low Stock (<5)" counter on the home dashboard. No action can be taken from it.

**What is missing:**
- Per-SKU reorder points
- Per-SKU reorder quantities
- Alert mechanism (even just a banner)
- Automatic reorder suggestion generation
- Minimum stock level enforcement

**Rating: Does not exist.**

---

## Supplier Management

**What exists:** A freetext `supplier` VARCHAR and a `supplier_code` VARCHAR in `product_master`. Multiple products can have the same supplier name typed differently ("Rahul Textiles" vs "Rahul textile" vs "R Textiles") — the Demand Planner will split these into three phantom suppliers.

**What is missing:**
- Supplier master table
- Supplier contact information
- Lead time per supplier
- MOQ per supplier per SKU
- Payment terms
- Supplier performance metrics (on-time delivery, quality issues)

**Rating: Does not exist as a managed entity.**

---

## Inventory Auditing

**What exists:** stock_ledger records manual ADD/REMOVE operations from the UI. Tab 3 shows the raw ledger.

**What is missing:**
- Bulk import audit trail (complete gap — bulk operations are invisible)
- User attribution on every entry
- Reconciliation report (ledger sum vs current balance)
- Tamper detection (no way to know if ledger rows were deleted directly)
- Date-range filtering on the history tab
- Export of audit history

**Rating: Partial. The ledger exists and is used for manual operations. The bulk path completely bypasses it, which negates the value of having a ledger at all.**

---

# DATA MODEL GAP ANALYSIS

## Inventory Tables

`inventory_master` is minimally viable as a current-balance table. It has four columns. It does not support:

- Stock status (available / reserved / damaged / quarantine)
- Multiple cost layers for valuation
- Location within the warehouse
- Committed quantity (reserved for open orders)
- In-transit quantity (ordered, not yet received)

The table needs additional columns before most Phase 1 improvements can be built.

**Orphaned dual-tracking:** `product_master.godown_stock_packs` and `product_master.shop_stock_pieces` (added via ALTER TABLE) duplicate the fields in `inventory_master`. No service code writes to or reads from these columns for inventory purposes — but they exist in the schema, contain stale data from any era when they were the primary tracking mechanism, and will mislead any developer querying the database directly. These columns should be removed or deprecated with a migration.

## Stock Ledger

`stock_ledger` is structurally adequate for basic audit trail but has five specific limitations:

**1. No user_id.** Every entry is anonymous. In a shared system, this makes accountability impossible.

**2. No reference_type / reference_id.** There is no way to link a ledger entry to a purchase order, sale, return, or transfer document. Entries are free-floating records with no business context beyond freetext reason.

**3. No unit_cost.** Inventory valuation is impossible without purchase price at time of receipt. This is not addable retroactively.

**4. No closing_balance.** Computing stock at any historical point requires scanning all prior entries. A running balance column makes this a single-row lookup and makes data integrity visible — if the closing_balance on row N doesn't equal closing_balance on row N-1 plus the delta on row N, a row was deleted or modified.

**5. Bulk import bypass.** The `transaction_type` column supports ADD, REMOVE, and ADJUSTMENT. The most common real-world operation — importing a stock count — writes nothing to this table. This makes the ledger a partial record, which is arguably worse than no record at all because it creates false confidence.

## Pack Structure

`pack_master` stores dimensions, weight, and quantity per pack. This is functional for shipping cost calculations.

**Missing:**
- `barcode` / `ean` column — no external reference for physical picking
- `case_qty` — units per shipping carton from supplier
- `min_order_qty` — supplier constraint per pack type

The deeper structural problem: `inventory_master` stores a single `pack_multiplier` integer per SKU. This means if a product is received in 1-packs and 6-packs simultaneously (common in ecommerce warehousing), they cannot be tracked separately. The multiplier value is overwritten by each stock operation, creating ambiguity when multiple pack sizes coexist.

A correct model would track inventory at the `pack_sku` level (from `pack_master`), not at the `master_sku` level. This would allow separate tracking of 1-pack stock vs 6-pack stock, which have different shipping costs, different storage footprints, and different reorder behaviour.

## SKU Model

The three-tier SKU model (Master SKU → Pack SKU → Channel SKU) is conceptually correct and well-implemented for the catalog and profitability modules.

For inventory, the model is only applied at the Master SKU level (`inventory_master.sku` references `product_master.sku`). The Pack SKU level — which is what is actually shipped — is not tracked in inventory. This means the system cannot answer "how many 6-packs of SKU-X do I have ready to ship" vs "how many 1-packs."

**Missing tables entirely:**

| Table | Purpose | Impact |
|---|---|---|
| `supplier_master` | Managed supplier entity with lead times, MOQ, contacts | Demand planning, PO management |
| `purchase_orders` | PO header: supplier, date, status, expected delivery | Receiving workflow, in-transit stock |
| `purchase_order_lines` | PO line: pack_sku, qty_ordered, unit_cost, qty_received | Partial receipts, cost capture |
| `stock_count_sessions` | Physical count header with date and counter | Cycle count workflow |
| `stock_count_lines` | System qty vs counted qty per SKU | Variance approval workflow |
| `returns` | Customer return header with marketplace and reason | Returns workflow |
| `return_lines` | Per-SKU return quantity and disposition | Stock reinstatement vs write-off |

---

# INVENTORY HEALTH SCORE

Scored against the benchmark set (Zoho, Cin7, Unleashed, Fishbowl, NetSuite, Odoo, ERPNext) for a comparable use case (small ecommerce business, multi-SKU, multi-channel).

---

**Tracking: 2/10**

Two stock numbers per SKU (godown packs, shop pieces). No multi-location, no lot tracking, no status differentiation, no reserved/committed stock, no in-transit. The Demand Planner doesn't even read the tracked stock. For a business with 50-500 SKUs across three marketplaces, these numbers cannot be trusted as operational inputs because there is no workflow to keep them accurate.

---

**Accuracy: 1/10**

No negative stock prevention. Bulk import overwrites without audit trail. No cycle count workflow. No reconciliation check. The system has no mechanism to detect or prevent stock drift. A number in `inventory_master` could be wrong for months before anyone notices because there is no process that validates it.

---

**Auditing: 3/10**

The ledger exists and records manual ADD/REMOVE operations correctly (after the stabilization fixes). This is a genuine capability. However, the bulk import path — which will be the primary data entry path for most users — writes nothing to the ledger. An audit of the stock_ledger would show a false picture of inventory history. No user attribution, no reference links.

---

**Reporting: 1/10**

A flat balance table and a raw ledger dump. No movement report. No aging report. No valuation report. No export from the balance view. No date filtering. No SKU filtering on the history tab. Every competing system in the benchmark provides at minimum 10 pre-built inventory reports. This system provides 0.

---

**Operations: 2/10**

Manual ADD/REMOVE adjustment exists. Transfer to shop exists in code but not in UI. No receiving workflow. No returns. No cycle count. No PO management. No reorder management. The Demand Planner is functional as a calculation tool but disconnected from the live database. The bulk import has no audit trail and is the only efficient data entry path.

---

**Scalability: 3/10**

SQLite is acceptable for 1–10 users as stated in the system constraints. The data model limits prevent scaling inventory sophistication regardless of database choice — a single multiplier per SKU, no cost per receipt, no status fields. Adding features in the current schema requires schema changes, not just query changes.

---

**Overall Inventory Health: 2.0/10**

---

# TOP 20 IMPROVEMENTS

Ranked by Business Impact ÷ Implementation Effort ratio. High-impact, low-effort items first.

---

## Rank 1 — Connect Demand Planner to Live Inventory

**Problem:** `generate_purchase_plan()` reads current stock from an uploaded file. It ignores `inventory_master` entirely. Users must export stock manually and re-upload it. If they forget, every SKU is treated as zero stock and the plan overstates requirements.

**Business Value:** The Demand Planner is the most operationally useful feature in the module. Making it accurate requires one code change: query `inventory_master` as the default stock input and only use the uploaded file as an override.

**Implementation Complexity:** Low. Add a method `get_stock_for_planning()` to `InventoryService` that returns a DataFrame of (sku, total_pieces). Pass it as the default stock in `generate_purchase_plan()` if no stock file is uploaded.

**Priority: P0. Do this immediately.**

---

## Rank 2 — Negative Stock Prevention

**Problem:** REMOVE operations execute without validation. `godown_stock_packs` can go negative.

**Business Value:** Negative inventory corrupts every downstream calculation — purchase plans, stock reports, home dashboard out-of-stock counts. Prevents phantom stock.

**Implementation Complexity:** Low. In `manage_godown_stock()`, before executing the UPDATE, check current balance. If action_type == "REMOVE" and current_packs < packs: raise `DataValidationException`.

**Priority: P0. Trivial to implement, high damage if absent.**

---

## Rank 3 — Ledger Entries for Bulk Import

**Problem:** Tab 4 Bulk Update writes absolute stock values with no ledger entries. Any stock take or bulk correction is invisible in the audit trail.

**Business Value:** Restores audit integrity for the most common mass data entry path. Without this, the ledger is a partial record.

**Implementation Complexity:** Low-Medium. Before the UPDATE, read current balance. After the UPDATE, compute delta, write ADJUSTMENT ledger entry with transaction_type = 'STOCK_TAKE'. Wrap all operations in a transaction.

**Priority: P0.**

---

## Rank 4 — Transfer to Shop UI

**Problem:** `transfer_to_shop()` is implemented and correct. There is no UI for it. Users who sell from a physical shop cannot record the movement.

**Business Value:** Completes an existing, working workflow. No service code changes needed — only UI.

**Implementation Complexity:** Low. Add a Tab 5 "Transfer to Shop" or a sub-section in Tab 2. Form: select SKU, enter packs, confirm multiplier, submit → `transfer_to_shop()`.

**Priority: P1.**

---

## Rank 5 — Configurable Reorder Points per SKU

**Problem:** Low stock threshold is hardcoded as `< 5` in `dashboard_service.py`. Meaningless for most SKUs.

**Business Value:** A product with 4 packs remaining at 50 units/day velocity needs immediate action. A product with 4 packs remaining at 2 units/month needs no action. Per-SKU reorder points make the low stock alert meaningful.

**Implementation Complexity:** Low. Add `reorder_point INT DEFAULT 0` and `reorder_qty INT DEFAULT 0` to `inventory_master`. Update the dashboard query to `godown_stock_packs <= reorder_point`. Add input fields to the stock update UI for setting these values.

**Priority: P1.**

---

## Rank 6 — Adjustment Reason Codes

**Problem:** Freetext reason field produces noise. "received" / "stock received" / "from supplier" / "new stock" are all the same event but unqueryable.

**Business Value:** Reason code analysis reveals shrinkage patterns, identifies frequent damaged-goods SKUs, and provides audit-quality documentation.

**Implementation Complexity:** Low. Add `reason_code VARCHAR(50)` to `stock_ledger`. Provide a dropdown in the UI: RECEIVED, SOLD, DAMAGED, LOST, COUNT_CORRECTION, RETURN, TRANSFER, OTHER. Keep the freetext field for notes.

**Priority: P1.**

---

## Rank 7 — Running Balance in Ledger

**Problem:** Computing stock at any historical point requires summing all ledger entries from creation. No way to detect if a row was modified or deleted.

**Business Value:** Instant point-in-time balance queries. Data integrity detection. Makes reconciliation feasible.

**Implementation Complexity:** Low. Add `closing_balance INT` to `stock_ledger`. When inserting a ledger row, read current balance and store it. A SELECT WHERE closing_balance != LAG(closing_balance) + delta detects corruption.

**Priority: P1.**

---

## Rank 8 — User Attribution on Ledger

**Problem:** No record of who made stock changes. No accountability.

**Business Value:** When stock is wrong, the business can identify who made the change and when. Essential for any shared-use system.

**Implementation Complexity:** Low. Add `updated_by VARCHAR(100)` to `stock_ledger`. In `manage_godown_stock()` and other write paths, accept an optional `user` parameter. For now, default to a config-set username or "system". When authentication (Item 3.1 in the Stabilization Plan) is implemented, pass the logged-in username automatically.

**Priority: P1.**

---

## Rank 9 — Remove Orphaned Stock Columns from product_master

**Problem:** `product_master.godown_stock_packs` and `product_master.shop_stock_pieces` are dead schema columns that duplicate `inventory_master`. They contain stale data and create a false second source of truth.

**Business Value:** Schema clarity. Prevents future developers from querying the wrong table. Eliminates confusion in direct database access.

**Implementation Complexity:** Low. Write a migration to copy any non-zero values from product_master columns to inventory_master (in case they hold real data), then drop the columns. Update schema.sql to remove the ALTER TABLE statements.

**Priority: P1.**

---

## Rank 10 — Remove Debug Output from Bulk Import UI

**Problem:** `st.write("🔍 **Debug Info:**")` and `st.write(f"SKUs to update: {df_upload['sku'].tolist()}")` print internal data to the production UI in Tab 4.

**Business Value:** Professional user interface. Prevents internal SKU lists from being visible on screen during demos or shared use. Also removes `print()` statements that write to server stdout.

**Implementation Complexity:** Trivial. Delete 2 lines. Move to logger.debug().

**Priority: P0. This should have been caught in code review.**

---

## Rank 11 — Supplier Master Table

**Problem:** Supplier is a freetext field. Typos create phantom suppliers in the Demand Planner grouping. No lead times, MOQ, or contact information.

**Business Value:** Accurate Demand Planner grouping. Foundation for PO management. Enables per-supplier lead time in purchase calculations instead of a single global parameter.

**Implementation Complexity:** Medium. New table `supplier_master`: supplier_code (PK), name, contact_name, contact_email, lead_time_days, min_order_qty, payment_terms. Add FK from `product_master.supplier_code` to `supplier_master.supplier_code`. Migrate existing supplier data. Update Demand Planner to use `lead_time_days` per supplier.

**Priority: P1.**

---

## Rank 12 — Stock Take / Cycle Count Workflow

**Problem:** No structured cycle count process. Users improvise with bulk import which leaves no audit trail.

**Business Value:** Enables periodic inventory verification with full traceability. Variance approval workflow prevents unauthorised adjustments.

**Implementation Complexity:** Medium. Workflow: (1) export current system balances to Excel, (2) user enters physical counts, (3) upload comparison — system shows variances, (4) user approves, (5) approved variances post as STOCK_TAKE adjustment entries. Requires new UI flow and a session/staging table.

**Priority: P2.**

---

## Rank 13 — Purchase Order Tracking (Lightweight)

**Problem:** No record of what was ordered from whom. Receiving is anonymous. In-transit stock is invisible.

**Business Value:** Answering "what stock is coming and when" is a basic operational question. Eliminates over-ordering because in-transit stock isn't visible.

**Implementation Complexity:** Medium-High. New tables: `purchase_orders` (PO header) and `purchase_order_lines` (line items). Simple status: DRAFT → SENT → PARTIAL → RECEIVED. Receiving workflow: against open PO, enter received qty, auto-post ADD ledger entries with reference to PO, capture unit cost.

**Priority: P2.**

---

## Rank 14 — Inventory Valuation (WAC)

**Problem:** No purchase cost at time of receipt. Cannot value inventory on the balance sheet.

**Business Value:** Real COGS for profit calculation (current system uses estimated manufacturing cost). Inventory asset value for financial reporting.

**Implementation Complexity:** Medium. Add `unit_cost DECIMAL(10,2)` to `stock_ledger`. On receipt events (from PO or direct ADD with RECEIVED reason code), record the unit cost. WAC = sum(packs × unit_cost) / total_packs, recalculated on each receipt. Requires the PO tracking from Rank 13 to be meaningful.

**Priority: P2.**

---

## Rank 15 — Returns Workflow

**Problem:** Customer returns have no supported path. Users either do an ADD adjustment (no context) or ignore the returned stock.

**Business Value:** Accurate stock levels for returned units. Visibility into return rates by SKU — a key ecommerce health metric. Proper handling of damaged vs resaleable returns.

**Implementation Complexity:** Medium. New UI flow: record return (marketplace, channel_sku, qty, reason, condition). If resaleable: post ADD to inventory with RETURN transaction type. If damaged: write off. Track return rate per SKU.

**Priority: P2.**

---

## Rank 16 — Stock Aging Report

**Problem:** No visibility into how long stock has been sitting. Dead stock ties up working capital silently.

**Business Value:** Identifies slow-moving SKUs for promotions, price cuts, or discontinuation decisions. Directly reduces working capital waste.

**Implementation Complexity:** Medium. Using the stock_ledger receipt events (ADD with RECEIVED reason) and FIFO logic, calculate the age of current on-hand units. Display as: <30 days / 30-60 / 60-90 / 90+ day buckets per SKU.

**Priority: P2.**

---

## Rank 17 — `manage_godown_stock()` Transaction Fix

**Problem:** `manage_godown_stock()` — the active code path for all UI stock updates — still uses `engine.connect()` + manual `conn.commit()` instead of `engine.begin()`. If the ledger INSERT fails after the inventory UPDATE, stock changes without an audit entry. This is Item 1.1's unfixed sibling.

**Business Value:** Atomicity guarantee on the most-used write path. Consistent with the pattern already applied to `add_stock()` and `transfer_to_shop()`.

**Implementation Complexity:** Trivial. Change `with self.engine.connect() as conn:` + `conn.commit()` to `with self.engine.begin() as conn:`.

**Priority: P0. This is a stabilization item missed in Group 1.**

---

## Rank 18 — Pack-Level Inventory Tracking

**Problem:** `inventory_master` tracks stock at the master SKU level with a single multiplier. Cannot track 1-packs and 6-packs of the same product separately.

**Business Value:** Correct available-to-sell calculations per pack type. Enables pack-level reorder points.

**Implementation Complexity:** High. Requires schema refactor: replace `inventory_master.sku → product_master.sku` with `inventory_master.pack_sku → pack_master.pack_sku`. Existing inventory data must be migrated. All service queries need updating.

**Priority: P3. Correct but disruptive. Do after the system is stable.**

---

## Rank 19 — Sales Deduction from Marketplace Orders

**Problem:** When an order ships on Amazon, the inventory doesn't decrease. The system has no knowledge of orders. Stock can only be reduced manually.

**Business Value:** Automated stock deduction eliminates manual REMOVE adjustments for sales. Reduces the frequency of stock take discrepancies. Essential for "real-time" inventory accuracy.

**Implementation Complexity:** High. Requires parsing marketplace order files (parsers for Amazon, Flipkart, Meesho orders already exist for demand planning), deducting stock, and writing SALE ledger entries. Order idempotency (don't deduct the same order twice) requires an order_id tracking table.

**Priority: P2-P3. High value but substantial scope.**

---

## Rank 20 — Inventory ABC Analysis

**Problem:** No velocity classification. Cannot distinguish fast-moving from slow-moving stock for prioritised stock counts, storage placement, or reorder frequency.

**Business Value:** Focus cycle counting effort on A-items (high value/velocity). Set tighter reorder points for A-items. Identify C-items for clearance.

**Implementation Complexity:** Low-Medium. Using sales data from the Demand Planner or historical ledger, classify SKUs as A (top 20% of revenue contribution), B (next 30%), C (bottom 50%). Display classification in inventory balance table.

**Priority: P3.**

---

# INVENTORY_MANAGER_ROADMAP

## Phase 1 — Operational Excellence

**Goal:** Make the inventory manager trustworthy and complete for daily operations. No new features that don't serve day-to-day accuracy.

**Duration estimate:** 3–4 weeks

---

### Sprint 1.1 — Fix What's Broken (Week 1)

These are defects, not features. They should have been in the stabilization plan.

| Item | Task | Acceptance Criteria |
|---|---|---|
| P0.1 | Remove debug output from Tab 4 bulk import | `st.write("🔍 Debug Info")` and `print()` statements removed |
| P0.2 | Fix `manage_godown_stock()` to use `engine.begin()` | If ledger INSERT fails, inventory UPDATE rolls back |
| P0.3 | Connect Demand Planner to `inventory_master` | Generating a plan with no stock file uploaded reads from DB; stock file is optional override |
| P0.4 | Add negative stock prevention to `manage_godown_stock()` | REMOVE of 10 when 5 in stock raises a user-visible error, no DB change |
| P0.5 | Write ledger entries during bulk import | After bulk import, stock_ledger contains STOCK_TAKE entries showing before/after delta for each SKU updated |

**Dependencies:** None — all are isolated fixes.

---

### Sprint 1.2 — Complete Existing Workflows (Week 2)

| Item | Task | Acceptance Criteria |
|---|---|---|
| 1.2.1 | Transfer to Shop UI (Tab 5 or sub-section of Tab 2) | User can select SKU, enter packs, confirm multiplier, execute transfer. Two ledger entries written. Godown decreases, shop increases. |
| 1.2.2 | Shop stock adjustment in UI | User can ADD or REMOVE shop_stock_pieces. Ledger entry written. |
| 1.2.3 | Reason code dropdown for all adjustments | Reason field in Tab 2 becomes a dropdown (RECEIVED, SOLD, DAMAGED, LOST, COUNT_CORRECTION, RETURN, TRANSFER, OTHER) plus optional notes text field. `reason_code` column added to `stock_ledger`. |
| 1.2.4 | User attribution on ledger | `updated_by` column added to `stock_ledger`. Defaults to a config-set name until auth is implemented. |

**Dependencies:** 1.2.3 and 1.2.4 require schema migration on `stock_ledger`.

---

### Sprint 1.3 — Data Model Cleanup (Week 3)

| Item | Task | Acceptance Criteria |
|---|---|---|
| 1.3.1 | Add `reorder_point` and `reorder_qty` to `inventory_master` | Columns exist with DEFAULT 0. UI allows setting these values per SKU. |
| 1.3.2 | Add `running_balance` to `stock_ledger` | Column populated on every write. Reconciliation query confirms balance = sum of all deltas. |
| 1.3.3 | Update Home Dashboard low stock to use `reorder_point` | `WHERE godown_stock_packs <= reorder_point AND reorder_point > 0` replaces hardcoded `< 5` |
| 1.3.4 | Migrate and deprecate orphaned product_master stock columns | Migration copies any non-zero values from `product_master.godown_stock_packs` and `product_master.shop_stock_pieces` to `inventory_master`. ALTER TABLE drops or nulls the columns. |

**Dependencies:** 1.3.1 before 1.3.3. Schema migration required.

---

### Sprint 1.4 — Reporting Foundation (Week 4)

| Item | Task | Acceptance Criteria |
|---|---|---|
| 1.4.1 | Stock Movement Report | Filter by SKU, date range, transaction type. Shows opening balance, movements, closing balance. Exportable as CSV. |
| 1.4.2 | Low Stock Alert List | A dedicated view showing all SKUs where `godown_stock_packs <= reorder_point`. Linked to Demand Planner. |
| 1.4.3 | Date range filter on history tab | Tab 3 allows filtering by date range and transaction type. |
| 1.4.4 | Balance export from Tab 1 | Download button for current balances as CSV/Excel (currently no export exists). |

**Dependencies:** 1.3.1 (reorder_point) for 1.4.2. 1.3.2 (running_balance) for 1.4.1.

---

**Phase 1 Definition of Done:**

- Negative stock is impossible via UI
- Every stock change (including bulk import) has a ledger entry
- Transfer to shop is accessible in the UI
- Demand Planner reads from live inventory
- Reorder points are configurable per SKU
- Low stock alert is based on actual reorder points
- Stock movement report with date filtering exists
- Balance view is exportable

---

## Phase 2 — Inventory Intelligence

**Goal:** Give the business the ability to plan, receive, and measure inventory with structured workflows.

**Duration estimate:** 4–6 weeks

**Dependency:** Phase 1 must be complete and stable.

---

### Sprint 2.1 — Supplier Master

| Item | Task | Acceptance Criteria |
|---|---|---|
| 2.1.1 | `supplier_master` table | supplier_code (PK), name, contact_name, contact_email, lead_time_days, min_order_qty, payment_terms, is_active |
| 2.1.2 | Supplier management UI | CRUD interface for supplier records. At minimum: add, edit, deactivate. |
| 2.1.3 | Link product_master to supplier_master | `supplier_code` FK validated against `supplier_master` on product create/update |
| 2.1.4 | Per-supplier lead time in Demand Planner | `generate_purchase_plan()` uses `supplier_master.lead_time_days` per supplier group instead of global sidebar parameter |

**Acceptance Criteria:** Demand Planner shows different lead times for different suppliers. Supplier typos produce a validation error, not a phantom supplier.

---

### Sprint 2.2 — Purchase Order Lifecycle

| Item | Task | Acceptance Criteria |
|---|---|---|
| 2.2.1 | `purchase_orders` table | po_number (PK), supplier_code (FK), order_date, expected_date, status (DRAFT/SENT/PARTIAL/RECEIVED/CANCELLED), notes |
| 2.2.2 | `purchase_order_lines` table | po_number (FK), sku, qty_ordered, unit_cost, qty_received |
| 2.2.3 | Create PO from Demand Planner | "Create PO" button converts plan output into a draft purchase order per supplier |
| 2.2.4 | PO management UI | List, view, edit draft POs. Mark as sent. |
| 2.2.5 | Receiving workflow | Against an open PO: enter received qty per line, confirm. Posts ADD ledger entries with RECEIVED reason code, captures unit_cost, partially or fully closes the PO. |

**Acceptance Criteria:** Creating a PO from the Demand Planner and receiving against it produces correct inventory adjustments with unit cost captured. Partially received POs show remaining quantity.

---

### Sprint 2.3 — Inventory Valuation

| Item | Task | Acceptance Criteria |
|---|---|---|
| 2.3.1 | `unit_cost` column on `stock_ledger` | NULL for non-receipt transactions. Populated from PO line on RECEIVED transactions. |
| 2.3.2 | WAC calculation | `InventoryService.get_weighted_average_cost(sku)` returns current WAC. Recalculated after each receipt. |
| 2.3.3 | Inventory value report | Table: SKU, current_packs, WAC, total_value. Grand total. Exportable. |

**Acceptance Criteria:** After receiving 100 units at ₹45 and 50 units at ₹60, the WAC is ₹50 and the inventory value report shows ₹7,500.

---

### Sprint 2.4 — Cycle Count Workflow

| Item | Task | Acceptance Criteria |
|---|---|---|
| 2.4.1 | Export count sheet | Download Excel with current system balances for counting |
| 2.4.2 | Upload count results | Import counted quantities, system computes variance (system_qty - counted_qty) per SKU |
| 2.4.3 | Variance review and approval | Display variance table. User approves or rejects each line. Approved variances post as COUNT_CORRECTION adjustment entries. |
| 2.4.4 | Count session tracking | Track when counts were performed, by whom, and total variance magnitude |

**Acceptance Criteria:** A complete count cycle from export to approval produces COUNT_CORRECTION ledger entries with approved variances. System balance after approval matches counted quantities.

---

### Sprint 2.5 — Returns Handling

| Item | Task | Acceptance Criteria |
|---|---|---|
| 2.5.1 | Returns UI | Enter: marketplace, channel_sku, qty, return_reason, condition (Resaleable/Damaged/Dispose) |
| 2.5.2 | Resaleable return processing | Posts ADD to inventory with RETURN transaction type and reason |
| 2.5.3 | Damaged return processing | Posts to damaged_pieces (new column in inventory_master) with DAMAGE reason code |
| 2.5.4 | Return rate reporting | Returns per SKU over time period |

**Acceptance Criteria:** A returned item marked resaleable increases godown stock with a RETURN ledger entry. A damaged item does not increase available stock but is tracked separately.

---

**Phase 2 Definition of Done:**

- Supplier master manages lead times and prevents freetext drift
- Purchase orders track inbound stock and capture unit cost
- Inventory is valued at WAC
- Cycle counts are auditable end-to-end
- Customer returns are handled with appropriate stock disposition

---

## Phase 3 — Advanced Inventory Management

**Goal:** Automate routine decisions and enable data-driven inventory strategy.

**Duration estimate:** 6–8 weeks

**Dependency:** Phase 2 complete.

---

### Sprint 3.1 — Automated Reorder Suggestions

| Item | Task | Acceptance Criteria |
|---|---|---|
| 3.1.1 | Velocity-based reorder point calculation | System calculates suggested reorder point = ADS × (lead_time + safety_days) per SKU using historical sales |
| 3.1.2 | Reorder suggestion engine | Daily check: SKUs at or below reorder point with no open PO → generate suggested PO list |
| 3.1.3 | One-click PO creation from suggestions | Confirm suggested quantities and create draft POs per supplier |

---

### Sprint 3.2 — Stock Aging and ABC Analysis

| Item | Task | Acceptance Criteria |
|---|---|---|
| 3.2.1 | Stock aging report | Current stock bucketed by receipt date: <30d, 30-60d, 60-90d, 90d+ |
| 3.2.2 | ABC classification | A/B/C classification based on revenue contribution. Stored per SKU, refreshed on demand. |
| 3.2.3 | Dead stock identification | SKUs with stock and zero sales in the last 90 days. Clearance recommendation flag. |

---

### Sprint 3.3 — Pack-Level Inventory (Schema Refactor)

| Item | Task | Acceptance Criteria |
|---|---|---|
| 3.3.1 | Migrate inventory_master to pack_sku level | `inventory_master.sku` FK to `pack_master.pack_sku`. Separate rows for 1-packs and 6-packs of same product. |
| 3.3.2 | Update all service queries | All reads from inventory_master use pack-level joins |
| 3.3.3 | Update Demand Planner to use pack-level stock | Available-to-sell calculation correct per pack configuration |

---

### Sprint 3.4 — Sales Order Deduction

| Item | Task | Acceptance Criteria |
|---|---|---|
| 3.4.1 | Order file parser integration | Reuse existing marketplace parsers for order files |
| 3.4.2 | Order deduction workflow | Upload order file → preview → confirm → deduct from inventory with SALE ledger entries |
| 3.4.3 | Order idempotency | Order ID tracking prevents double-deduction of same order |
| 3.4.4 | Committed stock | Orders pending dispatch reduce available stock before physical deduction |

---

**Phase 3 Definition of Done:**

- Reorder suggestions generated automatically from velocity data
- ABC classification guides cycle count frequency
- Pack-level inventory tracking enables accurate available-to-sell per pack type
- Marketplace sales deduct inventory automatically

---

## Implementation Principles

These apply across all phases and are non-negotiable:

**1. Ledger first, always.**
Every stock change — no matter how it originates — must produce a ledger entry before the operation is considered complete. Bulk imports, API calls, automated deductions, migrations. No exceptions.

**2. Schema migrations are not optional.**
Every schema change ships with a migration script that handles existing data. No silent column additions that leave legacy data intact but inconsistent.

**3. Validate before writing.**
Negative stock prevention, reorder point constraints, and supplier FK validation must be enforced in the service layer, not the UI. The UI can duplicate the validation for UX, but the service layer is the authority.

**4. Accuracy over features.**
A system that tracks two numbers accurately is more valuable than a system that tracks twenty numbers approximately. Do not add Phase 2 features until Phase 1 inventory numbers are trustworthy.

**5. No orphaned code.**
`add_stock()` and `transfer_to_shop()` were fixed but are not called from the UI. Either wire them into a UI path or remove them. Dead public methods with complex behaviour are maintenance traps.
