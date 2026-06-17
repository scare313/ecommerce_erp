# Supplier Product Code — Design Document

**Date:** 2026-06-10  
**Scope:** Internal ERP · SQLite · Streamlit · Single company · 1–10 users  
**Sprint target:** Phase 2.2 (Purchase Orders) prerequisite  
**Status:** Design only — no code changes

---

## Objective

### Problem being solved

Every supplier has their own internal code for each product they sell. Your internal SKU (`TSHIRT-BLK-L`) and the supplier's code for the same item (`RTS-2024-BLK-L`) are different identifiers for the same physical product. When placing purchase orders, receiving goods, or reconciling supplier invoices, you need the **supplier's code**, not yours.

Without a dedicated field, teams either:
- Manually look up the supplier's code each time they raise a PO
- Write it in freetext notes that cannot be queried or printed on documents
- Use the wrong code on POs, causing the supplier to reject or misprocess the order

### Real-world examples

| Internal SKU | Supplier | Supplier's Product Code | Context |
|---|---|---|---|
| `TSHIRT-BLK-L` | RAHUL-001 | `RTS-2024-BLK-L` | Rahul Textiles assigns their own part numbers |
| `JEANS-BLU-32` | VIKRAM-001 | `VK-JN-BLU-32W` | Vikram Knits uses a different naming scheme |
| `SOCKS-WHT-M` | RAHUL-001 | `RTS-ACC-SK-W` | Same supplier, different code per product |
| `CAP-RED-OS` | KAPIL-001 | `4521-R` | Kapil Accessories uses numeric codes |

The supplier's code is **product-specific** (not supplier-level) — the same supplier has a different code for each item they supply. It is a property of the product-supplier relationship, which in this system means it belongs on `product_master` alongside the existing `supplier_code` FK.

---

## Schema Changes

### Recommended column

```sql
ALTER TABLE product_master
ADD COLUMN supplier_product_code VARCHAR(100);
```

| Property | Value | Rationale |
|---|---|---|
| Column name | `supplier_product_code` | Unambiguous. Distinguishes it from `sku` (internal) and `supplier_code` (supplier FK). Searchable in code. |
| Data type | `VARCHAR(100)` | Matches `supplier_code` and `sku` column widths. 100 chars is sufficient for any real-world part number. Avoids over-engineering with TEXT. |
| Nullable | **Yes — NULL allowed** | Most existing products will not have this value at migration time. Forcing NOT NULL would require every product to have a supplier product code before the column can be added, which is impractical. NULL means "not yet assigned." |
| Default | `NULL` | No meaningful default is possible. An empty string default creates a false impression that the field has been filled. NULL is the honest state. |
| Uniqueness | **No unique constraint on the column alone** | Two different internal SKUs could share the same supplier product code (e.g., if two size variants map to one supplier bundle code). A unique constraint on `(supplier_code, supplier_product_code)` would be semantically correct but premature for this sprint — it prevents the NULL/migration state from working cleanly. |
| Index | **Not required now** | At 1–10 users and hundreds to low-thousands of SKUs, a full-table scan on this column is sub-millisecond in SQLite. An index adds maintenance cost with no perceptible benefit at this scale. Revisit at Sprint 3+. |
| FK constraint | **None** | This is a freetext code assigned by the supplier. It does not reference any table in this system. The supplier's catalog is an external system. |

### What is NOT added

| Field | Decision |
|---|---|
| `supplier_product_name` | Excluded by spec. The supplier's product name is either in `product_master.name` or irrelevant at this stage. Adding it creates sync obligations. |
| Supplier catalog table | Excluded by spec. One field on `product_master` is sufficient and avoids a new table relationship. |
| Supplier pricing table | Excluded by spec. Pricing belongs in Purchase Orders (Sprint 2.2+). |

---

## Migration Strategy

### Mechanism

`ALTER TABLE product_master ADD COLUMN supplier_product_code VARCHAR(100)` is an additive, non-destructive DDL operation. SQLite executes it instantly — it does not rewrite the table. All existing rows get `NULL` for the new column automatically.

This is added to `_apply_column_migrations()` in `init_db.py`, following the existing pattern used for `stock_ledger.reason_code` and `stock_ledger.updated_by`. It runs on every startup and is idempotent (SQLite raises `duplicate column name` when the column already exists; the existing error handler silently skips it).

