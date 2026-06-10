# Supplier Master — Implementation Plan

**Sprint:** Phase 2.1  
**Depends on:** Phase 1 complete and stable  
**Date:** 2026-06-10  
**Scope:** Internal ERP · SQLite · Streamlit · Single company · 1–10 users

---

## 1. Objectives

### 1.1 Primary Objectives

**O1 — Eliminate freetext supplier drift.**  
`product_master.supplier` is a free-text VARCHAR. "Rahul Textiles", "Rahul textile", and "R Textiles" are three phantom suppliers in `generate_purchase_plan()` grouping. The supplier must become a managed entity with a primary key.

**O2 — Per-supplier lead time in the Demand Planner.**  
`generate_purchase_plan()` currently accepts a single global `lead_time` integer from the sidebar. All suppliers get the same lead time regardless of their actual delivery performance. After this sprint, each supplier has its own `lead_time_days` that the planner uses automatically.

**O3 — Supplier CRUD via UI.**  
Users must be able to add, edit, and deactivate suppliers without touching the database directly. The UI must enforce uniqueness and prevent accidental deletion of suppliers with assigned products.

**O4 — Product-to-supplier assignment via FK.**  
Adding or editing a product in the Data Manager must require selecting from the supplier master, not typing a freetext name.

### 1.2 Out of Scope for This Sprint

- Purchase orders (Sprint 2.2)
- MOQ constraints per SKU (Sprint 2.2)
- Supplier performance metrics
- Supplier payment integration
- Per-supplier safety stock multiplier

---

## 2. Current State Analysis

### 2.1 What Exists Today

**`product_master` columns relevant to suppliers:**
```
supplier      VARCHAR(100)   -- freetext display name, no constraint
supplier_code VARCHAR(100)   -- freetext code, no FK, no constraint
```

Both columns are populated by `migration_script.py` from an Excel sheet.  
Note: `migration_script.py` line 156 has a typo in the rename map: `'suppier_code': 'supplier_code'` (missing 'l'). This means the supplier_code column is only populated if the Excel column header exactly matches `suppier_code`. Any Excel with the correctly-spelled header `supplier_code` would silently leave the DB column empty. This is a pre-existing data quality risk.

**`generate_purchase_plan()` in `inventory_service.py`:**  
Groups the purchase plan by `supplier` field from `product_master`. The `lead_time` it uses is `params.get('lead_time', 10)` — a single global integer passed from the sidebar. All supplier groups use the same lead time.

**`demand_planner.py` UI:**  
Line 42: `lead_time = st.sidebar.number_input("Supplier Lead Time", value=10, ...)` — single value, applied globally.

**`catalog_service.py` `add_product()`:**  
Calls `self._insert('product_master', data)` which does `INSERT OR REPLACE`. The `supplier` and `supplier_code` fields in `data` are whatever the UI provides — no validation against any reference table.

**No `supplier_master` table exists.** There is no managed supplier entity anywhere in the schema or service layer.

### 2.2 Risks in Current State

- Demand Planner produces separate supplier rows for the same supplier with different name spellings.
- A product can be assigned to a non-existent supplier code with no error.
- Lead time is guessed globally rather than reflecting actual supplier terms.
- There is no way to see which products belong to a given supplier without a manual SQL query.

---

## 3. Database Schema

### 3.1 New Table: `supplier_master`

```sql
CREATE TABLE IF NOT EXISTS supplier_master (
    supplier_code   VARCHAR(50)  PRIMARY KEY,
    name            VARCHAR(200) NOT NULL,
    contact_name    VARCHAR(100),
    contact_email   VARCHAR(200),
    contact_phone   VARCHAR(50),
    lead_time_days  INT          NOT NULL DEFAULT 10,
    min_order_qty   INT          NOT NULL DEFAULT 1,
    payment_terms   VARCHAR(100),
    notes           TEXT,
    is_active       INTEGER      NOT NULL DEFAULT 1,
    created_at      DATETIME     DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME     DEFAULT CURRENT_TIMESTAMP
);
```

**Column decisions:**

| Column | Rationale |
|---|---|
| `supplier_code` (PK) | Short unique identifier. Products reference this, not the display name. Case-insensitive comparison should be enforced in service code via `.upper().strip()`. |
| `name` | Human-readable display name. Shown in all UI dropdowns. NOT NULL — a supplier without a name is unusable. |
| `contact_name`, `contact_email`, `contact_phone` | Optional contact info. NULL is acceptable. Not validated in service layer beyond length. |
| `lead_time_days` | NOT NULL DEFAULT 10. The Demand Planner will use this per supplier. Must be >= 1. Validated in service layer. |
| `min_order_qty` | NOT NULL DEFAULT 1. Used for future PO validation. Must be >= 1. |
| `payment_terms` | Free text (e.g., "Net 30", "Advance"). Optional. |
| `notes` | Freetext scratch space. Optional. |
| `is_active` | INTEGER (SQLite has no BOOLEAN). 1 = active, 0 = deactivated. Deactivated suppliers appear in history but cannot be assigned to new products. Service layer filters `WHERE is_active = 1` for dropdowns. |
| `created_at`, `updated_at` | Audit timestamps. `updated_at` must be set to `CURRENT_TIMESTAMP` on every UPDATE — this must be done in the service layer since SQLite has no ON UPDATE trigger support. |

