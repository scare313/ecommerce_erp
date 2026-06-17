# Supplier Master — Implementation Plan V2

**Sprint:** Phase 2.1  
**Depends on:** Phase 1 complete and stable  
**Supersedes:** SUPPLIER_MASTER_IMPLEMENTATION_PLAN.md  
**Review applied:** SUPPLIER_MASTER_REVIEW.md  
**Date:** 2026-06-10  
**Scope:** Internal ERP · SQLite · Streamlit · Single company · 1–10 users

---

## Changes from V1

All four required changes from the review have been applied. Recommended improvements have been evaluated individually below.

| Review Issue | V1 Problem | V2 Decision |
|---|---|---|
| R1 — Seeding guard broken | `COUNT(*) > 0` guard fails when all products have NULL supplier_code | **Required — Fixed.** Dedicated `schema_migrations` tracking table. Guard is now independent of seeded row count. |
| R3 — Return signature change | Added 3rd return value for informational expander, breaking change risk | **Required — Fixed.** `lead_time_source` added as column in `plan_df`. Return remains `(plan, orphans)`. |
| R4 — Streamlit antipatterns | `st.caption` inside form (stale), confirmation checkbox inside form (non-functional) | **Required — Fixed.** `supplier_code` input outside form. Checkbox outside form. |
| R10 — migration_script.py typo deferred | Feature breaks on any catalog reimport without the fix | **Required — Fixed.** Typo fix is Step 1 of implementation order. |
| R2 — Denorm sync unnecessary | `update_supplier()` syncing `product_master.supplier` for a query being migrated away from it | **Recommended — Accepted.** Sync removed. `generate_purchase_plan()` gets display name from supplier_master JOIN. |
| R5 — Dead methods | `get_lead_times_map()` and `update_product_supplier()` have no callers in this sprint | **Recommended — Accepted.** Both removed from spec. |
| R6 — VARCHAR mismatch | `supplier_code` VARCHAR(50) in new table vs VARCHAR(100) in existing table | **Recommended — Accepted.** Both aligned to VARCHAR(100). |
| R7 — min_order_qty premature | No consumer until Sprint 2.2, risks semantic conflict with per-SKU MOQ | **Recommended — Accepted.** Removed from this sprint entirely. |
| R8 — Bulk reassign missing | Deactivation with products assigned has no path other than one-by-one editing | **Recommended — Accepted.** Bulk reassign added to deactivation workflow. |
| R9 — Two selectboxes in Tab 3 | Confusing: two "Select Supplier" dropdowns on same tab for different operations | **Recommended — Accepted.** Single selectbox drives both Edit and Deactivate sections. |
| R11 — Onboarding Wizard bypass | Wizard uses `to_sql()` directly, bypasses all service-layer validation | **Recommended — Accepted as documented limitation.** Acknowledged explicitly. No code change in this sprint. |
| R12 — PRAGMA FK not enabled | FK protection is service-layer only | **Recommended — Partially accepted.** Enable PRAGMA after migration completes. Validation query required first. |

---

## 1. Objectives

### 1.1 Primary Objectives

**O1 — Eliminate freetext supplier drift.**  
`product_master.supplier` is a free-text VARCHAR. "Rahul Textiles", "Rahul textile", and "R Textiles" produce three phantom supplier groups in `generate_purchase_plan()`. The supplier becomes a managed entity with a primary key. Typos produce a validation error at product add/edit time, not a phantom group.

**O2 — Per-supplier lead time in the Demand Planner.**  
`generate_purchase_plan()` uses a single global `lead_time` integer from the sidebar. After this sprint, each supplier has its own `lead_time_days` used automatically. The global sidebar value becomes a fallback for products with no supplier assignment.

**O3 — Supplier CRUD via UI.**  
Add, edit, deactivate, and bulk-reassign suppliers through a dedicated page. Enforce uniqueness. Prevent deactivation of suppliers with assigned products unless a reassignment target is specified.

**O4 — Product-to-supplier assignment via FK.**  
Product add and edit in Data Manager use a selectbox populated from supplier_master, not freetext inputs. Service layer validates the assignment before writing.

### 1.2 Out of Scope for This Sprint

- Purchase orders (Sprint 2.2)
- MOQ constraints — supplier-level or SKU-level (Sprint 2.2)
- Supplier performance metrics
- Supplier payment integration
- Per-supplier safety stock multiplier

### 1.3 In Scope (added vs V1)

- Fix `migration_script.py` typo (`suppier_code` → `supplier_code`)
- Bulk supplier reassignment in deactivation workflow
- Enable `PRAGMA foreign_keys = ON` after migration completes

---

## 2. Current State Analysis

### 2.1 What Exists Today

**`product_master` columns relevant to suppliers:**

| Column | Type | Current state |
|---|---|---|
| `supplier` | VARCHAR(100) | Freetext display name. No constraint. Groups in Demand Planner split on typos. |
| `supplier_code` | VARCHAR(100) | Freetext code. No FK. No constraint. |

Both columns are populated by `migration_script.py` from Excel. The rename map at line 156 contains a typo: `'suppier_code': 'supplier_code'` (missing 'l'). Any database loaded from a correctly-spelled Excel column header (`supplier_code`) has NULL in `product_master.supplier_code` for every row. This is the most common production state.

**`generate_purchase_plan()` in `inventory_service.py`:**  
Groups the purchase plan by `['base_sku', 'product_name', 'supplier', 'category']`. Uses a single global `params.get('lead_time', 10)` for all supplier groups.

**`demand_planner.py`:**  
Single `lead_time` sidebar number_input. Applied to all suppliers without distinction.

**`catalog_service.py` `add_product()`:**  
`INSERT OR REPLACE INTO product_master`. The `supplier` and `supplier_code` fields are whatever the UI passes — no validation against any reference table.

**No `supplier_master` table exists.**

### 2.2 Risks in Current State

- Demand Planner groups by freetext `supplier`. Any inconsistency in spelling produces phantom supplier rows and incorrect purchase quantities per group.
- Any string can be entered as a supplier code. There is no way to enforce consistency.
- All suppliers get the same lead time. A supplier who delivers in 7 days is treated identically to one who delivers in 21 days.
- No way to enumerate which products belong to a given supplier without a manual SQL query.
- `migration_script.py` typo means that on any standard Excel-imported database, supplier_code is NULL for every product. Any catalog reimport via Onboarding Wizard will repeat this, silently destroying any supplier assignments made through the new UI.

---

## 3. Database Schema

### 3.1 New Table: `supplier_master`

```sql
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
```

**Column decisions:**

