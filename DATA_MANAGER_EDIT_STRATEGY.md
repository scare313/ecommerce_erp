# Data Manager Edit Strategy

**Date:** 2026-06-10  
**Triggered by:** D2 — Supplier Master review defect  
**Scope:** Tab 1 (Products), Tab 2 (Packs), Tab 3 (Listings)  
**Author:** Architecture review

---

## 1. D2 Classification

### 1.1 Is D2 a Supplier Master defect?

Partially. The Supplier Master sprint (Phase 2.1) introduced `supplier_code` validation in `CatalogService.add_product()` and replaced the freetext supplier inputs in the Add New Product form with a validated selectbox. That work is correct.

D2 is a Supplier Master defect only in the sense that it is **newly visible** after the sprint: before Phase 2.1, there was no referential constraint on `supplier_code` to violate. The edit path bypass existed before but had no meaningful consequences.

### 1.2 Is D2 a Data Manager architecture defect?

Yes, primarily. The root cause is a pre-existing architectural pattern in `data_manager.py` that applies to all three editable tables:

```
st.data_editor → "Save" button → to_sql(if_exists='replace') → raw SQLite
```

This pattern predates Supplier Master. It bypasses all service-layer validation for any edit to any row of `product_master`, `pack_master`, or `channel_listings`. Phase 2.1 did not introduce the bypass — it introduced validation that the bypass now sidesteps.

**D2 is therefore a Data Manager architecture defect that Phase 2.1 made load-bearing.**

---

## 2. Current Edit Architecture

### 2.1 Add path (new records)

```
UI form → CatalogService.add_product() / add_pack() / add_listing()
       → service validates → engine.begin() INSERT
```

Validation is enforced. For products: supplier_code is checked against `supplier_master` (Phase 2.1). For packs: master_sku FK is implicitly validated by the database. For listings: duplicate check via IntegrityError catch.

### 2.2 Edit path (existing records)

```
st.data_editor (all columns editable) → "Save X Changes" button
→ edited_df.to_sql('table_name', conn, if_exists='replace', index=False)
→ raw SQLite REPLACE — no service layer involved
```

Affected tables and their respective save buttons:

| Tab | Table | Line | Bypass Risk |
|-----|-------|------|-------------|
| 1 — Products | `product_master` | 131 | `supplier_code` written raw; no validation against `supplier_master` |
| 2 — Packs | `pack_master` | 190 | `master_sku` written raw; no FK validation |
| 3 — Listings | `channel_listings` | 257 | `internal_sku` and `selling_price` written raw |

### 2.3 The `if_exists='replace'` amplification

`to_sql(if_exists='replace')` does not issue `UPDATE` statements. It:
1. `DROP TABLE product_master`
2. `CREATE TABLE product_master` (new schema inferred from DataFrame dtypes)
3. `INSERT` all rows

Consequences beyond validation bypass:
- Any columns not present in the DataFrame (e.g. due to Pandas dtype inference dropping a column) are silently lost
- Column types are re-inferred by Pandas, potentially losing SQLite type constraints
- Any triggers or indexes on the table are dropped and not recreated
- If the DataFrame has a row with a NULL primary key, it is inserted without error

---

## 3. Validation Bypass Risks

### 3.1 supplier_code integrity (immediate — Phase 2.1 era)

A user can open the data editor, type any arbitrary string into the `supplier_code` column of any product row, and click "Save Product Changes". The value bypasses `CatalogService.add_product()` entirely. Result:

- `product_master.supplier_code` contains a code not in `supplier_master`
- `_enable_foreign_keys_if_clean()` detects this on next startup and defers the FK PRAGMA
- The Demand Planner's step 5.5 left join finds no match for that code, falls back to sidebar default — silently wrong lead time
- No error is ever shown to the user

### 3.2 Referential integrity across all tables

| Risk | Table | Field | Consequence |
|------|-------|-------|-------------|
| Invalid supplier_code | `product_master` | `supplier_code` | Phantom supplier in demand planner |
| Invalid master_sku | `pack_master` | `master_sku` | Pack orphaned; channel listings break |
| Invalid internal_sku | `channel_listings` | `internal_sku` | Sales mapping fails; orphan rows in demand planner |
| SKU collision on save | `product_master` | `sku` | `if_exists='replace'` silently overwrites correct row |
| Table DROP on save | all | — | Schema constraints, indexes re-derived by Pandas dtypes |

### 3.3 Severity by table

`product_master` is highest severity: it is the root of all FK chains in the application. Corruption here cascades into pack, listing, inventory, and demand planning data. `pack_master` and `channel_listings` are secondary.

---

## 4. Smallest Safe Fix

The smallest fix that specifically closes the supplier_code bypass without altering the broader edit architecture is:

**Add a `supplier_code` validation column_config to `st.data_editor` for Tab 1, then validate the edited DataFrame against `supplier_master` before calling `to_sql`.**

```python
# Before the save button:
invalid_codes = edited_prod[
    edited_prod['supplier_code'].notna() &
    (edited_prod['supplier_code'].str.strip() != '') &
    ~edited_prod['supplier_code'].isin(valid_active_codes)
]
if not invalid_codes.empty:
    st.error(f"Invalid supplier codes: {invalid_codes['supplier_code'].tolist()}")
    # do not proceed to to_sql
```