### 3.2 Modified Table: `product_master`

No columns are added or removed from `product_master`. The existing `supplier_code` column becomes a soft foreign key to `supplier_master.supplier_code`.

**Why not a hard FK constraint?**  
SQLite FK constraints are only enforced when `PRAGMA foreign_keys = ON` is set per connection. The existing codebase does not consistently set this pragma. Adding a hard FK constraint now would silently fail to enforce anything on existing connections. The FK is enforced at the **service layer** instead — both on product create and product edit.

The existing `supplier` (display name) column is retained as a denormalized display field. It is populated by the service layer automatically from `supplier_master.name` when `supplier_code` is assigned. This keeps existing queries that GROUP BY `supplier` working without a JOIN until `generate_purchase_plan()` is migrated to use the JOIN path.

### 3.3 Schema File Change

**File:** `src/infrastructure/schema.sql`

Add the `CREATE TABLE IF NOT EXISTS supplier_master` statement before the `product_master` definition, because `product_master` holds the FK reference. The schema currently drops and recreates `product_master` via `DROP TABLE IF EXISTS`. The supplier master table must be created first, and also dropped before product_master if schema is being rebuilt from scratch:

```
Insertion order in schema.sql:
  PRAGMA foreign_keys = OFF
  DROP TABLE IF EXISTS channel_listings
  DROP TABLE IF EXISTS pack_master
  DROP TABLE IF EXISTS product_master
  DROP TABLE IF EXISTS supplier_master       ← ADD HERE
  PRAGMA foreign_keys = ON
  CREATE TABLE supplier_master (...)          ← ADD HERE (before product_master)
  CREATE TABLE product_master (...)
  ...
```

---

## 4. Migrations Required

### 4.1 New Table Migration (all environments)

This migration must run at startup via `_apply_column_migrations()` (or a new peer function `_apply_table_migrations()`). The table creation is idempotent via `CREATE TABLE IF NOT EXISTS`.

**Migration step 1 — Create supplier_master table:**  
`CREATE TABLE IF NOT EXISTS supplier_master (...)` — full DDL as above. Safe to run on every startup. No data is touched.

### 4.2 Data Migration (existing databases only)

**Migration step 2 — Populate supplier_master from existing product_master data:**  

This is a one-time data migration that must be run only once. It should be wrapped in a guard: check whether `supplier_master` is empty before running. If it already has rows, skip.

Logic:
1. SELECT DISTINCT `supplier_code`, `supplier` FROM `product_master` WHERE `supplier_code` IS NOT NULL AND `supplier_code` != ''
2. For each distinct row: INSERT OR IGNORE INTO `supplier_master` (`supplier_code`, `name`) VALUES (upper(supplier_code), supplier_name)
3. Any products with NULL or blank `supplier_code` are left as-is. They will appear as "Unassigned" in the UI until manually corrected.

**Migration step 3 — Handle the typo in migration_script.py:**  
The existing `migration_script.py` maps `'suppier_code'` → `'supplier_code'`. This typo means any database populated from a correctly-spelled Excel column (`supplier_code`) has a NULL `supplier_code` in `product_master` for every row. The data migration in step 2 must handle this: products with NULL `supplier_code` but non-NULL `supplier` freetext should be flagged in a log warning, not silently skipped.

**Migration step 4 — Normalise casing:**  
`supplier_code` values from existing data may be mixed case ("RAHUL-001", "rahul-001"). The migration must UPPER() all existing `supplier_code` values before inserting into `supplier_master`. It must also UPDATE `product_master SET supplier_code = UPPER(supplier_code)` WHERE supplier_code IS NOT NULL.

### 4.3 Migration Implementation Location

The table creation migration goes in `init_db.py` — add a new function `_apply_table_migrations(engine)` called directly before `_apply_column_migrations(engine)` in both `_check_and_migrate_existing_db()` and `_create_new_database()`.

The data migration (step 2–4) goes in a new function `_seed_supplier_master_from_products(engine)` called after `_apply_table_migrations()`. It must be idempotent: check `SELECT COUNT(*) FROM supplier_master` before executing. If > 0, log and skip.

---

## 5. New Files

### 5.1 `src/core/services/supplier_service.py`

A new service class `SupplierService` following the same pattern as `CatalogService` and `InventoryService`.

**Methods required:**

`get_all_suppliers(active_only=True) → DataFrame`  
Returns all supplier_master rows. `active_only=True` adds `WHERE is_active = 1`. Columns: supplier_code, name, contact_name, contact_email, contact_phone, lead_time_days, min_order_qty, payment_terms, notes, is_active. Used to populate the UI supplier list and management table.

`get_supplier_dropdown() → list[tuple]`  
Returns `[(supplier_code, display_label), ...]` where display_label is `"{supplier_code} — {name}"`. Filtered to active only. Used in product add/edit forms. Returns empty list if table has no active suppliers.