| Column | Type | Decision |
|---|---|---|
| `supplier_code` | VARCHAR(100) PK | Aligned to `product_master.supplier_code` VARCHAR(100). V1 had VARCHAR(50) — mismatch eliminated. UPPER-normalised on write by service layer. |
| `name` | VARCHAR(200) NOT NULL | Human-readable label shown in all dropdowns. NOT NULL — a nameless supplier is unusable. |
| `contact_name`, `contact_email`, `contact_phone` | VARCHAR, nullable | Optional contact info. No format validation. |
| `lead_time_days` | INT NOT NULL DEFAULT 10 | Per-supplier delivery lead time. Used by Demand Planner. Must be >= 1. |
| `payment_terms` | VARCHAR(100), nullable | Informational freetext. No system integration now. Kept because it has no future semantic conflict risk. |
| `notes` | TEXT, nullable | Freetext scratch space. |
| `is_active` | INTEGER, CHECK (0 or 1) | SQLite has no BOOLEAN. CHECK constraint enforced since SQLite 3.25.0 (2018). 1 = active, 0 = deactivated. `updated_at` set explicitly on every write since SQLite has no ON UPDATE trigger support. |
| `created_at`, `updated_at` | DATETIME | Audit timestamps. `updated_at` is the responsibility of the service layer, not a DB trigger. |
| `min_order_qty` | **Removed from V1** | No consumer in this sprint. Sprint 2.2 defines per-SKU and per-PO line MOQ with different semantics. Adding it now risks semantic conflict. |

### 3.2 New Table: `schema_migrations`

```sql
CREATE TABLE IF NOT EXISTS schema_migrations (
    migration_name VARCHAR(100) PRIMARY KEY,
    applied_at     DATETIME     DEFAULT CURRENT_TIMESTAMP
);
```

This table tracks which one-time data migrations have been applied. It is the guard mechanism that replaces the broken `COUNT(*) > 0` guard from V1. A migration inserts its name here upon completion, regardless of how many rows it processed. Subsequent startups check for the presence of the name — not the state of the data — and skip if found.

This table serves all future data migrations, not just supplier seeding. It is a general-purpose migration journal.

### 3.3 Modified Table: `product_master`

No columns are added or removed. The existing `supplier_code VARCHAR(100)` column becomes a soft foreign key to `supplier_master.supplier_code`. The `supplier` (freetext name) column is retained as a historical/fallback display field with no active maintenance. It is never updated by service code after this sprint.

**Why soft FK (service-layer enforcement, not DB constraint):**  
SQLite only enforces FK constraints when `PRAGMA foreign_keys = ON` is set per connection. This pragma is enabled as a final step after migrations confirm the data is clean (see Section 4.5). Even after enabling it, the Onboarding Wizard path bypasses service validation and could violate the constraint on catalog reimport (documented limitation). For this reason, service-layer validation remains the primary defence, with the PRAGMA as a secondary backstop.

### 3.4 Schema File Changes (`src/infrastructure/schema.sql`)

The schema.sql file is used for fresh installs. Two changes:

**Change 1 — Drop order:**
```
PRAGMA foreign_keys = OFF
DROP TABLE IF EXISTS channel_listings
DROP TABLE IF EXISTS pack_master
DROP TABLE IF EXISTS product_master
DROP TABLE IF EXISTS supplier_master       ← ADD (parent of product_master)
DROP TABLE IF EXISTS schema_migrations     ← ADD (tracking table)
PRAGMA foreign_keys = ON
```

Supplier_master is dropped before product_master because product_master references it (even though the FK is soft). With `PRAGMA foreign_keys = OFF` (already in the file) the drop order does not cause a constraint error — but keeping the logical order is correct for when the PRAGMA is eventually ON.

**Change 2 — Create order:**
```
CREATE TABLE schema_migrations (...)       ← ADD (first — no dependencies)
CREATE TABLE supplier_master (...)         ← ADD (before product_master)
CREATE TABLE product_master (...)          ← UNCHANGED
... rest unchanged ...
```

---

## 4. Migrations Required

### 4.1 Overview

Three categories of migration, handled by three separate functions in `init_db.py`:

| Function | Type | When runs | Guard mechanism |
|---|---|---|---|
| `_apply_table_migrations(engine)` | DDL — table creation | Every startup | `CREATE TABLE IF NOT EXISTS` — idempotent |
| `_seed_supplier_master_from_products(engine)` | One-time data | First startup after deployment | `schema_migrations` row check |
| `_enable_foreign_keys_if_clean(engine)` | PRAGMA enforcement | Every startup | Always runs — sets PRAGMA if data is clean |

### 4.2 `_apply_table_migrations(engine)` — Every Startup

Runs on every application startup. Each statement is idempotent.

**Statement 1:** `CREATE TABLE IF NOT EXISTS schema_migrations (...)`  
**Statement 2:** `CREATE TABLE IF NOT EXISTS supplier_master (...)`

These are safe to run on any database state. They create the tables only if absent. No data is touched. If both tables already exist, both statements succeed silently.

Implementation: add this function to `init_db.py`. Call it first in both `_check_and_migrate_existing_db()` and `_create_new_database()`, before `_apply_column_migrations()`.

### 4.3 `_seed_supplier_master_from_products(engine)` — One-Time Data Migration

**Guard mechanism:**  
`SELECT COUNT(*) FROM schema_migrations WHERE migration_name = 'seed_supplier_master_v1'`

If this row exists: log "Seed migration already applied — skipping" and return immediately.

If this row does not exist: run the seeding logic below, then insert the guard row unconditionally — regardless of how many supplier records were seeded. The migration is marked complete even if it found zero rows to seed. This is the critical fix from V1: the guard tracks completion, not outcome.

**Seeding logic:**

Step 1 — Normalise existing supplier_code casing in product_master:
```sql
UPDATE product_master
SET supplier_code = UPPER(TRIM(supplier_code))
WHERE supplier_code IS NOT NULL AND supplier_code != ''
```

Step 2 — Insert distinct supplier records from product_master:
```sql
INSERT OR IGNORE INTO supplier_master (supplier_code, name)
SELECT DISTINCT
    UPPER(TRIM(supplier_code)),
    COALESCE(NULLIF(TRIM(supplier), ''), UPPER(TRIM(supplier_code)))
FROM product_master
WHERE supplier_code IS NOT NULL AND TRIM(supplier_code) != ''
```
The `COALESCE` handles the case where `product_master.supplier` is NULL or blank: it falls back to the supplier_code itself as the name. A supplier named "RAHUL-001" is better than no supplier at all — the user can edit the name afterwards.

Step 3 — Log products with no supplier_code for manual resolution:
Query `SELECT sku, supplier FROM product_master WHERE supplier_code IS NULL OR TRIM(supplier_code) = ''` and log each row at WARNING level: `"Product {sku} has no supplier_code (supplier freetext: '{supplier}'). Assign in Supplier Manager."` These products will show "Unassigned" in the product edit form until manually corrected.

Step 4 — Mark migration complete:
```sql
INSERT OR IGNORE INTO schema_migrations (migration_name) VALUES ('seed_supplier_master_v1')
```

This INSERT runs regardless of the outcome of steps 1–3. The migration does not fail silently if it seeds zero rows — it completes, logs the situation, and marks itself done.