Where `valid_active_codes` is a set fetched from `supplier_master WHERE is_active = 1` once per render.

**Scope:** Tab 1 only. One read query added. No architectural change. The `to_sql` path is unchanged.

**What this fixes:** Prevents a user from saving an invalid `supplier_code` via the grid editor.

**What this does not fix:**
- `if_exists='replace'` risk (table DROP/recreate)
- Validation bypass on `pack_master` and `channel_listings`
- No service-layer consistency — validation logic lives in two places (service and UI layer)
- No handling for editing a product currently assigned to an **inactive** supplier (plan section 7.5 requirement)

---

## 5. Long-Term Correct Solution

Replace the `data_editor + to_sql(replace)` pattern with row-level service-validated edits.

### Option A — Row-level Edit Form (Recommended for internal ERP)

Replace the raw grid save path with a selectbox-driven single-row edit form, following the same pattern already used in `supplier_manager.py` Tab 3:

```
selectbox (pick SKU) → pre-populated st.form → CatalogService.update_product()
```

`CatalogService.update_product()` would:
- Accept a dict of changed fields
- Validate `supplier_code` against `supplier_master` (reuse existing logic from `add_product`)
- Issue an `UPDATE product_master SET ... WHERE sku = :sku` via `engine.begin()`
- Handle inactive supplier_code by prepending `"(Inactive) OLD_CODE"` as the first selectbox option

**Advantages:**
- All validation enforced at service layer — one canonical location
- No `if_exists='replace'` risk
- Handles the inactive supplier_code edit case (plan section 7.5)
- Consistent with supplier_manager.py architecture already in this codebase
- Auditable: service layer can log each field change

**Disadvantages:**
- Cannot bulk-edit multiple rows in a single interaction — one record at a time
- Higher implementation effort (see Section 6)
- Users accustomed to the spreadsheet-style editor will notice the UX change

### Option B — Validated Batch Save (Minimal UX disruption)

Keep `st.data_editor` for display and input, but replace `to_sql(replace)` with a row-by-row service call loop on save:

```python
for _, row in edited_prod.iterrows():
    if row_changed(original_df, row):
        service.update_product(row['sku'], row.to_dict())
```

**Advantages:**
- Preserves spreadsheet-style editing UX
- Service layer enforced for every row on save
- Handles inactive supplier_code in the service

**Disadvantages:**
- N UPDATE queries instead of 1 batch operation (acceptable for 1–10 users)
- Requires implementing `CatalogService.update_product()` (not yet in codebase)
- Row-change detection logic needed to avoid unnecessary UPDATE calls
- `st.data_editor` still allows typing arbitrary values before the save — errors only surface on button click, not inline

### Recommendation

**For Tab 1 (Products):** Option A (row-level edit form). Products are the most critical table and edits are infrequent. Service-layer validation for `supplier_code` is essential. The inactive-supplier-code handling from plan section 7.5 naturally fits the edit form pattern.

**For Tab 2 (Packs) and Tab 3 (Listings):** Option B (validated batch save) is acceptable once `update_pack()` and `update_listing()` service methods exist. FK violations on these tables are less frequent and less severe than supplier_code drift on product_master.

---

## 6. Implementation Effort

| Fix | Files | Effort | Risk |
|-----|-------|--------|------|
| Smallest safe fix — supplier_code pre-save guard in data_manager.py | `data_manager.py` | 1–2 hours | Very low. Read-only query added before existing to_sql call. No structural change. |
| Option A — Row-level edit form for Tab 1 | `data_manager.py`, `catalog_service.py` | 3–5 hours | Low. Follows existing supplier_manager.py pattern exactly. Requires `update_product()` in catalog_service. |
| Option B — Validated batch save for Tab 1 | `data_manager.py`, `catalog_service.py` | 2–3 hours | Low-medium. Requires `update_product()` + row-change detection logic. |
| Full remediation — all three tabs (Option B) | `data_manager.py`, `catalog_service.py` | 5–8 hours | Medium. Three `update_*` methods needed, each with appropriate validation. |

---

## 7. Dependency on Phase 2.1

The smallest safe fix and Option A both depend on `SupplierService.get_supplier_dropdown()` being available (Phase 2.1, implemented). They do not depend on any further sprint work.

Full remediation for Tab 2 and Tab 3 is independent of Phase 2.1.

---

## 8. Recommendation Summary

1. **Immediately (this sprint):** Apply the smallest safe fix — pre-save supplier_code validation in Tab 1 before `to_sql`. Closes the specific D2 exposure without scope expansion.

2. **Sprint 2.2 (Purchase Orders):** Implement `CatalogService.update_product()` and replace Tab 1's raw data_editor save with Option A row-level edit form. This is a natural fit alongside the purchase order work, which will also need editable product data.

3. **Sprint 2.2 or later:** Extend Option B batch-save validation to Tab 2 and Tab 3 using their respective service update methods.