`add_supplier(data: dict) → None`  
Validates: `supplier_code` not empty, `name` not empty, `lead_time_days` >= 1, `min_order_qty` >= 1. Normalises `supplier_code` to UPPER().strip(). Raises `DataValidationException` on validation failure. Raises `DataValidationException` with message "Supplier code already exists" on UNIQUE constraint violation. Uses `engine.begin()` for the INSERT.

`update_supplier(supplier_code: str, data: dict) → None`  
Validates same rules as `add_supplier`. Does NOT allow changing `supplier_code` (PK immutable). Updates `updated_at = CURRENT_TIMESTAMP` explicitly. Propagates name change to `product_master.supplier` WHERE `product_master.supplier_code = :supplier_code` in the same transaction. Uses `engine.begin()`.

`deactivate_supplier(supplier_code: str) → None`  
Sets `is_active = 0`, `updated_at = CURRENT_TIMESTAMP`. Does NOT delete. Before deactivating, checks: `SELECT COUNT(*) FROM product_master WHERE supplier_code = :code`. If > 0, raises `DataValidationException` with message: "Cannot deactivate: N products are assigned to this supplier. Reassign them first." Uses `engine.begin()`.

`get_supplier_by_code(supplier_code: str) → dict | None`  
Returns single supplier row as dict, or None if not found. Used to pre-populate edit form.

`get_products_for_supplier(supplier_code: str) → DataFrame`  
Returns all products assigned to a supplier. Columns: sku, name, category. Used in supplier detail view to show product list before deactivation.

`get_lead_times_map() → dict`  
Returns `{supplier_code: lead_time_days, ...}` for all active suppliers. Used by `generate_purchase_plan()` to look up lead times without a JOIN on every row.

### 5.2 `src/ui/pages/supplier_manager.py`

New Streamlit page with a `render()` function following the same pattern as `inventory_manager.py`.

Page title: "🏭 Supplier Master"

Tabs:
- Tab 1: "📋 All Suppliers" — supplier list table
- Tab 2: "➕ Add Supplier" — add form
- Tab 3: "✏️ Edit / Deactivate" — edit and deactivate workflow

---

## 6. Files to Modify

### 6.1 `src/infrastructure/schema.sql`

**Change 1:** Add `DROP TABLE IF EXISTS supplier_master` before `DROP TABLE IF EXISTS product_master`.  
**Change 2:** Add `CREATE TABLE IF NOT EXISTS supplier_master (...)` before `CREATE TABLE product_master`.  

No other schema.sql changes. `product_master` columns are unchanged.

### 6.2 `src/infrastructure/init_db.py`

**Change 1:** Add `_apply_table_migrations(engine)` function. Contains:
- `CREATE TABLE IF NOT EXISTS supplier_master (...)` — idempotent, safe to run every startup.

**Change 2:** Add `_seed_supplier_master_from_products(engine)` function. Contains:
- Guard check: `SELECT COUNT(*) FROM supplier_master` — if > 0, log and return.
- Data migration logic: SELECT DISTINCT from product_master, INSERT OR IGNORE into supplier_master.
- Casing normalisation: UPDATE product_master SET supplier_code = UPPER(supplier_code).

**Change 3:** Call order in `_check_and_migrate_existing_db()`:
```
1. _apply_table_migrations(engine)      ← new
2. _apply_column_migrations(engine)     ← existing
3. _seed_supplier_master_from_products(engine) ← new
4. _check_and_migrate_existing_db() existing logic
```

**Change 4:** Call order in `_create_new_database()`:
```
After schema execution, call _apply_table_migrations(engine) to ensure
supplier_master exists even if CREATE TABLE in schema.sql was already in
a CREATE TABLE IF NOT EXISTS form.
```

**Import change:** Add `from sqlalchemy.exc import OperationalError` is already present after Defect 3 fix. No further imports needed.

### 6.3 `src/core/services/inventory_service.py`

**Change to `generate_purchase_plan()`:**  

Currently at step 6 "Inventory Math", `lead_time` comes from `params.get('lead_time', 10)` and is applied uniformly to all supplier groups.

After the change:
- After step 5 (grouping by base_sku/supplier), merge `supplier_master.lead_time_days` into the plan DataFrame using a JOIN on `supplier` name or `supplier_code`.
- The JOIN must be on `product_master.supplier_code` → `supplier_master.supplier_code`, not on the freetext `supplier` name. This requires the `get_master_mapping()` query to also return `supplier_code`.
- For any supplier group that does NOT match a `supplier_master` record (e.g., legacy freetext supplier with no code), fall back to `params.get('lead_time', 10)`.
- The `lead_time_demand` calculation becomes per-row: `plan['lead_time_demand'] = plan['ads'] * plan['effective_lead_time']` where `effective_lead_time` is the merged supplier value with fallback.

**Change to `get_master_mapping()`:**  
Add `p.supplier_code` to the SELECT list so it flows through to the purchase plan grouping.

**Import change:** `SupplierService` is NOT imported here. The lead time lookup is done via a direct SQL JOIN or via `pd.merge()` with the result of a SQL query — not by calling SupplierService methods. This avoids a circular import and keeps the data access in one place.