### 4.4 Fix `migration_script.py` Typo — In This Sprint

**File:** `src/infrastructure/migration_script.py`, line 156.

**Change:** The rename map entry `'suppier_code': 'supplier_code'` is a typo. The correct Excel column header is `supplier_code`, not `suppier_code`. Since no rename is needed (the Excel column name already matches the DB column name), this entry should be removed from the rename map entirely.

**Impact:** After this fix, any fresh Excel import or catalog reimport correctly populates `product_master.supplier_code` from the Excel file. Without this fix, every catalog reimport silently sets all supplier_code values to NULL, destroying all supplier assignments made through the Supplier Master UI.

This fix is Step 1 of the implementation order — it is a prerequisite, not a follow-up.

The same typo appears in the duplicate code block below the `if __name__ == "__main__":` guard at the bottom of `migration_script.py` (lines 322–436, a dead code block that is never reached). Both occurrences must be fixed.

### 4.5 `_enable_foreign_keys_if_clean(engine)` — Post-Migration Validation

After all migrations run on startup, check whether the data is clean enough to enable FK enforcement:

```sql
SELECT COUNT(*) FROM product_master
WHERE supplier_code IS NOT NULL
  AND UPPER(TRIM(supplier_code)) NOT IN (
      SELECT supplier_code FROM supplier_master
  )
```

If this count is 0 (no orphaned supplier_code references): add a SQLAlchemy event listener on the engine that executes `PRAGMA foreign_keys = ON` for every new connection. Log: "Foreign key enforcement enabled."

If count > 0: log each orphaned product at WARNING level, do NOT enable the PRAGMA, and log: "Foreign key enforcement deferred: N products have unmatched supplier_code. Assign suppliers in Supplier Manager to enable FK enforcement." The application continues without the PRAGMA — behaviour identical to today.

This function runs on every startup. Once all orphans are resolved, the PRAGMA begins enforcing on the next restart automatically. No manual intervention is needed.

### 4.6 Migration Call Order in `init_db.py`

For `_check_and_migrate_existing_db()`:
```
1. _apply_table_migrations(engine)              ← new: creates schema_migrations + supplier_master
2. _apply_column_migrations(engine)             ← existing: idempotent column additions
3. _seed_supplier_master_from_products(engine)  ← new: one-time data seed, guarded
4. _enable_foreign_keys_if_clean(engine)        ← new: PRAGMA if data is clean
5. existing health check and migration logic    ← unchanged
```

For `_create_new_database()`:
```
After schema execution:
1. _apply_table_migrations(engine)              ← ensures tables exist even if schema.sql ran already
2. _enable_foreign_keys_if_clean(engine)        ← fresh DB has no products yet → count is 0 → PRAGMA ON
```

---

## 5. New Files

### 5.1 `src/core/services/supplier_service.py`

New service class `SupplierService`. Constructor: `self.engine = get_engine()`. Pattern follows `InventoryService`.

**Methods:**

---

`get_all_suppliers(active_only=True) → DataFrame`

Returns all `supplier_master` rows. If `active_only=True`, adds `WHERE is_active = 1`. Columns returned: `supplier_code, name, contact_name, contact_email, lead_time_days, payment_terms, notes, is_active, created_at, updated_at`. Used by the All Suppliers tab and the Tab 3 selectbox.

---

`get_supplier_dropdown() → list[dict]`

Returns `[{'supplier_code': ..., 'label': '...'}, ...]` where label is `"{supplier_code} — {name}"`. Active suppliers only (`is_active = 1`). Ordered by name. Returns empty list if no active suppliers exist. Used to populate selectboxes in Data Manager and Supplier Manager.

---

`add_supplier(data: dict) → None`

**Validation (in order):**
1. `supplier_code` after strip is not empty → `DataValidationException("Supplier code is required.")`
2. `name` after strip is not empty → `DataValidationException("Supplier name is required.")`
3. `lead_time_days` is integer >= 1 → `DataValidationException("Lead time must be at least 1 day.")`

**Normalisation:** `supplier_code = data['supplier_code'].strip().upper()`

**Write:** `INSERT INTO supplier_master (supplier_code, name, contact_name, contact_email, contact_phone, lead_time_days, payment_terms, notes) VALUES (...)` inside `engine.begin()`.

**Duplicate handling:** If `IntegrityError` (UNIQUE violation on PK), raise `DataValidationException("Supplier code '{code}' already exists.")`.

---

`update_supplier(supplier_code: str, data: dict) → None`

**Validation:** Same as `add_supplier` for `name` and `lead_time_days`. `supplier_code` is not in `data` — the PK is passed separately and is immutable.

**Write (single `engine.begin()` transaction):**
1. `UPDATE supplier_master SET name=:name, contact_name=:cn, ... updated_at=CURRENT_TIMESTAMP WHERE supplier_code=:code`
2. Nothing else. The `product_master.supplier` freetext column is **not** updated — it is a historical field with no active sync obligation. `generate_purchase_plan()` gets the canonical name from `supplier_master` via JOIN, not from `product_master.supplier`.

---

`get_supplier_by_code(supplier_code: str) → dict | None`

Returns single row as dict, or None. Used to pre-populate the edit form fields.

---

`get_products_for_supplier(supplier_code: str) → DataFrame`

Returns products assigned to the supplier. Columns: `sku, name, category`. Used in the deactivate section to show what will be affected.

---

`deactivate_supplier(supplier_code: str) → None`

Sets `is_active = 0, updated_at = CURRENT_TIMESTAMP` inside `engine.begin()`.

**Guard:** Before writing, check `SELECT COUNT(*) FROM product_master WHERE UPPER(TRIM(supplier_code)) = :code`. If > 0: raise `DataValidationException`. This guard exists only as a safety net — the UI's bulk reassign workflow should prevent reaching this path with assigned products. If it is reached, the error message is: "Cannot deactivate {code}: {N} products still assigned. Use bulk reassign first."

Note: no products need to be unassigned before this path is reached legitimately. The bulk reassign (below) re-assigns all products first, then calls this method. By the time `deactivate_supplier()` is called, the count should be 0.

---

`bulk_reassign_and_deactivate(from_code: str, to_code: str) → int`

The primary deactivation path when products are assigned.

**Validation:**
1. `from_code != to_code` → `DataValidationException("Source and target supplier cannot be the same.")`
2. `to_code` exists and `is_active = 1` in supplier_master → `DataValidationException("Target supplier '{to_code}' does not exist or is inactive.")`

**Write (single `engine.begin()` transaction):**
1. `UPDATE product_master SET supplier_code = :to_code WHERE UPPER(TRIM(supplier_code)) = :from_code`
2. `UPDATE supplier_master SET is_active = 0, updated_at = CURRENT_TIMESTAMP WHERE supplier_code = :from_code`

Returns the count of products reassigned (the row count from step 1). The caller displays this count in the success message.

---

### 5.2 `src/ui/pages/supplier_manager.py`