```python
(
    "ALTER TABLE product_master ADD COLUMN "
    "supplier_product_code VARCHAR(100)",
    "product_master.supplier_product_code",
),
```

### Existing product behavior

| State | supplier_product_code value | Behavior |
|---|---|---|
| Product existed before this sprint | `NULL` | No change to any calculation or display. NULL is treated as "not assigned." |
| Product added after this sprint without filling the field | `NULL` | Same as above. Field is optional. |
| Product added after this sprint with the field filled | `'RTS-2024-BLK-L'` | Appears on purchase plans, future POs, and reports. |

No existing products are altered. No data loss is possible. The migration is a pure addition.

### Rollback

To roll back: SQLite does not support `DROP COLUMN` natively in versions before 3.35.0 (2021). For environments on SQLite ≥ 3.35.0, `ALTER TABLE product_master DROP COLUMN supplier_product_code` is possible. For older SQLite, rollback requires the table-rebuild approach (CREATE new table without the column, copy data, rename). In practice, since the column is nullable and additive, rollback is rarely necessary — simply leaving the column unpopulated is functionally equivalent to not having it.

---

## Data Manager Changes

### UI placement

The field belongs in the **Add New Product form** (Tab 1), grouped with the supplier selectbox. They are semantically linked: you assign a supplier and simultaneously record the supplier's code for that product.

Suggested layout:
```
[ Supplier * ]  (selectbox — existing)
[ Supplier Product Code ]  (text_input — new, optional)
  Caption: "The code this supplier uses for this item (e.g. RTS-2024-BLK-L)"
```

Placement immediately below the supplier selectbox makes the relationship clear without requiring explanation.

### Validation rules

| Rule | Behaviour |
|---|---|
| Optional | NULL/blank is accepted. Not every product has a known supplier code at creation time. |
| Strip whitespace | `supplier_product_code.strip()` before storing. Prevents accidental space-padded values. |
| Store as-is (no UPPER) | Supplier codes are case-sensitive in the supplier's system. Normalising to UPPER could create mismatches with the supplier's actual reference. |
| Max 100 characters | Enforced at the UI level (no explicit DB constraint needed — VARCHAR(100) in SQLite is advisory, not enforced, but the service layer trims to 100 before insert). |
| No FK validation | It is a freetext field. No lookup against any reference table. |

### Search and filter implications

At this scale (1–10 users, hundreds of SKUs), no dedicated search UI is required. The existing `st.data_editor` product grid displays all columns including `supplier_product_code`, which is sufficient for ad-hoc lookup. If the product list becomes large enough to need filtering by this field, a `st.text_input` filter above the grid is a one-line addition — deferred until the need arises.

### Edit path

The existing `st.data_editor + to_sql` bulk edit path exposes `supplier_product_code` automatically once the column exists. No additional edit UI is needed for this sprint. The dedicated row-level edit form (recommended in `DATA_MANAGER_EDIT_STRATEGY.md` for Sprint 2.2) will include `supplier_product_code` when it is built.

---

## Supplier Master Integration

`supplier_code` (on `product_master`) identifies **which supplier** supplies this product.  
`supplier_product_code` (on `product_master`) identifies **what the supplier calls this product**.

They work together but are independent fields:

| Scenario | supplier_code | supplier_product_code |
|---|---|---|
| Fully assigned product | `RAHUL-001` | `RTS-2024-BLK-L` |
| Supplier assigned, code unknown | `RAHUL-001` | `NULL` |
| Legacy product, neither assigned | `NULL` | `NULL` |
| Supplier code reassigned via bulk reassign | `VIKRAM-001` (changed) | `RTS-2024-BLK-L` (unchanged — still the old supplier's code, now stale) |

The last row identifies an important implication: **bulk reassignment does not update `supplier_product_code`**. When `bulk_reassign_and_deactivate()` moves products from supplier A to supplier B, the `supplier_product_code` values still reflect supplier A's coding scheme. The user must update them manually after reassignment. The Supplier Master bulk reassign success message should be updated to include: *"Note: supplier product codes were not updated — review them in Data Manager."*

There is no FK between `supplier_product_code` and any table. It is the supplier's reference, not a managed entity in this system.

---

## Demand Planner Changes

### Where it adds value

The Demand Planner generates a list of products to purchase, grouped by supplier. When the planner output is used to raise a purchase order with a supplier, the buyer needs the supplier's code — not the internal SKU — to communicate correctly.

**Add `supplier_product_code` to the Lead Times expander**, which already groups by supplier:

| Supplier | Supplier Code | Lead Time (days) | Lead Time Source |
|---|---|---|---|
| Rahul Textiles | RTS-2024-BLK-L | 7 | Supplier Master |
| Rahul Textiles | RTS-ACC-SK-W | 7 | Supplier Master |

This expander is already the "supplier intelligence" section of the demand planner. Adding the supplier product code here makes it the natural reference a buyer uses when calling or emailing the supplier.

### Where it does NOT add value

- **Main purchase plan table**: The plan shows one row per `base_sku`. Adding `supplier_product_code` here is redundant with the expander and adds visual noise. Keep it out of the main table.
- **Orphans tab**: Orphaned SKUs have no mapping — `supplier_product_code` is irrelevant there.
- **Metrics (Total Units, SKUs to Restock)**: Aggregated numbers; individual product codes have no role.

### Demand Planner column change (Lead Times expander)

Current expander columns: `supplier_name, supplier_code, lead_time_days, lead_time_source`  
Proposed expander columns: `supplier_name, supplier_product_code, lead_time_days, lead_time_source`

`supplier_code` (the FK, e.g. `RAHUL-001`) is less useful to a buyer mid-planning than `supplier_product_code` (e.g. `RTS-2024-BLK-L`). The supplier already knows who they are; they need the product reference. Replace `supplier_code` with `supplier_product_code` in the expander. `supplier_code` is still in the plan DataFrame for internal use.

---

## Future Purchase Order Support

Purchase Orders (Sprint 2.2) will generate a document sent to the supplier. The supplier reads their own product codes, not internal SKUs. `supplier_product_code` is the reference that makes the PO unambiguous.

### Sample PO line item structure

```
PURCHASE ORDER — PO-2026-001
To: Rahul Textiles (RAHUL-001)
Date: 2026-06-10

Line | Supplier Product Code | Your Description        | Qty  | Unit
-----|----------------------|-------------------------|------|-----
1    | RTS-2024-BLK-L       | Black T-Shirt Large     | 120  | pcs
2    | RTS-ACC-SK-W         | White Socks Medium      | 240  | pcs
3    | (not assigned)       | Blue Jeans 32W          | 60   | pcs
```

Line 3 shows the graceful degradation: when `supplier_product_code` is NULL, the internal SKU or product name is used as a fallback. The PO is still generated — it just uses less precise identification. The buyer can fill in the supplier code manually on the printed document.

### Receiving workflow

When goods arrive, the packing slip from the supplier lists their product codes. The receiver matches:
```
Packing slip: RTS-2024-BLK-L × 120 pcs
System lookup: supplier_product_code = 'RTS-2024-BLK-L' → base_sku = 'TSHIRT-BLK-L'
Action: add 120 units to godown stock for TSHIRT-BLK-L
```

Without `supplier_product_code`, this lookup requires the receiver to know the internal SKU mapping from memory or a separate reference. With it, receiving becomes a direct lookup.

### PO generation query

```sql
SELECT
    pm.supplier_product_code,
    pm.name         AS product_description,
    pm.sku          AS internal_sku,
    sm.name         AS supplier_name,
    sm.contact_email,
    po_lines.qty_ordered
FROM po_lines                                        -- Sprint 2.2 table
JOIN product_master pm ON po_lines.sku = pm.sku
JOIN supplier_master sm ON pm.supplier_code = sm.supplier_code
WHERE po_lines.po_id = :po_id
ORDER BY pm.supplier_product_code NULLS LAST
```

Products without `supplier_product_code` sort to the bottom so the buyer can handle them separately.

---

## Reporting Implications

### Reports that should display both fields

| Report | SKU | Supplier Product Code | Rationale |
|---|---|---|---|
| Purchase Plan (demand planner) | ✓ | ✓ (in expander) | Buyer reference during planning |
| Purchase Order document | ✓ (secondary) | ✓ (primary) | Supplier communication |
| Goods Received Note | ✓ | ✓ | Receiving reconciliation |
| Product Master export (bulk download) | ✓ | ✓ | Full data portability |
| Profit Dashboard | ✓ | ✗ | Cost/margin analysis; supplier code irrelevant |
| Inventory status | ✓ | ✗ | Stock levels; supplier code irrelevant |

### Placement convention

Wherever both fields appear together, `supplier_product_code` should follow `supplier_code` or `supplier_name`:

```
SKU | Supplier | Supplier Product Code | ...
```

Never place `supplier_product_code` before the supplier identity fields — it has no meaning without knowing which supplier assigned it.

---

## Acceptance Criteria

### Functional

**AC1 — Field exists and is nullable**  
`SELECT supplier_product_code FROM product_master` executes without error on a fresh install and on a migrated existing database. NULL is returned for products where no value has been assigned.

**AC2 — Add product with supplier product code**  
Entering `'RTS-2024-BLK-L'` in the Add New Product form stores exactly `'RTS-2024-BLK-L'` in `product_master.supplier_product_code` (whitespace stripped, case preserved).

**AC3 — Add product without supplier product code**  
Leaving the Supplier Product Code field blank stores NULL. No validation error is raised. The product is created successfully.

**AC4 — Demand Planner expander shows supplier product code**  
After plan generation, the Lead Times expander column `supplier_product_code` is populated for products where the field is set, and blank for products where it is NULL. No error occurs for either case.

**AC5 — Bulk reassign does not update supplier product code**  
After `bulk_reassign_and_deactivate(RAHUL-001, VIKRAM-001)`, products that were reassigned retain their original `supplier_product_code` values. The field is not cleared or overwritten.

### Data integrity

**AC6 — Whitespace stripped on write**  
Storing `'  RTS-2024-BLK-L  '` results in `'RTS-2024-BLK-L'` in the database.

**AC7 — Case preserved**  
Storing `'rts-2024-blk-l'` results in `'rts-2024-blk-l'` (not uppercased).

**AC8 — NULL and empty string treated consistently**  
The service layer stores blank input as NULL, not empty string. `SELECT COUNT(*) FROM product_master WHERE supplier_product_code = ''` returns 0 after any write via the service.

### Migration

**AC9 — Existing databases upgraded without data loss**  
An existing database with 500 products: after startup with the migration applied, all 500 rows have `supplier_product_code = NULL`. No rows are modified, deleted, or duplicated. All other columns are unchanged.

**AC10 — Migration is idempotent**  
Running startup twice does not raise an error. The second `ALTER TABLE` attempt is silently skipped by the existing `duplicate column name` handler.

**AC11 — Fresh install includes the column**  
`schema.sql` includes `supplier_product_code VARCHAR(100)` in the `product_master` CREATE TABLE statement so fresh installs do not depend on the migration path.

---

## Recommended Implementation Order

Each step is independently deployable and testable. No step requires the next one to be complete before it can ship.

| Step | File(s) | Action | Deliverable |
|---|---|---|---|
| 1 | `schema.sql` | Add `supplier_product_code VARCHAR(100)` to the `CREATE TABLE product_master` block | Fresh installs include the column |
| 2 | `init_db.py` | Add `ALTER TABLE product_master ADD COLUMN supplier_product_code VARCHAR(100)` to `_apply_column_migrations()` | Existing databases upgraded on next startup |
| 3 | `catalog_service.py` | Strip whitespace from `supplier_product_code` in `add_product()` before `_insert()`; store NULL when blank | Service layer enforces data quality |
| 4 | `data_manager.py` | Add optional `st.text_input("Supplier Product Code")` below the supplier selectbox in the Add New Product form; pass value to `add_product()` | Users can populate the field at product creation |
| 5 | `inventory_service.py` | Add `p.supplier_product_code` to `get_master_mapping()` SELECT; add to groupby keys; show in Lead Times expander in place of `supplier_code` | Demand Planner surfaces the field |
| 6 | `supplier_manager.py` | Update the bulk reassign success message to note that supplier product codes were not updated | User is informed of the stale-code risk |

Steps 1–2 are a paired deploy (schema + migration). Steps 3–4 are a paired deploy (service + UI). Step 5 is independent. Step 6 is a one-line copy change, lowest priority.

Steps 1–4 are sufficient for the field to be usable in production. Steps 5–6 are enhancements.

Purchase Order generation (Sprint 2.2) consumes `supplier_product_code` from `product_master` directly — no further changes to this field are required when POs are built.