### 6.4 `src/ui/pages/demand_planner.py`

**Change 1:** The global `lead_time` sidebar input is retained but relabelled to "Default Lead Time (days)" with updated help text: "Used for suppliers not in the Supplier Master."

**Change 2:** After the plan is generated and displayed, add an informational expander "📊 Lead Times Used" that shows a DataFrame of supplier_name → effective_lead_time. This makes it transparent which suppliers used their own lead time vs the global fallback. This data must be returned from `generate_purchase_plan()` as a third return value (currently returns `(plan, orphans)`; change to `(plan, orphans, lead_time_used_df)`).

**Change 3:** No changes to file uploaders or planning parameters beyond the above.

### 6.5 `src/core/services/catalog_service.py`

**Change to `add_product()`:**  
Before calling `self._insert('product_master', data)`, validate `supplier_code`:
1. If `supplier_code` is not None and not empty: query `SELECT COUNT(*) FROM supplier_master WHERE supplier_code = :code AND is_active = 1`.
2. If count == 0: raise `DataValidationException("Supplier code '{code}' does not exist in Supplier Master. Add the supplier first.")`.
3. If validation passes: also set `data['supplier']` to the supplier's `name` from `supplier_master` — so the denormalized `supplier` column stays in sync automatically.

**Change to `_insert()`:**  
No change. The validation is done in `add_product()` before reaching `_insert()`.

**New method `update_product_supplier(sku, supplier_code)`:**  
For future use by the supplier edit workflow. Sets both `supplier_code` and `supplier` (denormalized name) on a product. Validates the supplier_code exists and is active before writing. Uses `engine.begin()`.

### 6.6 `src/ui/pages/data_manager.py`

**Change to the product add/edit form:**  
The `supplier` text input and `supplier_code` text input are replaced with a single selectbox: "Supplier" populated from `SupplierService.get_supplier_dropdown()`. The selected value sets `supplier_code` in the submitted data dict. The `supplier` (display name) field is set automatically from the selection — not user-entered.

If `SupplierService.get_supplier_dropdown()` returns an empty list (no suppliers yet), show an `st.warning("No suppliers found. Please add suppliers in the Supplier Master page before adding products.")` and disable the form submit button.

### 6.7 Application Entry Point / Navigation

**File:** Wherever page navigation is registered (likely `app.py` or the main Streamlit entry point — not yet read, but follows the same pattern as other pages).

Add `supplier_manager.render` to the navigation alongside `inventory_manager.render`, `demand_planner.render`, etc.

---

## 7. UI Changes

### 7.1 Supplier Manager Page — Tab 1: All Suppliers

Displays `service.get_all_suppliers(active_only=False)` as a styled DataFrame.

Columns shown: Supplier Code, Name, Contact Name, Lead Time (days), Min Order Qty, Payment Terms, Status (Active / Inactive).

Inactive suppliers are shown with greyed-out row styling (via `pandas Styler.apply` on `is_active` column).

No edit controls on this tab — Tab 3 handles editing. Tab 1 is read-only.

Include a row count: "Showing N suppliers (M active, K inactive)."

### 7.2 Supplier Manager Page — Tab 2: Add Supplier

A single `st.form("add_supplier_form")` containing:

| Field | Widget | Validation |
|---|---|---|
| Supplier Code | `text_input`, required | Non-empty after strip. UPPER applied in service. Max 50 chars. |
| Supplier Name | `text_input`, required | Non-empty after strip. Max 200 chars. |
| Contact Name | `text_input`, optional | — |
| Contact Email | `text_input`, optional | No format validation (overkill for 1–10 users) |
| Contact Phone | `text_input`, optional | — |
| Lead Time (Days) | `number_input`, min=1, default=10, step=1 | >= 1 enforced in service |
| Min Order Qty | `number_input`, min=1, default=1, step=1 | >= 1 enforced in service |
| Payment Terms | `text_input`, optional | e.g., "Net 30", "Advance" |
| Notes | `text_area`, optional | — |

Submit button: "Add Supplier"

On success: `st.success("✅ Supplier {code} — {name} added.")` + `st.rerun()`.  
On `DataValidationException`: `st.error(str(e))` (no rerun — let user correct the form).

### 7.3 Supplier Manager Page — Tab 3: Edit / Deactivate

Split into two sub-sections using `st.radio` or two `st.expander` blocks:

**Sub-section A: Edit Supplier**

- Supplier selectbox outside the form (key: `"tab3_edit_supplier_code"`), populated from `get_all_suppliers(active_only=False)` — all suppliers, including inactive, can be edited.
- On selection: pre-populate form fields from `get_supplier_by_code(selected_code)`.
- Form `"edit_supplier_form"` contains same fields as Add (except Supplier Code is displayed as `st.text("[immutable]")` — not editable). Submit button: "Save Changes".
- On success: `st.success("✅ {code} updated.")` + `st.rerun()`.

**Sub-section B: Deactivate Supplier**