New Streamlit page. `render()` function. Pattern follows `inventory_manager.py`.

Page title: "🏭 Supplier Master"

Three tabs:
- Tab 1: "📋 All Suppliers"
- Tab 2: "➕ Add Supplier"
- Tab 3: "✏️ Manage Supplier"

---

## 6. Files to Modify

### 6.1 `src/infrastructure/schema.sql`

Add `DROP TABLE IF EXISTS supplier_master` and `DROP TABLE IF EXISTS schema_migrations` before `DROP TABLE IF EXISTS product_master`. Add `CREATE TABLE IF NOT EXISTS schema_migrations (...)` and `CREATE TABLE IF NOT EXISTS supplier_master (...)` before `CREATE TABLE product_master`. No other changes to existing table definitions.

### 6.2 `src/infrastructure/migration_script.py`

**Change 1 (line ~156 in `run_migration()`):** Remove the entry `'suppier_code': 'supplier_code'` from the rename map. No rename is needed — the Excel column already matches the DB column name. The corrected rename_map should not contain any reference to `suppier_code`.

**Change 2 (duplicate dead code block at bottom of file):** The same rename map with the same typo appears in the `if __name__ == "__main__":` block (lines ~322–436). This code is dead (there are two `if __name__ == "__main__":` guards — Python only executes the first one's namespace). Both occurrences must be fixed regardless because the dead block will confuse future maintainers.

### 6.3 `src/infrastructure/init_db.py`

**New function 1:** `_apply_table_migrations(engine)` — Creates `schema_migrations` and `supplier_master` via `CREATE TABLE IF NOT EXISTS`. Wraps each in a try/except `OperationalError` consistent with the existing pattern from the Defect 3 fix.

**New function 2:** `_seed_supplier_master_from_products(engine)` — One-time data seed. Full logic described in Section 4.3.

**New function 3:** `_enable_foreign_keys_if_clean(engine)` — PRAGMA check described in Section 4.5.

**Import addition:** `from sqlalchemy import event` for the connection-level PRAGMA listener. `OperationalError` import already present after Defect 3 fix.

**Modified:** `_check_and_migrate_existing_db()` and `_create_new_database()` — call order updated per Section 4.6.

### 6.4 `src/core/services/inventory_service.py`

**Change 1 — `get_master_mapping()` query:**  
Add `p.supplier_code` to the SELECT list.

**Change 2 — `generate_purchase_plan()`, step 5.5 (new, between groupby and inventory math):**

After the groupby that produces `plan`, insert the following logic:

Query `SELECT supplier_code, lead_time_days FROM supplier_master WHERE is_active = 1` and load into `lead_time_df`.

Merge `lead_time_df` into `plan` on `supplier_code`, left join. For rows where the merge found no match (NULL after left join), fill `lead_time_days` with `params.get('lead_time', 10)`.

Add a `lead_time_source` column: value `"Supplier Master"` where the merge found a match, `"Default (sidebar)"` where fillna was applied.

`lead_time_demand` calculation becomes `plan['ads'] * plan['lead_time_days']`.

Also query supplier display names: `SELECT supplier_code, name FROM supplier_master WHERE is_active = 1`. Merge into plan as `supplier_name`. Where NULL (unmatched supplier_code), fall back to `plan['supplier']` (historical freetext). The `supplier` freetext column is no longer used for grouping — only as a display fallback.

**Change 3 — Return signature:**  
**Unchanged.** Still returns `(plan, orphans)`. No third value. The `lead_time_source` column and `supplier_name` column are part of `plan_df` and accessible to the UI from the first return value. No breaking change.

**No import of SupplierService.** The lead time and name lookups are direct SQL queries inside `generate_purchase_plan()` — the same method that already queries `inventory_master` directly.

### 6.5 `src/ui/pages/demand_planner.py`

**Change 1:** Relabel sidebar input: `"Default Lead Time (days)"` with help text `"Fallback for suppliers not in Supplier Master."` No other change to the input itself.

**Change 2:** After the existing metrics section and the plan dataframe display, add an `st.expander("📊 Lead Times Applied per Supplier")`. Contents: `plan_df[['supplier_name', 'supplier_code', 'lead_time_days', 'lead_time_source']].drop_duplicates()` rendered as a dataframe. This is derived from `plan_df` — no additional return value needed.

**Change 3:** The existing `plan_df, orphans_df = service.generate_purchase_plan(files, params)` unpacking is **unchanged**. No risk of `ValueError: too many values to unpack`.

### 6.6 `src/core/services/catalog_service.py`

**Change to `add_product(data: dict)`:**

Before `self._insert('product_master', data)`:

1. Extract `supplier_code = data.get('supplier_code', '').strip().upper()` if present.
2. If `supplier_code` is non-empty: `SELECT name FROM supplier_master WHERE supplier_code = :code AND is_active = 1`. If no row found: raise `DataValidationException(f"Supplier '{supplier_code}' does not exist in Supplier Master.")`.
3. If validation passes: do NOT update `data['supplier']` — leave `product_master.supplier` as whatever was already in the data dict. This field is historical and not actively maintained. The Demand Planner now gets the canonical name from supplier_master directly.

**No new `update_product_supplier()` method.** Removed from V1 — no caller in this sprint.

### 6.7 `src/ui/pages/data_manager.py`

**Change to product add/edit form:**

Replace the two text inputs (`supplier` and `supplier_code`) with a single selectbox built from `SupplierService().get_supplier_dropdown()`. The selected value's `supplier_code` is submitted with the form data.

If the dropdown returns an empty list: show `st.warning("No suppliers found. Add suppliers in **Supplier Master** before adding products.")` and disable the submit button.

For editing an existing product with a deactivated or unmatched supplier_code: the selectbox prepends an option `"(Inactive/Unknown) {old_code}"` so the form does not crash. Saving with this option leaves supplier_code unchanged. Saving with a valid active supplier updates it.

**Known limitation:** The Onboarding Wizard (`onboarding_wizard.py`) imports products via `migration_script.py` using `to_sql()` directly. It bypasses `CatalogService.add_product()` and therefore bypasses supplier validation. Products imported via the wizard will have supplier_code populated from the Excel file (after the typo fix in Section 6.2), but the value is not validated against `supplier_master`. After any wizard import, users should visit Supplier Manager to verify assignments. This is an accepted limitation for this sprint.

### 6.8 Application Navigation

**File:** Application entry point (app.py or equivalent navigation file — not read during analysis, but follows existing pattern).

Add `from src.ui.pages import supplier_manager` to imports. Add `supplier_manager.render` to the navigation sidebar alongside existing pages. Suggested position: between Data Manager and Inventory Manager.

---

## 7. UI Changes

### 7.1 Supplier Manager — Tab 1: All Suppliers

Loads `service.get_all_suppliers(active_only=False)`. Displays as styled DataFrame.

Columns: Supplier Code, Name, Contact Name, Lead Time (days), Payment Terms, Status.

Status column derived: `is_active == 1` → "Active" (green text via Styler), `is_active == 0` → "Inactive" (grey text via Styler).

Row count below table: "Showing N suppliers (M active, K inactive)."

Read-only. All edits happen in Tab 3.

### 7.2 Supplier Manager — Tab 2: Add Supplier

**Widget layout — outside the form:**

`supplier_code_input = st.text_input("Supplier Code *", key="add_supplier_code")`

`st.caption(f"Will be stored as: {supplier_code_input.strip().upper() or '—'}")`

This caption is outside the form. Because `supplier_code_input` is also outside the form, it triggers a rerender on every keystroke, and the caption updates live as the user types. This is the correct pattern — the Stale-Inside-Form antipattern from V1 is avoided.

**Widget layout — inside `st.form("add_supplier_form")`:**

| Field | Widget | Validation |
|---|---|---|
| Supplier Name | `text_input`, required | Non-empty after strip. Max 200 chars. |
| Contact Name | `text_input`, optional | — |
| Contact Email | `text_input`, optional | — |
| Contact Phone | `text_input`, optional | — |
| Lead Time (Days) | `number_input`, min=1, default=10, step=1 | >= 1 enforced in service |
| Payment Terms | `text_input`, optional | e.g., "Net 30", "Advance" |
| Notes | `text_area`, optional | — |
| Submit | `st.form_submit_button("Add Supplier")` | — |

On submit: read `supplier_code_input` from outside the form (via `st.session_state["add_supplier_code"]`), combine with form field values, call `service.add_supplier(data)`.

On success: `st.success(f"✅ {code} — {name} added.")` + `st.rerun()`.  
On `DataValidationException`: `st.error(str(e))`. No rerun — user corrects the form.

### 7.3 Supplier Manager — Tab 3: Manage Supplier

**Single selectbox above everything else (outside any form):**

`selected_code = st.selectbox("Select Supplier", options=[...], key="tab3_supplier_code")`

Options include all suppliers (active and inactive, so inactive can be edited but not reassigned to). Label format: `"{code} — {name} [Inactive]"` for inactive suppliers.

Below the selectbox, two visually separated sections:

---

**Section A: Edit**

Uses `service.get_supplier_by_code(selected_code)` to pre-populate. The `supplier_code` is shown as `st.text(f"Supplier Code: {selected_code} (cannot be changed)")` — not an editable widget.

Inside `st.form("edit_supplier_form")`: Name (required), Contact Name, Contact Email, Contact Phone, Lead Time, Payment Terms, Notes inputs. Submit button: "Save Changes".

On success: `st.success(f"✅ {selected_code} updated.")` + `st.rerun()`.

---

**Section B: Deactivate**

Visible only if `selected_supplier['is_active'] == 1`. If the selected supplier is already inactive, show `st.info("This supplier is already inactive.")` and skip this section.

Show `service.get_products_for_supplier(selected_code)` as a table. Header: `"Products assigned to this supplier: N"`.

**If N == 0 (no products assigned):**

Show `st.warning("⚠️ Deactivating {code} will remove it from all dropdowns.")`.

Confirmation checkbox OUTSIDE any form:
`confirmed = st.checkbox("I confirm I want to deactivate this supplier", key="deactivate_confirm")`

Below it, conditionally:
```
if confirmed:
    if st.button("Deactivate", type="primary"):
        service.deactivate_supplier(selected_code)
        ...
```

The button only renders when the checkbox is checked. This is the correct Streamlit pattern — the checkbox outside the form triggers a rerender, which conditionally renders the button. No form involved.

**If N > 0 (products assigned):**

Show `st.info("This supplier has {N} assigned products. To deactivate, you must reassign them to another supplier first.")`.

Show a selectbox: `reassign_to = st.selectbox("Reassign all {N} products to:", active_suppliers_except_this_one, key="reassign_target")`.

Confirmation checkbox OUTSIDE any form:
`confirmed = st.checkbox("I confirm reassignment and deactivation", key="reassign_confirm")`

Conditionally:
```
if confirmed:
    if st.button("Reassign and Deactivate", type="primary"):
        count = service.bulk_reassign_and_deactivate(selected_code, reassign_to)
        st.success(f"✅ {count} products reassigned to {reassign_to}. {selected_code} deactivated.")
        st.rerun()
```

The Reassign and Deactivate operation is a single `engine.begin()` transaction — both the product UPDATE and the supplier deactivation happen atomically.

### 7.4 Demand Planner UI Changes

Sidebar `lead_time` input relabelled: `"Default Lead Time (days)"`. Help text: `"Fallback for suppliers not in Supplier Master."` All other inputs and file uploaders unchanged.

After the plan dataframe display, add:
```
with st.expander("📊 Lead Times Applied per Supplier"):
    lt_display = plan_df[['supplier_name', 'supplier_code', 'lead_time_days', 'lead_time_source']]
                 .drop_duplicates()
                 .reset_index(drop=True)
    st.dataframe(lt_display, hide_index=True)
```

This data comes from `plan_df` — no return signature change required.

If all rows show `lead_time_source = "Default (sidebar)"`, the expander implicitly signals to the user that no suppliers are configured in Supplier Master.

### 7.5 Data Manager Product Form Changes

The `supplier` text_input and `supplier_code` text_input are replaced by:

`st.selectbox("Supplier *", options=supplier_labels, key="product_supplier")`

Where `supplier_labels = [f"{s['supplier_code']} — {s['label']}" for s in SupplierService().get_supplier_dropdown()]`.

If the dropdown is empty: `st.warning("No active suppliers. Add suppliers in **Supplier Master** first.")` + submit button disabled.

The selected option's `supplier_code` is extracted (by splitting on " — " or using the index into the original list) and passed to `add_product(data)` as `data['supplier_code']`.

---

## 8. Service Layer — Detailed Specifications

### 8.1 `generate_purchase_plan()` Changes

**Step 5 — groupby (existing):**  
Add `supplier_code` to the groupby key list: `['base_sku', 'product_name', 'supplier', 'supplier_code', 'category']`. Since each `base_sku` maps to one supplier, adding `supplier_code` does not change the grouping cardinality — it just carries the value through the aggregation so it is available in the next step.

**Step 5.5 — lead time and supplier name merge (new):**

Two separate queries against `supplier_master`:
1. `SELECT supplier_code, lead_time_days FROM supplier_master WHERE is_active = 1` → `lead_time_df`
2. `SELECT supplier_code, name AS supplier_name FROM supplier_master WHERE is_active = 1` → `names_df`

Merge `lead_time_df` into `plan` on `supplier_code`, left join. Set `lead_time_source`:
- Before fillna: `plan['lead_time_source'] = plan['lead_time_days'].apply(lambda x: "Supplier Master" if pd.notna(x) else "Default (sidebar)")`
- After fillna: `plan['lead_time_days'] = plan['lead_time_days'].fillna(params.get('lead_time', 10))`

Merge `names_df` into `plan` on `supplier_code`, left join. `plan['supplier_name'] = plan['supplier_name'].fillna(plan['supplier'])` — falls back to historical freetext if no supplier_master match.

**Step 6 — Inventory Math (existing, modified):**  
`plan['lead_time_demand'] = plan['ads'] * plan['lead_time_days']`  
`plan['safety_stock'] = plan['ads'] * safety_days`  
`plan['cycle_stock'] = plan['ads'] * buffer_days`  
All other math unchanged.

**Return value:** Still `(plan, orphans)`. No change.

### 8.2 Transaction Patterns

All `SupplierService` write methods (`add_supplier`, `update_supplier`, `deactivate_supplier`, `bulk_reassign_and_deactivate`) use `engine.begin()`.

`bulk_reassign_and_deactivate` executes two SQL statements in one `engine.begin()` block. If either fails, both roll back — products are not reassigned without the deactivation completing, and the deactivation does not complete without the reassignment.

`update_supplier` executes one SQL statement (UPDATE supplier_master only). It does NOT update `product_master.supplier`. This is intentional — the denormalized sync from V1 is removed.

---

## 9. Integration Points

### 9.1 Demand Planner ↔ supplier_master

**Data flow:** `generate_purchase_plan()` → `get_master_mapping()` returns `supplier_code` → merged with `supplier_master.lead_time_days` in step 5.5.

**Graceful degradation:** If `supplier_master` is empty: all rows have NULL after the lead time merge → fillna with global default → `lead_time_source = "Default (sidebar)"` for all rows. Behaviour is identical to today. The expander shows all rows as "Default (sidebar)" — a clear signal to the user.

**If `supplier_code` is NULL in product_master (legacy or wizard-imported data):** The left join produces NULL for `lead_time_days` and `supplier_name` → fillna applies defaults → no crash, no missing rows.

### 9.2 Data Manager ↔ supplier_master

**Product add:** `CatalogService.add_product()` validates `supplier_code` against `supplier_master` before INSERT.

**Product edit (legacy products with no supplier_code):** The edit form shows "— Unassigned —" as first option. Saving with "Unassigned" keeps `supplier_code` as NULL — no validation error (NULL is permitted). The Demand Planner handles NULL gracefully via the fallback path.

**Empty supplier_master:** Product add blocked with a clear warning. Product edit for existing products still works — the form shows their current (possibly NULL) state.

### 9.3 `update_supplier()` ↔ `product_master`

`product_master.supplier` (freetext) is **not** updated when a supplier name changes. This column is treated as a historical field. The Demand Planner uses `supplier_master.name` via JOIN as the authoritative display name. The freetext column continues to exist as a fallback for products with NULL `supplier_code` — it degrades gracefully rather than being actively maintained.

The consequence: a product that has `supplier_code = NULL` and `supplier = "Rahul Textiles"` (old freetext) will show "Rahul Textiles" in the Demand Planner. A product with `supplier_code = "RAHUL-001"` will show whatever `supplier_master.name` says for "RAHUL-001". The two paths are independent. No sync needed.

### 9.4 Onboarding Wizard ↔ supplier_master (Known Limitation)

The Onboarding Wizard calls `migration_script.py` which uses `DataFrame.to_sql()` to bulk-insert product rows. This bypasses `CatalogService.add_product()` and therefore bypasses supplier_code validation. After the `migration_script.py` typo fix (Section 6.2), wizard-imported products will have `supplier_code` populated from the Excel file — but the value is not validated against `supplier_master`.

Two consequences:
1. If the wizard runs before any suppliers are added in Supplier Master: products have supplier_codes that don't exist in supplier_master. The FK validation in `_enable_foreign_keys_if_clean()` will detect this and defer the PRAGMA.
2. If the wizard runs after suppliers are added: supplier_codes from Excel may match or not match. Mismatches are logged as warnings but do not crash the application.

**Mitigation text for UI:** After any wizard import, a banner should appear on the Supplier Master page: "Your catalog was recently imported. Verify supplier assignments are correct." This banner can be triggered by checking whether any `product_master.supplier_code` values are absent from `supplier_master`.

**Full fix** (wizard validation) is deferred to a future sprint to avoid scope creep.

### 9.5 Bulk Reassign ↔ Foreign Key Enforcement

When `bulk_reassign_and_deactivate(from_code, to_code)` completes, all products previously referencing `from_code` now reference `to_code`. The deactivated supplier remains in `supplier_master` with `is_active = 0`. If the PRAGMA is enabled, the FK still holds because the row still exists (deactivation, not deletion). If the PRAGMA is not yet enabled, no change to behaviour.

---

## 10. Risks

### R1 — Products with NULL supplier_code after wizard import (post-typo-fix)

**Status:** Mitigated, not eliminated.

After the typo fix, wizard imports populate `supplier_code` from Excel. But if the Excel file contains supplier_codes that were never added to Supplier Master, those products have dangling codes. `_enable_foreign_keys_if_clean()` detects and logs these at startup. The application continues to function. The FK PRAGMA is not enabled until the data is clean.

**Severity:** Low. Operational, not a crash risk.

### R2 — `product_master.supplier` freetext diverges over time

**Status:** Accepted as design decision.

The freetext column is no longer maintained. It may diverge from `supplier_master.name` as supplier names are edited. This is acceptable because `generate_purchase_plan()` uses `supplier_master.name` as the authoritative source. The freetext field is a display-only fallback for unassigned products. It will show stale data for products that had freetext set at import time. Users need to assign a supplier_code to get canonical names.

**Severity:** Low. Affects display only, not calculations.

### R3 — Deactivation with bulk reassign is an irreversible operation in one click

**Status:** Mitigated by two-step confirmation.

Bulk reassigning products from one supplier to another and deactivating the source is a significant data change. It is wrapped in: (1) explicit selectbox choice of target supplier, (2) checkbox confirmation, (3) button click. Three distinct user actions are required. Reversibility: the user can manually reassign products back individually, or reactivate the source supplier by editing it in Tab 3. There is no single "undo" — this is acceptable for a 1-10 user system.

**Severity:** Low.

### R4 — schema_migrations table adds a dependency to migration logic

**Status:** Accepted trade-off.

`_apply_table_migrations()` must create `schema_migrations` before `_seed_supplier_master_from_products()` reads it. If `_apply_table_migrations()` fails (e.g., disk full), the seed migration guard will fail with "no such table: schema_migrations". The application logs the error and continues — the seed will attempt to run on the next startup.

**Severity:** Very low. The DDL is a two-statement `CREATE TABLE IF NOT EXISTS`. Failure requires a broken filesystem.

### R5 — `PRAGMA foreign_keys = ON` event listener fires for every connection

**Status:** By design.

SQLite connection-level pragma means the PRAGMA must be set on every new connection. A SQLAlchemy `event.listen(engine, "connect", ...)` listener handles this automatically for every connection the engine creates. The listener is only registered if `_enable_foreign_keys_if_clean()` determines the data is clean. If data becomes dirty after the fact (e.g., via wizard reimport), the PRAGMA is still ON — which means the next wizard import that creates FK violations will fail with an IntegrityError rather than silently corrupting data. This is the correct behaviour: the PRAGMA enforcement surfaces bugs instead of hiding them.

---

## 11. Acceptance Criteria

### AC1 — supplier_master and schema_migrations tables exist after any startup

Fresh install and existing database: both tables exist after startup. Verified by: `SELECT * FROM supplier_master` and `SELECT * FROM schema_migrations` execute without error.

### AC2 — Seeding runs exactly once, regardless of row count

On an existing database where all products have NULL supplier_code: startup runs seeding, finds zero rows to seed, inserts `('seed_supplier_master_v1', ...)` into schema_migrations, logs all products with NULL supplier_code as WARNING. Second startup: seeding skipped. `schema_migrations` has one row. Supplier_master has zero rows (but migration is still complete). Verified by row counts and log output.

### AC3 — Seeding correctly populates from existing data when supplier_codes are present

On a database with three distinct supplier_codes in product_master: startup seeds three rows into supplier_master. Names populated from product_master.supplier freetext. Verified by supplier_master row count.

### AC4 — migration_script.py typo fix correctly populates supplier_code

After the typo fix: import a catalog from an Excel file with column header `supplier_code` (correctly spelled). Verify `product_master.supplier_code` is populated for all rows, not NULL. Verified by `SELECT COUNT(*) FROM product_master WHERE supplier_code IS NOT NULL`.

### AC5 — Add supplier: code normalised, name required, lead_time validated

Entering code "rahul-001" stores "RAHUL-001". Empty name → error. Lead time = 0 → error. Duplicate code (any case) → error. Verified by DB inspection and UI error messages.

### AC6 — supplier_code live preview updates as user types

On the Add Supplier tab: typing in the Supplier Code field (outside form) causes the `st.caption` preview to update immediately with the normalised code. No page submit required. Verified by visual inspection.

### AC7 — Per-supplier lead time applied in Demand Planner

"RAHUL-001" has `lead_time_days = 7`. "VIKRAM-001" has `lead_time_days = 21`. Global sidebar = 10. After plan generation: RAHUL-001 rows have `lead_time_days = 7`, VIKRAM-001 rows have `lead_time_days = 21`. Products with NULL supplier_code have `lead_time_days = 10`. Verified by inspecting plan_df or expander.

### AC8 — Demand Planner return signature unchanged

`plan_df, orphans_df = service.generate_purchase_plan(files, params)` unpacks without error. Verified by a full Demand Planner run.

### AC9 — Lead Times Applied expander shows source correctly

After plan generation: expander shows `lead_time_source = "Supplier Master"` for RAHUL-001 and VIKRAM-001. `lead_time_source = "Default (sidebar)"` for unassigned products. Verified by inspecting expander contents.

### AC10 — Deactivate with no products: two-step confirmation required

Selecting an unassigned supplier: deactivate button only appears after checkbox is checked. Clicking without checking: button does not render. Verified by visual inspection.

### AC11 — Deactivate with assigned products: bulk reassign required

Selecting a supplier with 5 products: deactivate button is replaced by the "Reassign and Deactivate" workflow. Deactivation without reassignment is not possible through the UI. After reassignment: all 5 products updated to target supplier_code. Source supplier `is_active = 0`. Verified by DB inspection.

### AC12 — Bulk reassign is atomic

If the deactivation UPDATE fails after the reassignment UPDATE: both roll back. Products remain with the original supplier_code. Source supplier remains active. Verified by: introduce a deliberate constraint violation on the deactivation UPDATE and confirm no products were reassigned.

### AC13 — PRAGMA FK enforcement enabled when data is clean

After seeding with valid data where all product supplier_codes match supplier_master: `_enable_foreign_keys_if_clean()` registers the connection listener. Attempting to insert a product with a non-existent supplier_code via `engine.begin()` produces an IntegrityError. Verified by attempting a direct SQL insert with an invalid supplier_code.

### AC14 — PRAGMA FK enforcement deferred when data is dirty

If any product has a supplier_code not in supplier_master: PRAGMA listener is not registered. Application logs WARNING with list of orphaned products. No crash. Verified by log output.

### AC15 — Product add blocked when supplier_master is empty

Opening the product add form when supplier_master has no active rows: warning shown with link to Supplier Master. Submit button disabled. Verified by visual inspection with an empty supplier_master.

### AC16 — update_supplier does not update product_master.supplier

After editing a supplier name: `SELECT supplier FROM product_master WHERE supplier_code = :code` returns the OLD freetext value. `SELECT name FROM supplier_master WHERE supplier_code = :code` returns the NEW value. Demand Planner shows NEW value (from supplier_master JOIN). Verified by both DB queries and plan output.

---

## 12. Test Cases

### T1 — Fresh install: both new tables created

**Steps:** Delete SQLite file. Start application. Navigate to any page.  
**Expected:** No startup error. `supplier_master` and `schema_migrations` tables exist and are empty.

---

### T2 — Seeding with all-NULL supplier_codes (typo scenario, pre-fix DB)

**Steps:** Use a database where all products have `supplier_code = NULL` (pre-fix state). Start application.  
**Expected:** Seeding runs, inserts zero rows into supplier_master, inserts `'seed_supplier_master_v1'` into schema_migrations. Log shows WARNING for each product with NULL supplier_code. Second startup: seeding does not run (schema_migrations guard fires). supplier_master still empty after second startup.

---

### T3 — Seeding with valid supplier_codes

**Steps:** Use a database with products having supplier_codes "RAHUL-001" (3 products, supplier "Rahul Textiles"), "VIKRAM-001" (5 products, supplier "Vikram Knits"). Start application.  
**Expected:** supplier_master has 2 rows. Names populated from product_master.supplier. `lead_time_days = 10` (default). Second startup: no change.

---

### T4 — migration_script.py typo fix

**Steps:** Import a catalog from Excel with column `supplier_code` (correctly spelled). Check product_master.  
**Expected:** `SELECT COUNT(*) FROM product_master WHERE supplier_code IS NOT NULL` = total product count. No NULL values from a correctly-spelled Excel import.

---

### T5 — Add supplier: success with code normalisation

**Steps:** Tab 2 → type "rahul-001" in Supplier Code input (outside form). Observe caption. Fill Name = "Rahul Textiles", Lead Time = 14. Submit.  
**Expected:** Caption shows "RAHUL-001" immediately as user types. supplier_master has row with `supplier_code = "RAHUL-001"`, `lead_time_days = 14`.

---

### T6 — Add supplier: validation failures (four sub-cases)

Empty code → "Supplier code is required." Empty name → "Supplier name is required." Lead time = 0 → "Lead time must be at least 1 day." Duplicate code "rahul-001" when "RAHUL-001" exists → "Supplier code 'RAHUL-001' already exists."

---

### T7 — Tab 3 single selectbox drives both edit and deactivate

**Steps:** Tab 3 → select "RAHUL-001". Verify both Section A (edit form) and Section B (deactivate section) respond to the same selection. Change selection to "VIKRAM-001". Verify both sections update.  
**Expected:** Single selectbox. No second "Select Supplier" dropdown anywhere on Tab 3.

---

### T8 — Edit supplier: name change does not propagate to product_master

**Steps:** Edit "RAHUL-001" name from "Rahul Textiles" to "Rahul Fabrics Ltd". Submit.  
**Expected:** `SELECT name FROM supplier_master WHERE supplier_code = 'RAHUL-001'` → "Rahul Fabrics Ltd". `SELECT supplier FROM product_master WHERE supplier_code = 'RAHUL-001'` → "Rahul Textiles" (unchanged). Demand Planner shows "Rahul Fabrics Ltd" (from JOIN). product_master.supplier untouched.

---

### T9 — Deactivate: confirmation checkbox outside form

**Steps:** Tab 3 → select an unassigned supplier. Before checking checkbox: verify no "Deactivate" button is visible. Check checkbox: verify button appears. Uncheck: button disappears.  
**Expected:** Button appears and disappears based on checkbox state, without any form submission. Purely via Streamlit rerender.

---

### T10 — Deactivate: blocked when products assigned, bulk reassign shown

**Steps:** Tab 3 → select "RAHUL-001" (3 products assigned). Look for deactivate section.  
**Expected:** Table of 3 products shown. "Reassign all 3 products to:" selectbox appears. No plain "Deactivate" button shown. The path requires choosing a target supplier.

---

### T11 — Bulk reassign and deactivate: success

**Steps:** Select "RAHUL-001" (3 products). Select target "VIKRAM-001". Check confirmation. Click "Reassign and Deactivate".  
**Expected:** Success message: "3 products reassigned to VIKRAM-001. RAHUL-001 deactivated." `SELECT supplier_code FROM product_master WHERE supplier_code = 'RAHUL-001'` returns 0 rows. `SELECT is_active FROM supplier_master WHERE supplier_code = 'RAHUL-001'` → 0.

---

### T12 — Bulk reassign atomicity

**Steps:** Simulate a failure in the deactivation UPDATE (e.g., attempt to deactivate a code that doesn't exist — craft a test scenario).  
**Expected:** Products remain with original supplier_code. supplier_master unchanged. Transaction fully rolled back.

---

### T13 — Demand Planner: per-supplier lead times

**Setup:** "RAHUL-001" lead_time=7, "VIKRAM-001" lead_time=21, sidebar default=10. 2 products with NULL supplier_code.  
**Steps:** Upload sales file, generate plan.  
**Expected:** RAHUL-001 rows: `lead_time_days=7`, `lead_time_source="Supplier Master"`. VIKRAM-001 rows: `lead_time_days=21`, `lead_time_source="Supplier Master"`. NULL supplier_code rows: `lead_time_days=10`, `lead_time_source="Default (sidebar)"`. Return unpacking `plan_df, orphans_df = ...` succeeds.

---

### T14 — Demand Planner: empty supplier_master, no crash

**Steps:** Ensure supplier_master is empty. Upload sales file, generate plan.  
**Expected:** Plan generates without error. All rows have `lead_time_source="Default (sidebar)"`. Expander shows all suppliers using default. Plan is functionally identical to pre-feature behaviour.

---

### T15 — PRAGMA FK enabled when data is clean

**Setup:** All product supplier_codes match supplier_master.  
**Steps:** Restart application. Attempt a direct SQL insert into product_master with an invalid supplier_code via engine.begin().  
**Expected:** IntegrityError raised. Row not inserted.

---

### T16 — PRAGMA FK deferred when data has orphans

**Setup:** One product has `supplier_code = 'ORPHAN-999'` which is not in supplier_master.  
**Steps:** Start application.  
**Expected:** Log WARNING: "Foreign key enforcement deferred: 1 product has unmatched supplier_code." No crash. Application runs normally.

---

### T17 — Product add blocked when supplier_master empty

**Steps:** supplier_master empty. Open product add form in Data Manager.  
**Expected:** Warning shown with explicit link to Supplier Master page. Submit button disabled. No way to submit the form.

---

### T18 — Product add validates against supplier_master

**Steps:** supplier_master has "RAHUL-001". Attempt to add product via Data Manager with supplier selectbox set to "RAHUL-001". Submit.  
**Expected:** Product inserted. `product_master.supplier_code = 'RAHUL-001'`.

---

### T19 — All Suppliers tab: shows active and inactive with correct styling

**Steps:** 2 active suppliers, 1 deactivated supplier. Navigate to Tab 1.  
**Expected:** 3 rows shown. Deactivated row styled differently (grey). Row count shows "(2 active, 1 inactive)."

---

## 13. Implementation Order

Steps 9 and 10 are the only pair that must be deployed in the same commit. All other steps are independently testable.

| Step | File(s) | Action | Why first |
|---|---|---|---|
| 1 | `migration_script.py` | Fix `suppier_code` typo (both occurrences) | Prerequisite for all data integrity downstream. Any catalog reimport after this step correctly populates supplier_code. |
| 2 | `schema.sql` | Add `schema_migrations` and `supplier_master` CREATE TABLE and DROP TABLE statements | Foundation. Must exist before init_db changes. |
| 3 | `init_db.py` | Add `_apply_table_migrations()`, `_seed_supplier_master_from_products()`, `_enable_foreign_keys_if_clean()`. Update call order. | Creates and seeds the table. Enables FK enforcement when clean. Verify seeding works on a real DB before touching any service code. |
| 4 | `supplier_service.py` | Create full file with all methods | Service layer before UI. All methods testable by inspection and direct service call before any UI touches them. |
| 5 | `supplier_manager.py` | Create full page (all 3 tabs) | CRUD UI. Manually verify add/edit/deactivate/bulk-reassign before wiring into catalog. |
| 6 | Navigation | Add supplier_manager to app navigation | Makes the page accessible. Verify it renders without error. |
| 7 | `catalog_service.py` | Add supplier_code validation to `add_product()` | Validation in service before UI change. If this step is deployed before step 8, the UI still sends freetext — but the service will reject invalid codes at the service layer. |
| 8 | `data_manager.py` | Replace supplier text inputs with selectbox | UI now enforces correct supplier selection. Requires step 7 and step 5 to be complete. |
| 9 | `inventory_service.py` | Update `get_master_mapping()` and `generate_purchase_plan()` | Lead time merge logic. Must be deployed in the same commit as step 10. |
| 10 | `demand_planner.py` | Update sidebar label, add expander. Verify `plan_df, orphans_df = ...` unpacking unchanged. | Paired with step 9. No signature change means this is a low-risk edit. |