- Separate selectbox (key: `"tab3_deactivate_supplier_code"`), active suppliers only.
- On selection: show `get_products_for_supplier(code)` as a table — "The following N products are assigned to this supplier."
- If any products are assigned: show `st.error("Cannot deactivate: N products are assigned. Reassign them first.")` and disable the deactivate button.
- If no products assigned: show `st.warning("⚠️ This will deactivate {code}. It will no longer appear in product assignment dropdowns.")` + a `st.form("deactivate_form")` with a single `st.checkbox("I confirm I want to deactivate this supplier")` and "Deactivate" button. Checkbox must be checked to enable submit.

### 7.4 Demand Planner UI Changes

The sidebar `lead_time` number_input is relabelled: "Default Lead Time (days)" with help text "Fallback for suppliers not in Supplier Master."

After plan results are displayed (after the existing metrics section), add:
```
st.expander("📊 Lead Times Applied per Supplier")
  → DataFrame: Supplier Name | Supplier Code | Lead Time Used | Source (Master / Default)
```

This provides traceability — the user can see exactly which suppliers used master data vs the global fallback.

### 7.5 Data Manager Product Form Changes

The existing separate "Supplier" and "Supplier Code" text inputs in the product add form are replaced by:
- A single `st.selectbox("Supplier", options=supplier_dropdown, ...)` where options are `["{code} — {name}", ...]`.
- Hint text: "Add suppliers in the Supplier Master page if none appear here."

If supplier_master is empty: `st.warning` + disabled submit. This forces users to create suppliers before products, establishing the master data hierarchy.

---

## 8. Service Layer Changes — Detail

### 8.1 `generate_purchase_plan()` lead time merge logic

**Current:**
```
plan['lead_time_demand'] = plan['ads'] * lead_time
  where lead_time = params.get('lead_time', 10)  -- single global value
```

**After:**
```
Step 5.5 (new, after groupby, before inventory math):
  - Query: SELECT supplier_code, lead_time_days FROM supplier_master WHERE is_active = 1
  - Result → lead_time_df (DataFrame)
  - The plan DataFrame has a 'supplier_code' column from the updated get_master_mapping() query
  - Merge: plan = pd.merge(plan, lead_time_df, on='supplier_code', how='left')
  - Fill missing: plan['lead_time_days'] = plan['lead_time_days'].fillna(params.get('lead_time', 10))
  - plan['lead_time_demand'] = plan['ads'] * plan['lead_time_days']
```

The `lead_time_used_df` returned to the UI is:  
`plan[['supplier', 'supplier_code', 'lead_time_days']].drop_duplicates()` with a computed column `source`: "Supplier Master" where the merge found a match, "Default (fallback)" where `fillna()` was applied.

Return signature changes from `(plan, orphans)` to `(plan, orphans, lead_time_used_df)`.

**`demand_planner.py` must be updated to unpack three values:** `plan_df, orphans_df, lt_df = service.generate_purchase_plan(files, params)`.

### 8.2 `get_master_mapping()` query change

Add `p.supplier_code` to the SELECT:
```sql
SELECT
    cl.marketplace,
    cl.channel_sku,
    pm.pack_sku,
    pm.quantity      AS pack_qty,
    pm.master_sku    AS base_sku,
    p.name           AS product_name,
    p.supplier,
    p.supplier_code,   ← ADD THIS
    p.category
FROM channel_listings cl
JOIN pack_master pm ON cl.internal_sku = pm.pack_sku
JOIN product_master p ON pm.master_sku = p.sku
```

This flows `supplier_code` through the merge chain so the plan DataFrame has it available for the lead_time JOIN.

### 8.3 Transaction patterns

All `SupplierService` write methods use `engine.begin()`. The `update_supplier()` method must update both `supplier_master` and `product_master.supplier` (denorm name sync) in the **same transaction** so they never diverge if the connection drops mid-write.

---

## 9. Integration Points

### 9.1 Demand Planner ↔ supplier_master

**Join path:** `generate_purchase_plan()` → `get_master_mapping()` returns `supplier_code` → merged with `supplier_master.lead_time_days` in step 5.5.

**Fallback behaviour:** Products with no `supplier_code` (legacy data before migration) have NULL after the merge. `fillna(params.get('lead_time', 10))` ensures they still produce a valid plan using the global default.

**Impact if supplier_master is empty:** All products use the global default lead time. Behaviour is identical to today. The feature degrades gracefully.

### 9.2 Data Manager ↔ supplier_master

**Join path:** Product add/edit form → `SupplierService.get_supplier_dropdown()` populates selectbox → selected `supplier_code` passed to `CatalogService.add_product()` → validated against `supplier_master` before INSERT.

**Impact if supplier_master is empty:** Data Manager shows warning. Product add is blocked. This is an intentional forcing function — create suppliers first.

**Edge case:** If a product was created before supplier_master existed (legacy data), its `supplier_code` may not match any supplier_master record. The edit form should not crash in this case. The selectbox should default to the closest match if one exists, or show "— Unassigned —" as the first option if no match is found. Saving the form with "Unassigned" clears `supplier_code` to NULL without raising a validation error (NULL is allowed; empty string is treated as NULL).

### 9.3 Supplier edit name change ↔ product_master.supplier

When `update_supplier()` changes the `name` field, it must also execute:
```sql
UPDATE product_master SET supplier = :new_name WHERE supplier_code = :code
```
in the same transaction. This keeps the denormalized `supplier` column in sync with the master. If this UPDATE is not done, the Demand Planner grouping (which groups by `supplier` freetext) will show the old name for existing products until they are individually re-saved.

### 9.4 Supplier deactivate ↔ product_master

Deactivation does not change any product_master records. The deactivated supplier's products remain assigned to it. The products will still appear in inventory and demand planning with the historical supplier name. The only effect is that the supplier no longer appears in add/edit dropdowns — preventing new assignments.

If a user tries to edit a product that has a deactivated supplier code, the edit form must show the deactivated supplier in the selectbox with a label like "(Inactive) SUPPLIER_CODE — Name" so the user knows re-assignment is needed.

### 9.5 `migration_script.py` typo ↔ supplier_master seeding

The typo `'suppier_code': 'supplier_code'` means that on an Excel-imported database, `product_master.supplier_code` may be NULL for all rows even though the Excel had a `supplier_code` column. The data migration `_seed_supplier_master_from_products()` handles this:
- If `supplier_code` is NULL but `supplier` (freetext) is not NULL: log a WARNING per product listing the SKU and the freetext supplier name. These must be resolved manually.
- Do not auto-generate supplier codes from freetext names — this would create unpredictable codes.
- The typo in `migration_script.py` must also be fixed as a separate item (outside this sprint's scope but documented here as a dependency).

---

## 10. Risks

### R1 — Existing products have no valid supplier_code

**Risk:** The data migration seeding `supplier_master` from `product_master` may find many NULL or blank `supplier_code` values (due to the `suppier_code` typo or simply because the field was never populated). After migration, those products cannot be reassigned in the Data Manager edit form until a supplier is created and manually assigned.

**Severity:** Medium. Demand Planner continues to work (falls back to global lead time). Product editing is blocked until supplier is assigned.

**Mitigation:** `_seed_supplier_master_from_products()` logs every product with NULL supplier_code as a WARNING so the user knows exactly which products need attention. The UI should display an "Unassigned" option in the supplier selectbox for these products rather than crashing.

### R2 — Demand Planner return signature change breaks calling code

**Risk:** `generate_purchase_plan()` currently returns `(plan, orphans)`. Changing it to `(plan, orphans, lead_time_used_df)` will cause a `ValueError: too many values to unpack` in `demand_planner.py` until that file is also updated.

**Severity:** High. Application crash on every Demand Planner use.

**Mitigation:** The service change and the UI change must be implemented in the same commit. This is the highest-risk breaking change in this sprint. Verify both files after implementation.

### R3 — Supplier dropdown is empty on first use

**Risk:** If a fresh install adds products before any suppliers exist, the product add form is blocked. The user has no context for why unless the warning is clear.

**Severity:** Low. Warning message in the UI addresses this. The workflow is supplier → product, which is the correct order.

**Mitigation:** The `st.warning` in the Data Manager product form must explicitly name the Supplier Master page: "Go to **Supplier Master** to add suppliers before adding products."

### R4 — SQLite FK not enforced at DB level

**Risk:** Because the application does not set `PRAGMA foreign_keys = ON` consistently, a direct database edit or a future code path that bypasses `CatalogService` can insert a product with a non-existent `supplier_code`.

**Severity:** Low in a 1–10 user environment with no external integrations.

**Mitigation:** Service-layer validation in `add_product()` and `update_product_supplier()` is the authority. Document this limitation in code comments. Consider adding `PRAGMA foreign_keys = ON` to `database.py` `get_engine()` as a future hardening task — but that is out of scope for this sprint as it could introduce FK violations from legacy data.

### R5 — Supplier name change creates a brief window of inconsistency

**Risk:** If `update_supplier()` updates `supplier_master.name` and then `product_master.supplier`, but the application reads `product_master.supplier` between those two operations, it will see the old name.

**Severity:** Negligible. SQLite with `engine.begin()` executes both UPDATEs in a single transaction. No other connection can read between them in SQLite's default serialized write mode.

**Mitigation:** Already handled by the single `engine.begin()` transaction requirement for `update_supplier()`.

### R6 — supplier_code is PK and immutable

**Risk:** If a user enters "RAHUL-001" but meant "RAHUL001", the only fix is deactivation and recreation. Existing products still reference the old code.

**Severity:** Low. This is standard master data management behaviour. Deactivation + recreation is the correct workflow.

**Mitigation:** The Add Supplier form shows a preview of the normalised supplier_code (UPPER + strip) before submission. Add a `st.caption(f"Will be stored as: {input.upper().strip()}")` below the input field.

---

## 11. Acceptance Criteria

### AC1 — supplier_master table exists after any startup

On both a fresh install and an existing database, `supplier_master` exists after application startup with all required columns. Verified by: startup completes without error, `SELECT * FROM supplier_master` executes without "no such table" error.

### AC2 — Data migration populates supplier_master from existing products

On an existing database where `product_master` has products with `supplier_code` values, running startup once populates `supplier_master` with distinct supplier codes. Running startup a second time does not insert duplicates (idempotent guard). Verified by: row count before and after second startup is unchanged.

### AC3 — Add supplier enforces uniqueness and normalisation

Attempting to add a supplier with code "rahul-001" when "RAHUL-001" already exists raises a `DataValidationException` visible in the UI. The stored `supplier_code` is always uppercase. Verified by: attempting duplicate add and observing error message.

### AC4 — Deactivate supplier blocked when products assigned

Attempting to deactivate a supplier with one or more products assigned shows an error listing the product count and does not change `is_active`. Verified by: deactivation attempt + `SELECT is_active FROM supplier_master WHERE supplier_code = :code` returns 1.

### AC5 — Deactivate supplier succeeds when no products assigned

A supplier with zero assigned products can be deactivated. After deactivation: `is_active = 0`, supplier no longer appears in add/edit product dropdowns, supplier still appears in "All Suppliers" tab with "Inactive" status.

### AC6 — Demand Planner uses per-supplier lead time

Given: supplier "RAHUL-001" has `lead_time_days = 7` in supplier_master, and supplier "VIKRAM-KNIT" has `lead_time_days = 15`. When a purchase plan is generated, the plan rows for RAHUL-001 use lead_time = 7 and rows for VIKRAM-KNIT use lead_time = 15, regardless of the global sidebar value. Verified by: inspect `lead_time_days` column on plan DataFrame or "Lead Times Applied" expander.

### AC7 — Global lead time is used as fallback for unmatched suppliers

Given: a product with `supplier_code = NULL` or a supplier code not in `supplier_master`. When a purchase plan is generated, that product's plan row uses `params['lead_time']` (global sidebar value). No crash or missing row occurs. Verified by: check `lead_time_used_df` source column shows "Default (fallback)" for those rows.

### AC8 — Supplier name change propagates to product_master

After editing a supplier's name in the Edit tab, `product_master.supplier` for all products with that `supplier_code` reflects the new name immediately. Verified by: edit name → query `SELECT supplier FROM product_master WHERE supplier_code = :code` returns new name.

### AC9 — Product add requires valid supplier

Attempting to add a product in Data Manager with a supplier_code not in `supplier_master` raises a `DataValidationException` and no row is inserted into `product_master`. Verified by: `SELECT COUNT(*) FROM product_master` before and after failed attempt is unchanged.

### AC10 — Demand Planner return value unpacking does not break

After the signature change to `(plan, orphans, lead_time_used_df)`, the Demand Planner page renders without error and the lead times expander appears. Verified by: full end-to-end Demand Planner run with at least one sales file.

---

## 12. Test Cases

### T1 — Fresh install: supplier_master created automatically

**Steps:**
1. Delete the SQLite database file.
2. Start the application.
3. Navigate to any page.

**Expected:** Application starts without error. `supplier_master` table exists. Table is empty (no products to seed from).

---

### T2 — Existing database: supplier_master seeded from product_master

**Steps:**
1. Use an existing database with 10 products having distinct supplier_codes: "RAHUL-001" (3 products), "VIKRAM-001" (5 products), "MEHTA-02" (2 products).
2. Start the application.

**Expected:**
- `supplier_master` contains exactly 3 rows.
- `supplier_code` values are UPPER-cased.
- `name` field is populated from the corresponding `product_master.supplier` freetext.
- `lead_time_days` defaults to 10 for all (no source data for lead time).
- Second startup: row count still 3.

---

### T3 — Seeding skipped if supplier_master already populated

**Steps:**
1. Add one supplier manually via the Supplier Manager UI.
2. Restart the application.

**Expected:** The existing supplier_master row is not overwritten or duplicated. Log shows "Migration skipped: supplier_master already seeded."

---

### T4 — Add supplier: success

**Steps:**
1. Navigate to Supplier Master → Add Supplier tab.
2. Enter: Code = "test-supplier-01", Name = "Test Supplier One", Lead Time = 14, Min Order Qty = 5.
3. Submit.

**Expected:**
- Success message shown.
- Row appears in All Suppliers tab.
- `supplier_code` stored as "TEST-SUPPLIER-01" (uppercased).
- `lead_time_days = 14`, `min_order_qty = 5`.
- `is_active = 1`.

---

### T5 — Add supplier: duplicate code rejected

**Steps:**
1. Add supplier with code "TEST-01".
2. Attempt to add another with code "test-01" (same, different case).

**Expected:** Error message: "Supplier code already exists." No duplicate row inserted.

---

### T6 — Add supplier: validation failures

**Sub-cases:**
- Empty supplier_code → error "Supplier code is required."
- Empty name → error "Supplier name is required."
- lead_time_days = 0 → error "Lead time must be at least 1 day."
- min_order_qty = 0 → error "Minimum order quantity must be at least 1."

**Expected:** Each sub-case shows the corresponding error. No row inserted.

---

### T7 — Edit supplier: name propagates to product_master

**Steps:**
1. Supplier "RAHUL-001" has name "Rahul Textiles". Three products assigned.
2. Edit name to "Rahul Fabrics Ltd".
3. Submit.

**Expected:**
- `supplier_master.name = "Rahul Fabrics Ltd"` for `supplier_code = "RAHUL-001"`.
- All three products in `product_master` show `supplier = "Rahul Fabrics Ltd"`.
- No other products affected.

---

### T8 — Edit supplier: lead_time_days change

**Steps:**
1. Supplier "VIKRAM-001" has `lead_time_days = 10`.
2. Edit to `lead_time_days = 21`.
3. Submit.
4. Generate a Demand Planner purchase plan.

**Expected:** Plan rows for VIKRAM-001 products show `lead_time_days = 21`. The global sidebar "Default Lead Time" value does not override this.

---

### T9 — Deactivate supplier: blocked when products assigned

**Steps:**
1. Supplier "RAHUL-001" has 3 products assigned.
2. Navigate to Edit / Deactivate tab.
3. Select "RAHUL-001" in the deactivate selectbox.

**Expected:**
- Table shows 3 assigned products.
- Deactivate button is disabled (or shows error if clicked).
- `is_active` remains 1 in the database.

---

### T10 — Deactivate supplier: succeeds when unassigned

**Steps:**
1. Create supplier "TEMP-001" with no products.
2. Navigate to deactivate section, select "TEMP-001".
3. Check confirmation checkbox, click "Deactivate".

**Expected:**
- Success message shown.
- `is_active = 0` in database.
- "TEMP-001" no longer appears in add/edit product supplier dropdown.
- "TEMP-001" still appears in All Suppliers tab with "Inactive" status.

---

### T11 — Add product: supplier validation enforced

**Steps:**
1. Attempt to add a product via Data Manager with supplier_code "DOES-NOT-EXIST".

**Expected:** `DataValidationException` raised. No row in `product_master`. Error shown in UI: "Supplier code 'DOES-NOT-EXIST' does not exist in Supplier Master."

---

### T12 — Add product: supplier dropdown populated correctly

**Steps:**
1. supplier_master has 3 active suppliers.
2. Open product add form in Data Manager.

**Expected:** Supplier selectbox shows 3 options. No freetext input. Selecting any option sets both `supplier_code` and `supplier` (name) on submission.

---

### T13 — Add product: blocked when supplier_master empty

**Steps:**
1. supplier_master is empty (fresh install, no suppliers created yet).
2. Open product add form in Data Manager.

**Expected:** Warning shown: "No suppliers found. Add suppliers in Supplier Master first." Submit button disabled.

---

### T14 — Demand Planner: per-supplier lead time applied

**Setup:**
- "RAHUL-001": `lead_time_days = 7`
- "VIKRAM-001": `lead_time_days = 21`
- Sidebar "Default Lead Time" = 10
- Products assigned: 5 to RAHUL-001, 3 to VIKRAM-001, 2 with NULL supplier_code

**Steps:**
1. Upload sales files with demand for all 10 products.
2. Generate plan.

**Expected:**
- RAHUL-001 plan rows: `lead_time_days = 7`
- VIKRAM-001 plan rows: `lead_time_days = 21`
- NULL supplier_code rows: `lead_time_days = 10` (global fallback)
- "Lead Times Applied" expander shows all three rows with correct source labels.

---

### T15 — Demand Planner: return value unpack does not crash

**Steps:**
1. Upload one sales file.
2. Click "Generate Purchase Plan".

**Expected:** Page renders completely, including the "Lead Times Applied" expander. No `ValueError: too many values to unpack` traceback.

---

### T16 — All Suppliers tab: active and inactive both shown

**Steps:**
1. Have 2 active suppliers and 1 deactivated supplier.
2. Navigate to All Suppliers tab.

**Expected:** Table shows 3 rows. Deactivated row has "Inactive" in status column. Active rows have "Active".

---

## 13. Implementation Order

This sequence minimises risk by completing the schema and service layer before any UI touches the new data.

| Step | Action | Reason |
|---|---|---|
| 1 | `schema.sql` — add supplier_master CREATE TABLE | Foundation. Must exist before migration runs. |
| 2 | `init_db.py` — `_apply_table_migrations()` | Ensures table is created at startup for existing DBs. |
| 3 | `init_db.py` — `_seed_supplier_master_from_products()` | Populates master from existing product data. |
| 4 | `supplier_service.py` — all methods | Service layer before any UI depends on it. |
| 5 | `supplier_manager.py` — full page | CRUD UI. Allows manual supplier data entry and verification. |
| 6 | Navigation — add supplier_manager to app | Makes the page accessible. |
| 7 | `catalog_service.py` — `add_product()` validation | Must be done before Data Manager UI change. If done after, the UI sends supplier_code but the service doesn't validate it yet. |
| 8 | `data_manager.py` — supplier selectbox | UI change. Service already enforces the rule. |
| 9 | `inventory_service.py` — `get_master_mapping()` + `generate_purchase_plan()` | Lead time logic. Service change first. |
| 10 | `demand_planner.py` — unpack 3 return values + expander | Must be done **in same commit as step 9** to avoid unpacking crash. |

Steps 9 and 10 are the only pair that must be deployed atomically. All other steps are independent and can be validated individually.
