# Inventory Phase 1 — Execution Plan

**Scope:** Sprint 1.1 (Fix What's Broken) + Sprint 1.2 (Complete Existing Workflows)  
**Goal:** Make inventory trustworthy and operational before adding intelligence  
**Date:** 2026-06-10  
**Context:** Internal ERP · SQLite · Streamlit · 1–10 users

---

## Reading This Document

Tasks are ranked in strict implementation order. Each task either has no dependencies or depends only on tasks listed before it. Do not skip ahead — schema migrations in Tasks 6 and 7 must precede the UI tasks that write to the new columns.

**Sprint 1.1** — Fixes to broken behaviour. No new features.  
**Sprint 1.2** — Complete workflows that are partially implemented. Minimal schema changes.

---

## Dependency Map

```
Task 1 (debug removal)       — no deps
Task 2 (engine.begin fix)    — no deps
Task 3 (negative stock)      — depends on Task 2 (same method)
Task 4 (bulk import ledger)  — depends on Task 2 (consistent transaction pattern)
Task 5 (demand planner)      — no deps, independent
Task 6 (updated_by schema)   — no deps, must precede Tasks 8 and 9
Task 7 (reason_code schema)  — no deps, must precede Tasks 8 and 9
Task 8 (transfer to shop UI) — depends on Tasks 6 and 7 (new columns must exist)
Task 9 (shop stock adjust UI)— depends on Tasks 6 and 7 (new columns must exist)
```

---

## SPRINT 1.1 — FIX WHAT'S BROKEN

---

### Task 1 — Remove Debug Output from Bulk Import UI

**Priority:** P0 · Effort: Trivial · Risk: None

---

#### Objective

Remove `st.write()` debug statements that print internal SKU lists to the production UI in Tab 4 (Bulk Update). Also remove `print()` calls that write operational state to server stdout. These lines serve no user purpose and expose internal data on screen.

---

#### Files Affected

| File | Change Type |
|---|---|
| `src/ui/pages/inventory_manager.py` | Delete 4 lines |

---

#### Database Changes

None.

---

#### Implementation Approach

**Lines to delete** (inventory_manager.py):

Line 215–216 — `st.write` debug block:
```python
# DELETE THESE TWO LINES:
st.write("🔍 **Debug Info:**")
st.write(f"SKUs to update: {df_upload['sku'].tolist()}")
```

Line 248 — `print` inside the update loop:
```python
# DELETE THIS LINE:
print(f"✅ Updated {sku}: {packs} packs, {multiplier} multiplier")
```

Line 254 — `print` after commit:
```python
# DELETE THIS LINE:
print(f"✅ Database committed: {updated_count} records updated")
```

The `logger.debug()` calls already on lines 247 and 253 provide the same information through the proper logging channel. No replacement is needed — just deletion.

---

#### Risks

None. Deletion only. No logic is altered. The import workflow, validation, commit, and result display are untouched.

---

#### Acceptance Criteria

- [ ] Clicking "Import Data" in Tab 4 does not display any debug output section on screen
- [ ] The "SKUs to update" list is not visible in the UI during or after import
- [ ] Import success/failure messages still display correctly
- [ ] `logger.debug()` calls remain intact (verify in logs, not UI)
- [ ] No `print()` to stdout during bulk import

---

#### Test Cases

| # | Action | Expected Result |
|---|---|---|
| T1.1 | Upload valid Excel, click Import Data | Only "✅ Successfully updated N records!" message visible. No debug section appears. |
| T1.2 | Upload file with some invalid SKUs | Warning about not-found SKUs displays. No debug SKU list visible. |
| T1.3 | Check server terminal during import | No `print()` output related to import appears in terminal |

---

---

### Task 2 — Fix manage_godown_stock() Transaction Atomicity

**Priority:** P0 · Effort: Trivial · Risk: Low

---

#### Objective

`manage_godown_stock()` is the **only stock write method called from the UI**. It currently uses `engine.connect()` with a manual `conn.commit()`. If the ledger `INSERT` on line 322 fails after the inventory `UPDATE` on line 313 has executed, the stock balance changes but no audit entry is written. The ledger and `inventory_master` diverge silently.

Replace with `engine.begin()` — the same pattern already applied to `add_stock()` and `transfer_to_shop()` in the stabilization work — so that both operations succeed together or neither is applied.

---

#### Files Affected

| File | Change Type |
|---|---|
| `src/core/services/inventory_service.py` | Modify `manage_godown_stock()`, lines 305–329 |

---

#### Database Changes

None. Schema is unchanged. The fix only changes the Python transaction context manager.

---

#### Implementation Approach

**Current code** (`manage_godown_stock()`, lines 305–329):

```python
with self.engine.connect() as conn:
    conn.execute(text("""
        INSERT OR IGNORE INTO inventory_master (sku)
        VALUES (:sku)
    """), {"sku": sku})
    
    conn.execute(text("""
        UPDATE inventory_master 
        SET godown_stock_packs = godown_stock_packs + :packs,
            pack_multiplier = :mult,
            last_updated = CURRENT_TIMESTAMP
        WHERE sku = :sku
    """), {"sku": sku, "packs": pack_change, "mult": multiplier})

    conn.execute(text("""
        INSERT INTO stock_ledger (sku, transaction_type, packs, multiplier, total_pieces_affected, reason)
        VALUES (:sku, :action, :p, :m, :tp, :r)
    """), {
        "sku": sku, "action": action_type, "p": packs, 
        "m": multiplier, "tp": total_pieces, "r": reason
    })
    conn.commit()
```

**Replace with:**

```python
with self.engine.begin() as conn:
    conn.execute(text("""
        INSERT OR IGNORE INTO inventory_master (sku)
        VALUES (:sku)
    """), {"sku": sku})
    
    conn.execute(text("""
        UPDATE inventory_master 
        SET godown_stock_packs = godown_stock_packs + :packs,
            pack_multiplier = :mult,
            last_updated = CURRENT_TIMESTAMP
        WHERE sku = :sku
    """), {"sku": sku, "packs": pack_change, "mult": multiplier})

    conn.execute(text("""
        INSERT INTO stock_ledger (sku, transaction_type, packs, multiplier, total_pieces_affected, reason)
        VALUES (:sku, :action, :p, :m, :tp, :r)
    """), {
        "sku": sku, "action": action_type, "p": packs, 
        "m": multiplier, "tp": total_pieces, "r": reason
    })
    # engine.begin() auto-commits on clean exit, auto-rolls back on exception
```

Two changes: `engine.connect()` → `engine.begin()`, and `conn.commit()` line deleted. All SQL is preserved verbatim.

---

#### Risks

**Low.** `engine.begin()` is equivalent to `engine.connect()` + `conn.commit()` on the happy path. The only behavioural difference is on the error path: `engine.begin()` rolls back automatically whereas `engine.connect()` with manual commit would leave the transaction in an undefined state if commit never executes. This is strictly safer behaviour.

One edge case: SQLite `INSERT OR IGNORE` inside a `begin()` block. This is supported and behaves identically — the IGNORE handling is at the statement level, not the transaction level.

---

#### Acceptance Criteria

- [ ] `manage_godown_stock()` no longer contains `conn.commit()` 
- [ ] `manage_godown_stock()` uses `engine.begin()` context manager
- [ ] Happy path: ADD 5 packs for a SKU — inventory_master increments and one ledger row is inserted
- [ ] Happy path: REMOVE 3 packs for a SKU — inventory_master decrements and one ledger row is inserted
- [ ] `INSERT OR IGNORE` still creates a new row in inventory_master when SKU does not exist
- [ ] No regression in Tab 2 Update Stock UI

---

#### Test Cases

| # | Action | Expected Result |
|---|---|---|
| T2.1 | ADD 10 packs to existing SKU | `godown_stock_packs` increases by 10; one ADD ledger row created with matching packs/multiplier |
| T2.2 | REMOVE 3 packs from existing SKU | `godown_stock_packs` decreases by 3; one REMOVE ledger row created |
| T2.3 | ADD to a SKU not yet in inventory_master | Row is created via INSERT OR IGNORE; balance is set to the added quantity; ledger row created |
| T2.4 | Verify ledger entry exists for every UI submit | Count ledger rows before and after — delta must be exactly 1 for each submit |

---

---

### Task 3 — Negative Stock Prevention

**Priority:** P0 · Effort: Low · Risk: Low

---

#### Objective

`manage_godown_stock()` executes REMOVE operations without checking the current balance. A REMOVE of 100 packs when 5 are in stock silently writes -95 to `inventory_master`. Negative stock corrupts the Home Dashboard out-of-stock count, the Demand Planner's net requirement calculation, and any manual stock review.

Add a pre-flight balance check before any REMOVE operation. If the requested removal exceeds current stock, raise a `DataValidationException` with a clear message. The UI already wraps the service call in a try/except that surfaces the message to the user.

---

#### Files Affected

| File | Change Type |
|---|---|
| `src/core/services/inventory_service.py` | Modify `manage_godown_stock()` — add balance check |
| `src/ui/pages/inventory_manager.py` | No change needed — existing `except` on line 110 already surfaces the error |

---

#### Database Changes

None. The check is a SELECT before the UPDATE — no schema change.

---

#### Implementation Approach

Insert the balance check **inside** the `engine.begin()` block (established in Task 2), **before** the UPDATE statement. This is intentional: performing the check inside the transaction ensures no concurrent modification can pass a stale balance between the check and the update in a multi-user scenario.

**Modified `manage_godown_stock()`** (showing the relevant section only):

```python
def manage_godown_stock(self, sku, packs, multiplier, action_type, reason=""):
    """Handles adding or removing packs with a specific multiplier."""
    total_pieces = packs * multiplier
    pack_change = packs if action_type == "ADD" else -packs

    with self.engine.begin() as conn:
        # 1. Ensure the SKU row exists in inventory_master
        conn.execute(text("""
            INSERT OR IGNORE INTO inventory_master (sku)
            VALUES (:sku)
        """), {"sku": sku})

        # 2. Guard: prevent negative stock on REMOVE
        if action_type == "REMOVE":
            result = conn.execute(
                text("SELECT godown_stock_packs FROM inventory_master WHERE sku = :sku"),
                {"sku": sku}
            )
            row = result.fetchone()
            current_packs = row[0] if row else 0
            if current_packs < packs:
                raise DataValidationException(
                    f"Cannot remove {packs} packs: only {current_packs} in stock for {sku}. "
                    "Operation cancelled."
                )

        # 3. Apply the stock change
        conn.execute(text("""
            UPDATE inventory_master 
            SET godown_stock_packs = godown_stock_packs + :packs,
                pack_multiplier = :mult,
                last_updated = CURRENT_TIMESTAMP
            WHERE sku = :sku
        """), {"sku": sku, "packs": pack_change, "mult": multiplier})

        # 4. Write ledger entry
        conn.execute(text("""
            INSERT INTO stock_ledger (sku, transaction_type, packs, multiplier, total_pieces_affected, reason)
            VALUES (:sku, :action, :p, :m, :tp, :r)
        """), {
            "sku": sku, "action": action_type, "p": packs,
            "m": multiplier, "tp": total_pieces, "r": reason
        })
        # engine.begin() auto-commits on exit, auto-rolls back on exception
```

`DataValidationException` is already imported at the top of `inventory_service.py` (line 9). No new imports needed.

The exception message is clear enough for the UI to surface verbatim. The existing `st.error(f"Update failed: {str(e)}")` on line 112 of inventory_manager.py will display it.

**Boundary condition:** `current_packs == packs` (removing exactly what is in stock) is allowed — result is 0 packs, which is a valid "out of stock" state. Only `current_packs < packs` is blocked.

---

#### Risks

**Low.** The SELECT adds one round-trip per REMOVE operation. On SQLite with 1–10 users this is negligible. The check is inside the transaction so no TOCTOU race condition can occur.

One known gap: this prevents negative godown_stock_packs but does **not** prevent negative shop_stock_pieces (shop stock has no UI for direct REMOVE — only bulk import and transfer_to_shop, addressed in Tasks 4 and 8). This is acceptable for this sprint.

---

#### Acceptance Criteria

- [ ] REMOVE operation where requested packs > current balance raises a `DataValidationException`
- [ ] UI displays the error message: "Cannot remove N packs: only M in stock for SKU"
- [ ] `inventory_master` balance is unchanged after a blocked REMOVE (transaction rolled back)
- [ ] No ledger row is written for a blocked REMOVE
- [ ] REMOVE operation where requested packs == current balance succeeds (balance becomes 0)
- [ ] REMOVE operation where requested packs < current balance succeeds as before
- [ ] ADD operations are completely unaffected

---

#### Test Cases

| # | Setup | Action | Expected Result |
|---|---|---|---|
| T3.1 | SKU has 5 packs | REMOVE 10 | Error displayed; balance remains 5; no ledger entry |
| T3.2 | SKU has 5 packs | REMOVE 5 | Succeeds; balance becomes 0; REMOVE ledger entry created |
| T3.3 | SKU has 5 packs | REMOVE 4 | Succeeds; balance becomes 1; REMOVE ledger entry created |
| T3.4 | SKU has 0 packs | REMOVE 1 | Error displayed; balance remains 0; no ledger entry |
| T3.5 | SKU has 0 packs | ADD 10 | Succeeds; balance becomes 10; ADD ledger entry created |
| T3.6 | SKU not yet in inventory_master | REMOVE 1 | INSERT OR IGNORE creates row with 0 packs; check fires; error displayed |

---

---

### Task 4 — Bulk Import Ledger Entries

**Priority:** P0 · Effort: Medium · Risk: Medium (highest in Sprint 1.1)

---

#### Objective

Tab 4 "Bulk Update" writes absolute stock values directly to `inventory_master` using a raw SQLite cursor and commits with **zero ledger entries**. This means every stock take, initial load, or mass correction is completely invisible in the audit trail. The "Recent History" tab will show nothing corresponding to these changes.

Rewrite the bulk import to: (1) read the current balance before updating, (2) compute the delta, (3) write a `STOCK_TAKE` ledger entry for every SKU that changed, (4) wrap all operations for a single SKU in a single transaction. Replace the raw cursor approach with SQLAlchemy `engine.begin()` blocks for consistency.

---

#### Files Affected

| File | Change Type |
|---|---|
| `src/ui/pages/inventory_manager.py` | Rewrite the import loop inside Tab 4 (lines 197–266) |

---

#### Database Changes

The `stock_ledger.transaction_type` column is a `VARCHAR(20)` with no CHECK constraint. The value `STOCK_TAKE` fits within 20 characters and requires no schema change. It is a new string value in an existing column.

No DDL required.

---

#### Implementation Approach

The core structural change: replace the raw `conn = engine.raw_connection()` + `cursor` pattern with `engine.begin()`, and add a before/after balance read to compute the delta for the ledger entry.

**Replace lines 199–258** (from `engine = get_engine()` to the `finally` block) with:

```python
engine = get_engine()

# Clean and prepare data
df_upload = df_upload[df_upload['sku'].notna() & (df_upload['sku'] != '')].copy()
df_upload['sku'] = df_upload['sku'].str.strip().str.upper()

if 'godown_stock_packs' not in df_upload.columns:
    df_upload['godown_stock_packs'] = 0
if 'pack_multiplier' not in df_upload.columns:
    df_upload['pack_multiplier'] = 1

df_upload['godown_stock_packs'] = pd.to_numeric(
    df_upload['godown_stock_packs'], errors='coerce').fillna(0).astype(int)
df_upload['pack_multiplier'] = pd.to_numeric(
    df_upload['pack_multiplier'], errors='coerce').fillna(1).astype(int)

updated_count = 0
not_found = []
skipped_no_change = 0

for idx, row in df_upload.iterrows():
    sku_val = row['sku']
    new_packs = int(row['godown_stock_packs'])
    new_multiplier = int(row['pack_multiplier'])

    try:
        with engine.begin() as conn:
            # 1. Read current balance — must be inside the transaction
            result = conn.execute(
                text("SELECT godown_stock_packs FROM inventory_master WHERE sku = :sku"),
                {"sku": sku_val}
            )
            existing = result.fetchone()

            if existing is None:
                not_found.append(sku_val)
                logger.debug(f"Bulk import: SKU not found in inventory_master: {sku_val}")
                continue  # skip — do not create rows; bulk import is for existing SKUs only

            old_packs = existing[0] if existing[0] is not None else 0
            delta = new_packs - old_packs

            # 2. Update inventory_master with the new absolute value
            conn.execute(
                text("""
                    UPDATE inventory_master
                    SET godown_stock_packs = :packs,
                        pack_multiplier = :mult,
                        last_updated = CURRENT_TIMESTAMP
                    WHERE sku = :sku
                """),
                {"packs": new_packs, "mult": new_multiplier, "sku": sku_val}
            )

            # 3. Write ledger entry only if the balance actually changed
            if delta != 0:
                ledger_type = "ADD" if delta > 0 else "REMOVE"
                conn.execute(
                    text("""
                        INSERT INTO stock_ledger
                            (sku, transaction_type, packs, multiplier,
                             total_pieces_affected, reason)
                        VALUES
                            (:sku, 'STOCK_TAKE', :packs, :mult, :total, :reason)
                    """),
                    {
                        "sku": sku_val,
                        "packs": abs(delta),
                        "mult": new_multiplier,
                        "total": abs(delta) * new_multiplier,
                        "reason": f"Bulk stock take: {old_packs} → {new_packs} packs",
                    }
                )
                updated_count += 1
            else:
                skipped_no_change += 1
                logger.debug(f"Bulk import: {sku_val} unchanged at {old_packs} packs, skipping ledger")

        # engine.begin() commits on clean exit from with block

    except Exception as row_err:
        logger.error(f"Bulk import: failed to process {sku_val}: {row_err}", exc_info=True)
        st.warning(f"⚠️ Failed to update {sku_val}: {row_err}")

clear_inventory_cache()
logger.info(
    f"Bulk import complete: {updated_count} updated, "
    f"{len(not_found)} not found, {skipped_no_change} unchanged"
)
```

Keep the `st.success()`, `st.warning()` for not_found, and `st.rerun()` calls that follow this block — they are unchanged.

**Key design decisions:**

1. **Per-row transactions** — each SKU is its own `engine.begin()` block. If one SKU fails, previous successful SKUs are already committed and their ledger entries are intact. A single transaction wrapping all rows would roll back everything on one bad row, which is worse for a bulk operation.

2. **No ledger entry for unchanged rows** — if the uploaded value equals the current balance, the UPDATE still runs (it's idempotent) but no ledger entry is written. This prevents noise in the audit trail.

3. **`STOCK_TAKE` transaction type** — distinguishes bulk count corrections from manual ADD/REMOVE adjustments in the history tab. The existing `color_action()` function in the UI will not color STOCK_TAKE entries (neither green nor red) — they will display in the default color. This is acceptable and actually appropriate since STOCK_TAKE is neither an addition nor a removal in the strict directional sense.

4. **Not-found SKUs are skipped, not created** — bulk import is for updating existing inventory, not bootstrapping new SKUs. Users who have new SKUs should use Tab 2 (which calls `INSERT OR IGNORE` first).

---

#### Risks

**Medium.** This is the largest code change in Sprint 1.1.

*Risk 1: Large files with many rows.* Each row opens a database connection. For 500 SKUs on SQLite, this is still fast (< 2 seconds) but a progress indicator would improve UX. Acceptable for now — a progress bar can be added without changing the logic.

*Risk 2: File upload reads the entire file into memory.* No change here — the existing `pd.read_excel()` already does this. No new risk introduced.

*Risk 3: The `color_action()` styler on Tab 3.* It maps any non-`ADD` value to red. `STOCK_TAKE` entries will appear red in the history tab. This is mildly incorrect visually. It will be fixed as part of Task 7 (when `STOCK_TAKE` is added to the reason codes palette and the color mapping is extended). It is not a functional defect.

---

#### Acceptance Criteria

- [ ] Bulk import with changed values writes one `STOCK_TAKE` ledger entry per updated SKU
- [ ] Ledger entry `reason` contains both old and new pack count: `"Bulk stock take: N → M packs"`
- [ ] Bulk import with unchanged values writes no ledger entry (skipped_no_change increments)
- [ ] SKUs not in `inventory_master` are skipped and reported in the warning, not created
- [ ] `inventory_master.last_updated` is updated for every processed SKU (changed or unchanged)
- [ ] A failed row does not prevent other rows from being processed
- [ ] Debug `st.write()` output does not appear (verified — removed in Task 1)
- [ ] `Recent History` tab shows `STOCK_TAKE` entries after a bulk import with changes
- [ ] Import summary shows correct counts: updated, not found, unchanged

---

#### Test Cases

| # | Setup | Action | Expected Result |
|---|---|---|---|
| T4.1 | SKU-A has 10 packs. Upload: SKU-A=15 | Import | inventory_master shows 15; ledger: STOCK_TAKE, packs=5, reason="Bulk stock take: 10 → 15 packs" |
| T4.2 | SKU-A has 10 packs. Upload: SKU-A=10 | Import | inventory_master shows 10; no ledger entry; skipped_no_change=1 |
| T4.3 | SKU-A has 10 packs. Upload: SKU-A=3 | Import | inventory_master shows 3; ledger: STOCK_TAKE, packs=7 (absolute delta), reason="Bulk stock take: 10 → 3 packs" |
| T4.4 | Upload contains SKU-NOTEXIST | Import | not_found list includes SKU-NOTEXIST; warning displayed; no inventory_master row created |
| T4.5 | Upload: 3 valid + 1 not-found SKU | Import | 3 valid SKUs updated with ledger entries; 1 not-found reported; all 3 valid commits succeed |
| T4.6 | Upload file with no godown_stock_packs column | Import | Defaults to 0; each existing SKU updated to 0; ledger entries reflect delta from prior balance |
| T4.7 | Check Recent History after bulk import | View Tab 3 | STOCK_TAKE rows are visible for each changed SKU |

---

---

### Task 5 — Connect Demand Planner to Live Inventory

**Priority:** P0 · Effort: Low · Risk: Medium

---

#### Objective

`generate_purchase_plan()` computes net purchase requirements as:

```
to_purchase = gross_requirement - stock_qty
```

`stock_qty` is sourced from an uploaded stock file (`files_dict['stock']`). If no file is uploaded, `plan['stock_qty'] = 0` is used, meaning every SKU is treated as having zero on-hand stock. The business's actual `inventory_master` data is completely ignored.

Add a method `get_stock_for_planning()` to `InventoryService` that returns current on-hand stock from `inventory_master`. Use it as the default stock source when no upload file is provided.

---

#### Files Affected

| File | Change Type |
|---|---|
| `src/core/services/inventory_service.py` | Add `get_stock_for_planning()` method; modify `generate_purchase_plan()` lines 150–163 |
| `src/ui/pages/demand_planner.py` | No change. The UI already passes `files_dict['stock']` as-is. The fix is entirely in the service layer. |

---

#### Database Changes

None. Read-only query against existing `inventory_master` table.

---

#### Implementation Approach

**Step 1: Add `get_stock_for_planning()` to `InventoryService`**

Add after `get_godown_inventory()` (line 295):

```python
def get_stock_for_planning(self):
    """Returns current on-hand stock from inventory_master for purchase planning.

    Converts pack-level godown stock to base units using pack_multiplier,
    then adds shop_stock_pieces. This gives the total available units
    expressed in the same base unit as the Demand Planner's sales data.

    Returns:
        DataFrame with columns: sku, stock_qty
    """
    query = """
    SELECT
        sku,
        (COALESCE(godown_stock_packs, 0) * COALESCE(pack_multiplier, 1))
            + COALESCE(shop_stock_pieces, 0) AS stock_qty
    FROM inventory_master
    WHERE godown_stock_packs > 0 OR shop_stock_pieces > 0
    """
    df = pd.read_sql(query, self.engine)
    df['sku'] = df['sku'].str.upper().str.strip()
    return df
```

**Why total units (not packs):** The Demand Planner aggregates sales at the base unit level (`base_units_sold = qty × pack_qty`) and calculates `gross_requirement` in base units. Subtracting godown_stock_packs (a pack count) from a base unit requirement would produce wrong results. Converting to base units here keeps the units consistent.

**Step 2: Modify `generate_purchase_plan()` lines 150–163**

**Current code:**
```python
# 7. Subtract Current Stock (if provided)
if files_dict.get('stock'):
    stock_df, _ = parse_stock_file(files_dict['stock'])
    if stock_df is not None:
        plan = pd.merge(plan, stock_df, left_on='base_sku', right_on='sku', how='left')
        plan['stock_qty'] = plan['stock_qty'].fillna(0)
    else:
        plan['stock_qty'] = 0
else:
    plan['stock_qty'] = 0
```

**Replace with:**
```python
# 7. Subtract Current Stock
# Priority: uploaded stock file > live inventory_master > 0
if files_dict.get('stock'):
    # User uploaded an explicit stock override — use it as-is
    stock_df, stock_err = parse_stock_file(files_dict['stock'])
    if stock_df is not None:
        logger.info("Using uploaded stock file for planning")
        plan = pd.merge(plan, stock_df, left_on='base_sku', right_on='sku', how='left')
        plan['stock_qty'] = plan['stock_qty'].fillna(0)
    else:
        logger.warning(f"Stock file parse failed ({stock_err}), falling back to live inventory")
        live_stock = self.get_stock_for_planning()
        plan = pd.merge(plan, live_stock, left_on='base_sku', right_on='sku', how='left')
        plan['stock_qty'] = plan['stock_qty'].fillna(0)
else:
    # Default: read from live inventory_master
    logger.info("No stock file uploaded — using live inventory_master for planning")
    live_stock = self.get_stock_for_planning()
    plan = pd.merge(plan, live_stock, left_on='base_sku', right_on='sku', how='left')
    plan['stock_qty'] = plan['stock_qty'].fillna(0)
```

The uploaded file remains fully functional as an override. This is important for cases where the user wants to plan against a specific snapshot (e.g., end-of-day stock as of last night) rather than the live balance.

---

#### Risks

**Medium.** This changes the output of the Demand Planner's most critical calculation for all users who do not upload a stock file (which is likely most users, most of the time).

*Risk 1: Unit mismatch.* The `get_stock_for_planning()` query converts to base units. If a user's `pack_multiplier` in `inventory_master` is stale or incorrect (e.g., was set to 1 when the actual pack size is 6), the stock deduction will be wrong. This is a data quality issue, not a code defect — but it will surface more visibly now that live stock is being used. Acceptable: the data quality problem existed before; now it has consequences that motivate fixing the data.

*Risk 2: SKUs in inventory_master that are not in the planning mapping.* The LEFT JOIN `plan = pd.merge(plan, live_stock, left_on='base_sku', right_on='sku', how='left')` means SKUs in inventory_master with no corresponding sales data will not appear in the plan. This is correct behaviour — if a SKU has no sales, it doesn't need to be purchased.

*Risk 3: Empty inventory.* If `inventory_master` is empty (new install, first use), `get_stock_for_planning()` returns an empty DataFrame. The LEFT JOIN fills `stock_qty` with 0 for all plan rows. This is identical to the prior behaviour and is correct.

---

#### Acceptance Criteria

- [ ] Generating a plan with **no stock file uploaded** uses `inventory_master` stock values
- [ ] Generating a plan with **a stock file uploaded** uses the uploaded file values (existing behaviour preserved)
- [ ] `to_purchase` for a SKU with 100 units in `inventory_master` and `gross_requirement` of 80 is 0 (not 80)
- [ ] `to_purchase` for a SKU with 0 units in `inventory_master` and `gross_requirement` of 80 is 80
- [ ] `get_stock_for_planning()` returns base units (packs × multiplier + shop pieces), not raw packs
- [ ] SKUs with zero stock in `inventory_master` do not appear in `get_stock_for_planning()` result (WHERE clause filters them)
- [ ] Plan still generates correctly when `inventory_master` is empty (no crash, treats all stock as 0)

---

#### Test Cases

| # | Setup | Action | Expected Result |
|---|---|---|---|
| T5.1 | SKU-A: 50 packs × 6 = 300 units in inventory. gross_requirement = 200 | Generate plan, no stock file | to_purchase = 0 (300 > 200) |
| T5.2 | SKU-A: 50 packs × 6 = 300 units in inventory. gross_requirement = 400 | Generate plan, no stock file | to_purchase = 100 (400 - 300) |
| T5.3 | inventory_master is empty | Generate plan, no stock file | Plan generates; to_purchase = gross_requirement for all SKUs (stock_qty = 0) |
| T5.4 | SKU-A: 50 packs in inventory. Upload stock file saying SKU-A = 500 units | Generate plan with stock file | to_purchase uses 500, not the live inventory value |
| T5.5 | Upload a corrupt/unparseable stock file | Generate plan | Falls back to live inventory; logger.warning issued; plan uses inventory_master values |
| T5.6 | Call get_stock_for_planning() directly | Method call | Returns DataFrame with sku and stock_qty columns; stock_qty is in base units |

---

---

## SPRINT 1.2 — COMPLETE EXISTING WORKFLOWS

---

### Task 6 — Add user Attribution Column to stock_ledger

**Priority:** P1 · Effort: Low · Risk: Low

---

#### Objective

Every `stock_ledger` entry is anonymous. When a balance is wrong, there is no way to identify who made the change. Add an `updated_by` column to `stock_ledger`.

This task is done **before** Tasks 7, 8, and 9 because those tasks introduce new write paths. Building those paths after the column exists means they can populate it from day one, avoiding a second migration to retrofit.

Until proper authentication (Stabilization Plan item 3.1) is implemented, `updated_by` will be populated from a configuration value — defaulting to `"system"` if no value is configured.

---

#### Files Affected

| File | Change Type |
|---|---|
| `src/infrastructure/schema.sql` | Add column to `stock_ledger` definition |
| `src/core/services/inventory_service.py` | Update all `INSERT INTO stock_ledger` statements (4 locations) |
| `src/ui/pages/inventory_manager.py` | Pass `updated_by` through from UI to service calls (Tab 2, Tab 4) |

---

#### Database Changes

**Migration required.** The column does not exist in the current schema.

```sql
-- Migration: add updated_by to stock_ledger
ALTER TABLE stock_ledger ADD COLUMN updated_by VARCHAR(100) DEFAULT 'system';
```

This is an additive change. `DEFAULT 'system'` means all existing rows get a sensible value without any data update. The migration is safe to run on a live database.

**Update `schema.sql`** to include the column in the `stock_ledger` CREATE TABLE block so new installs include it:

```sql
CREATE TABLE IF NOT EXISTS stock_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sku VARCHAR(50),
    transaction_type VARCHAR(20),
    packs INT,
    multiplier INT,
    total_pieces_affected INT,
    reason TEXT,
    updated_by VARCHAR(100) DEFAULT 'system',   -- NEW
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

**Where to run the migration:** Add to `src/infrastructure/init_db.py` `_check_and_migrate_existing_db()` as a safe idempotent ALTER:

```python
# In _check_and_migrate_existing_db(), after the product_count check:
with engine.connect() as conn:
    # Add updated_by column if not present (idempotent — SQLite ignores if exists via try/except)
    try:
        conn.execute(text("ALTER TABLE stock_ledger ADD COLUMN updated_by VARCHAR(100) DEFAULT 'system'"))
        conn.commit()
        logger.info("Migration: added updated_by column to stock_ledger")
    except Exception:
        pass  # Column already exists — SQLite raises on duplicate ALTER TABLE
```

---

#### Implementation Approach

**Step 1: Run migration on startup** (as above in init_db.py).

**Step 2: Update all `INSERT INTO stock_ledger` in `inventory_service.py`**

There are four INSERT locations. Each needs `:updated_by` added to the column list and params dict.

Pattern for all four (shown once):
```python
conn.execute(
    text("""
        INSERT INTO stock_ledger
            (sku, transaction_type, packs, multiplier, total_pieces_affected, reason, updated_by)
        VALUES
            (:sku, :action, :p, :m, :tp, :r, :updated_by)
    """),
    {
        "sku": sku, "action": action_type, "p": packs,
        "m": multiplier, "tp": total_pieces, "r": reason,
        "updated_by": updated_by,
    }
)
```

**Step 3: Update method signatures** to accept `updated_by` parameter with default `"system"`:

```python
def manage_godown_stock(self, sku, packs, multiplier, action_type, reason="", updated_by="system"):
def add_stock(self, sku, qty, location, unit_type, reason, updated_by="system"):
def transfer_to_shop(self, sku, packs, multiplier, updated_by="system"):
```

Default `"system"` ensures backward compatibility — no call sites break.

**Step 4: Update bulk import in inventory_manager.py (Task 4's code)**

The ledger INSERT written in Task 4 should also include `updated_by`. Since bulk imports are always performed by an interactive user, use `"bulk_import"` as the value for now:

```python
"updated_by": "bulk_import",
```

**Step 5: Pass username from Tab 2 UI (future-proofing)**

When Stabilization Plan item 3.1 (authentication) is implemented, the username will come from session state. For now, pass the default:

```python
# inventory_manager.py Tab 2 — no change required because default "system" is used
service.manage_godown_stock(sku, num_packs, pack_size, action, reason)
# updated_by defaults to "system" — will be changed when auth is implemented
```

---

#### Risks

**Low.** `ALTER TABLE ... ADD COLUMN ... DEFAULT` on SQLite is instant regardless of table size. The try/except in the migration code makes it idempotent — re-running on a database that already has the column is safe.

One downstream risk: the Tab 3 history display shows all `stock_ledger` columns via `SELECT *`. The `updated_by` column will automatically appear in the dataframe. This is desirable — no UI change needed for the column to be visible.

---

#### Acceptance Criteria

- [ ] `stock_ledger` table has an `updated_by VARCHAR(100) DEFAULT 'system'` column after migration
- [ ] All existing rows have `updated_by = 'system'` after migration
- [ ] New rows from Tab 2 manual adjustment have `updated_by = 'system'`
- [ ] New rows from Tab 4 bulk import have `updated_by = 'bulk_import'`
- [ ] New rows from `transfer_to_shop()` have `updated_by = 'system'`
- [ ] Migration is idempotent — running twice does not error
- [ ] `schema.sql` CREATE TABLE includes the column for new installs
- [ ] Tab 3 history table shows the `updated_by` column without any UI change

---

#### Test Cases

| # | Action | Expected Result |
|---|---|---|
| T6.1 | Run application startup on existing DB without updated_by column | Column is added; existing rows show 'system' |
| T6.2 | Run application startup on DB that already has updated_by column | No error; migration skipped silently |
| T6.3 | ADD 5 packs via Tab 2 | Ledger row: updated_by = 'system' |
| T6.4 | Bulk import via Tab 4 | Ledger rows: updated_by = 'bulk_import' |
| T6.5 | New install with updated schema.sql | stock_ledger includes updated_by column from creation |

---

---

### Task 7 — Reason Code Dropdown for Adjustments

**Priority:** P1 · Effort: Low · Risk: Low

---

#### Objective

The `reason` field in `stock_ledger` is a freetext string. `manage_godown_stock()` accepts any string and writes it verbatim. Over time this produces unqueryable noise ("received", "stock received", "New Stock", "from supplier", "supplier" — all the same event).

Add a `reason_code` column to `stock_ledger` to hold a structured value. Replace the freetext `reason` input in Tab 2 with a dropdown for the reason code plus an optional notes field for freetext context.

This task must be completed **before** Tasks 8 and 9 so the new Transfer to Shop and Shop Adjustment UIs are built with structured reason codes from day one.

---

#### Files Affected

| File | Change Type |
|---|---|
| `src/infrastructure/schema.sql` | Add `reason_code` column to `stock_ledger` |
| `src/core/services/inventory_service.py` | Add `reason_code` parameter to `manage_godown_stock()`, `add_stock()`, `transfer_to_shop()` |
| `src/ui/pages/inventory_manager.py` | Replace freetext reason in Tab 2 with dropdown + notes |
| `src/ui/pages/inventory_manager.py` | Update `color_action()` to handle `STOCK_TAKE` display |

---

#### Database Changes

**Migration required.** Add `reason_code` to `stock_ledger`.

```sql
ALTER TABLE stock_ledger ADD COLUMN reason_code VARCHAR(50) DEFAULT 'ADJUSTMENT';
```

`DEFAULT 'ADJUSTMENT'` is the safest retroactive classification — existing manual ADD/REMOVE rows are likely stock adjustments.

**Update `schema.sql`:**

```sql
CREATE TABLE IF NOT EXISTS stock_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sku VARCHAR(50),
    transaction_type VARCHAR(20),
    packs INT,
    multiplier INT,
    total_pieces_affected INT,
    reason_code VARCHAR(50) DEFAULT 'ADJUSTMENT',   -- NEW
    reason TEXT,
    updated_by VARCHAR(100) DEFAULT 'system',
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

**Add to startup migration in `init_db.py`** (same try/except pattern as Task 6):

```python
try:
    conn.execute(text("ALTER TABLE stock_ledger ADD COLUMN reason_code VARCHAR(50) DEFAULT 'ADJUSTMENT'"))
    conn.commit()
    logger.info("Migration: added reason_code column to stock_ledger")
except Exception:
    pass  # Column already exists
```

---

#### Implementation Approach

**Reason code vocabulary:**

| Code | Meaning | Used By |
|---|---|---|
| `RECEIVED` | Stock received from supplier | ADD operations |
| `SALE` | Sold/dispatched (manual deduction) | REMOVE operations |
| `DAMAGED` | Written off — damaged | REMOVE operations |
| `LOST` | Written off — lost/shrinkage | REMOVE operations |
| `COUNT_CORRECTION` | Manual correction after physical count | ADD or REMOVE |
| `TRANSFER` | Moved between locations | transfer_to_shop() |
| `STOCK_TAKE` | Bulk import from stock take file | Bulk import (Tab 4) |
| `ADJUSTMENT` | General adjustment (catch-all) | Default / legacy |
| `RETURN` | Customer return restocked | ADD operations (Phase 2) |

**Step 1: Update service method signatures**

```python
def manage_godown_stock(self, sku, packs, multiplier, action_type, reason="", 
                         reason_code="ADJUSTMENT", updated_by="system"):
```

```python
def add_stock(self, sku, qty, location, unit_type, reason, 
              reason_code="ADJUSTMENT", updated_by="system"):
```

```python
def transfer_to_shop(self, sku, packs, multiplier, updated_by="system"):
    # transfer_to_shop always uses TRANSFER — no reason_code parameter needed
```

**Step 2: Update all `INSERT INTO stock_ledger` in inventory_service.py**

For `manage_godown_stock()`:
```python
conn.execute(
    text("""
        INSERT INTO stock_ledger
            (sku, transaction_type, packs, multiplier, total_pieces_affected,
             reason_code, reason, updated_by)
        VALUES
            (:sku, :action, :p, :m, :tp, :reason_code, :r, :updated_by)
    """),
    {
        "sku": sku, "action": action_type, "p": packs,
        "m": multiplier, "tp": total_pieces,
        "reason_code": reason_code, "r": reason, "updated_by": updated_by,
    }
)
```

For `transfer_to_shop()` — both ledger rows use hardcoded `TRANSFER`:
```python
conn.execute(
    text("""
        INSERT INTO stock_ledger
            (sku, transaction_type, packs, multiplier, total_pieces_affected,
             reason_code, reason, updated_by)
        VALUES (:sku, 'REMOVE', :p, :m, :tp, 'TRANSFER', 'Moved to Shop', :updated_by)
    """),
    {"sku": sku, "p": packs, "m": multiplier, "tp": pieces, "updated_by": updated_by}
)
conn.execute(
    text("""
        INSERT INTO stock_ledger
            (sku, transaction_type, packs, multiplier, total_pieces_affected,
             reason_code, reason, updated_by)
        VALUES (:sku, 'ADD', :p, :m, :tp, 'TRANSFER', 'Received from Godown', :updated_by)
    """),
    {"sku": sku, "p": packs, "m": multiplier, "tp": pieces, "updated_by": updated_by}
)
```

**Step 3: Update Tab 2 UI in inventory_manager.py**

Replace:
```python
reason = st.text_input("Note / Reference (Optional)")
```

With:
```python
REASON_CODES_BY_ACTION = {
    "ADD":    ["RECEIVED", "COUNT_CORRECTION", "RETURN", "ADJUSTMENT"],
    "REMOVE": ["SALE", "DAMAGED", "LOST", "COUNT_CORRECTION", "ADJUSTMENT"],
}
reason_code = st.selectbox(
    "Reason",
    REASON_CODES_BY_ACTION.get(action, ["ADJUSTMENT"]),
    help="Select the reason for this stock change"
)
reason_note = st.text_input("Notes (Optional)", placeholder="e.g. Invoice #1234, damaged in transit")
```

Update the `manage_godown_stock()` call:
```python
service.manage_godown_stock(sku, num_packs, pack_size, action, reason_note, reason_code)
```

**Step 4: Update `color_action()` to handle STOCK_TAKE**

Current:
```python
def color_action(val):
    color = 'green' if val == 'ADD' else 'red'
    return f'color: {color}; font-weight: bold'
```

Replace with:
```python
def color_action(val):
    colors = {
        'ADD':        'green',
        'REMOVE':     'red',
        'STOCK_TAKE': 'orange',
        'ADJUSTMENT': 'steelblue',
    }
    color = colors.get(val, 'grey')
    return f'color: {color}; font-weight: bold'
```

---

#### Risks

**Low.** Schema change is additive. Default values prevent breaking existing rows. Method signature changes use keyword arguments with defaults — no call sites break.

One UX consideration: restricting reason to a dropdown may frustrate power users who want freetext. The optional `Notes` field retains that flexibility. The reason code gives structure; the notes field gives context. This is the right balance.

---

#### Acceptance Criteria

- [ ] `stock_ledger` has a `reason_code VARCHAR(50)` column after migration
- [ ] Existing rows have `reason_code = 'ADJUSTMENT'` after migration
- [ ] Tab 2 shows a reason code dropdown with context-sensitive options (ADD shows RECEIVED etc., REMOVE shows SALE/DAMAGED etc.)
- [ ] Tab 2 still has an optional freetext Notes field
- [ ] Manual adjustments write the selected reason code to `reason_code` column
- [ ] `transfer_to_shop()` writes `reason_code = 'TRANSFER'` (hardcoded, no user selection needed)
- [ ] Bulk import (Task 4) writes `reason_code = 'STOCK_TAKE'`
- [ ] Tab 3 history shows `reason_code` column
- [ ] STOCK_TAKE entries appear in orange; ADD green; REMOVE red in Tab 3
- [ ] `schema.sql` includes `reason_code` for new installs

---

#### Test Cases

| # | Action | Expected Result |
|---|---|---|
| T7.1 | ADD packs — select "RECEIVED" + notes "Invoice 123" | Ledger: reason_code='RECEIVED', reason='Invoice 123' |
| T7.2 | REMOVE packs — select "DAMAGED" + no notes | Ledger: reason_code='DAMAGED', reason='' |
| T7.3 | REMOVE packs — action dropdown shows only removal-relevant codes | SALE, DAMAGED, LOST, COUNT_CORRECTION, ADJUSTMENT visible; RECEIVED not visible |
| T7.4 | ADD packs — action dropdown shows only addition-relevant codes | RECEIVED, COUNT_CORRECTION, RETURN, ADJUSTMENT visible; SALE/DAMAGED not visible |
| T7.5 | Switch action from ADD to REMOVE | Reason code dropdown options change dynamically |
| T7.6 | Run startup on existing DB without reason_code column | Column added; existing rows show 'ADJUSTMENT' |
| T7.7 | Tab 3 history after Task 4 bulk import | STOCK_TAKE rows appear in orange |

---

---

### Task 8 — Transfer to Shop UI

**Priority:** P1 · Effort: Low · Risk: Low

---

#### Objective

`transfer_to_shop()` in `InventoryService` is fully implemented, atomic, and correct (fixed in Stabilization Plan item 1.1). It is not accessible from the UI. Users who operate a physical shop stocking from the godown have no supported workflow for this movement.

Add a new tab "🔄 Transfer to Shop" to the Inventory Manager. The service layer requires no changes.

---

#### Files Affected

| File | Change Type |
|---|---|
| `src/ui/pages/inventory_manager.py` | Add Tab 5, update `st.tabs()` call, add transfer form |

---

#### Database Changes

None. `transfer_to_shop()` already writes to `inventory_master` and `stock_ledger`. Task 6 and 7 have already added `updated_by` and `reason_code` columns — this task populates them.

---

#### Implementation Approach

**Step 1: Update `st.tabs()` call** (line 47):

```python
# Before:
tab1, tab2, tab3, tab4 = st.tabs([
    "📊 Current Balances", "➕ Update Stock", "📜 Recent History", "📥 Bulk Update"
])

# After:
tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "📊 Current Balances", "➕ Update Stock", "📜 Recent History",
    "📥 Bulk Update", "🔄 Transfer to Shop"
])
```

**Step 2: Add Tab 5 block** (insert before the outer `except` at the bottom of `render()`):

```python
# --- TAB 5: TRANSFER TO SHOP ---
with tab5:
    try:
        st.subheader("🔄 Transfer Stock from Godown to Shop")
        st.info(
            "Moves packs from Godown inventory and converts them to pieces in Shop inventory. "
            "Both the godown deduction and shop addition are recorded in the audit trail."
        )

        df_transfer = service.get_godown_inventory()

        # Only show SKUs that have godown stock to transfer
        df_with_stock = df_transfer[df_transfer['godown_stock_packs'] > 0]

        if df_with_stock.empty:
            st.warning("No SKUs have godown stock available to transfer.")
        else:
            with st.form("transfer_form"):
                c1, c2 = st.columns(2)
                transfer_sku = c1.selectbox(
                    "Select SKU",
                    df_with_stock['sku'].unique(),
                    help="Only SKUs with godown stock are shown"
                )
                
                # Look up current stock and multiplier for the selected SKU
                sku_row = df_with_stock[df_with_stock['sku'] == transfer_sku].iloc[0]
                current_godown_packs = int(sku_row['godown_stock_packs'])
                
                try:
                    default_mult = int(sku_row['pack_multiplier'])
                except (ValueError, TypeError):
                    default_mult = 1
                
                transfer_packs = c2.number_input(
                    "Packs to Transfer",
                    min_value=1,
                    max_value=current_godown_packs,
                    step=1,
                    help=f"Maximum: {current_godown_packs} packs currently in godown"
                )
                
                pack_size = st.number_input(
                    "Pieces per Pack",
                    min_value=1,
                    value=default_mult,
                    help="Used to calculate pieces added to shop"
                )
                
                # Preview the effect before submitting
                pieces_to_add = transfer_packs * pack_size
                st.markdown(
                    f"**Preview:** Move **{transfer_packs} packs** from Godown → "
                    f"Add **{pieces_to_add} pieces** to Shop"
                )

                submitted = st.form_submit_button("✅ Confirm Transfer", type="primary")
                
                if submitted:
                    try:
                        logger.info(
                            f"Transfer to shop: {transfer_sku}, "
                            f"{transfer_packs} packs × {pack_size}"
                        )
                        service.transfer_to_shop(transfer_sku, transfer_packs, pack_size)
                        clear_inventory_cache()
                        st.success(
                            f"✅ Transferred {transfer_packs} packs "
                            f"({pieces_to_add} pieces) of {transfer_sku} to Shop."
                        )
                        st.rerun()
                    except Exception as e:
                        logger.error(
                            f"Transfer to shop failed for {transfer_sku}: {str(e)}",
                            exc_info=True
                        )
                        st.error(f"Transfer failed: {str(e)}")

    except Exception as e:
        logger.error(f"Error in transfer to shop tab: {str(e)}", exc_info=True)
        st.error(f"Error loading transfer form: {str(e)}")
```

**Note on `max_value`:** The `number_input` uses `max_value=current_godown_packs` as a UI hint, but this is not a hard guard — Streamlit's `number_input` with `max_value` does enforce the maximum in the widget. Combined with `transfer_to_shop()` calling the underlying inventory which is subject to the same ledger writes, this is sufficient. `transfer_to_shop()` does not currently have its own negative stock guard (unlike `manage_godown_stock()` which got one in Task 3). The UI `max_value` prevents the common case; a Task 3-equivalent guard in `transfer_to_shop()` would be belt-and-suspenders and is a low-risk addition if desired.

---

#### Risks

**Low.** The service method is already implemented and reviewed. This is a pure UI addition.

One UX note: the SKU dropdown shows only godown stock packs — it may not reflect changes mid-session if another user updates stock concurrently. The `st.rerun()` on submit refreshes the view. For 1–10 users this is acceptable.

---

#### Acceptance Criteria

- [ ] Tab 5 "🔄 Transfer to Shop" is visible in the Inventory Manager
- [ ] Only SKUs with `godown_stock_packs > 0` appear in the SKU dropdown
- [ ] Packs input has `max_value` set to current godown packs for the selected SKU
- [ ] Preview text shows the expected pieces to be added to shop before submit
- [ ] On submit: `godown_stock_packs` decreases by the transferred packs
- [ ] On submit: `shop_stock_pieces` increases by `packs × multiplier`
- [ ] Two ledger rows are written: REMOVE (reason_code=TRANSFER) and ADD (reason_code=TRANSFER)
- [ ] `last_updated` is updated for the SKU
- [ ] Attempting to transfer more packs than in stock is prevented by the `max_value` UI constraint
- [ ] Tab 3 Recent History shows the TRANSFER entries after submission

---

#### Test Cases

| # | Setup | Action | Expected Result |
|---|---|---|---|
| T8.1 | SKU-A: 10 godown packs, 5 shop pieces, multiplier=6 | Transfer 3 packs | godown=7, shop=5+18=23; two TRANSFER ledger rows |
| T8.2 | SKU-A: 10 godown packs | Transfer 10 packs (all stock) | godown=0; SKU no longer appears in dropdown on next render |
| T8.3 | All SKUs have 0 godown packs | Open Tab 5 | "No SKUs have godown stock available" message; no form shown |
| T8.4 | Transfer 5 packs with multiplier=1 | Submit | Shop increases by 5 pieces (not 5 × default); ledger shows multiplier=1 |
| T8.5 | Check Recent History after transfer | Tab 3 | Two rows for the SKU: one REMOVE TRANSFER, one ADD TRANSFER |

---

---

### Task 9 — Shop Stock Adjustment UI

**Priority:** P1 · Effort: Low · Risk: Low

---

#### Objective

`shop_stock_pieces` exists in `inventory_master` and is correctly tracked. There is no UI to adjust it directly. Shop stock can only change via `transfer_to_shop()` (Task 8) or via bulk import (Tab 4, no audit trail until Task 4 was completed). There is no way to record a shop sale, shop damage, or shop count correction through the UI.

Add shop stock adjustment capability to the existing Tab 2 "Update Stock" by adding a "Location" selector. When "Shop" is selected, the form adjusts `shop_stock_pieces` and records the ledger entry appropriately.

---

#### Files Affected

| File | Change Type |
|---|---|
| `src/core/services/inventory_service.py` | Add `manage_shop_stock()` method (or extend `manage_godown_stock()`) |
| `src/ui/pages/inventory_manager.py` | Update Tab 2 form to include Location selector |

---

#### Database Changes

None. `shop_stock_pieces` already exists in `inventory_master`. The `stock_ledger` schema is unchanged — the same columns capture shop adjustments.

---

#### Implementation Approach

**Option A (Preferred): Add `manage_shop_stock()` as a distinct service method**

Keeps concerns separated. Godown stock is in packs; shop stock is in pieces. The unit semantics are different and mixing them in one method creates ambiguity.

**Add to `inventory_service.py`** (after `manage_godown_stock()`):

```python
def manage_shop_stock(self, sku, pieces, action_type, reason_code="ADJUSTMENT",
                      reason="", updated_by="system"):
    """Adds or removes pieces from shop_stock_pieces with a ledger entry.

    Args:
        sku: Product SKU
        pieces: Number of pieces to add or remove
        action_type: 'ADD' or 'REMOVE'
        reason_code: Structured reason code (SALE, DAMAGED, COUNT_CORRECTION, etc.)
        reason: Optional freetext notes
        updated_by: Username for audit trail

    Raises:
        DataValidationException: If REMOVE would drive shop_stock_pieces below zero
    """
    piece_change = pieces if action_type == "ADD" else -pieces

    with self.engine.begin() as conn:
        # Ensure row exists
        conn.execute(
            text("INSERT OR IGNORE INTO inventory_master (sku) VALUES (:sku)"),
            {"sku": sku}
        )

        # Guard against negative shop stock
        if action_type == "REMOVE":
            result = conn.execute(
                text("SELECT shop_stock_pieces FROM inventory_master WHERE sku = :sku"),
                {"sku": sku}
            )
            row = result.fetchone()
            current_pieces = row[0] if row and row[0] is not None else 0
            if current_pieces < pieces:
                raise DataValidationException(
                    f"Cannot remove {pieces} pieces: only {current_pieces} in shop for {sku}. "
                    "Operation cancelled."
                )

        # Apply change
        conn.execute(
            text("""
                UPDATE inventory_master
                SET shop_stock_pieces = shop_stock_pieces + :pieces,
                    last_updated = CURRENT_TIMESTAMP
                WHERE sku = :sku
            """),
            {"pieces": piece_change, "sku": sku}
        )

        # Write ledger entry (multiplier=1: shop stock is already in pieces, not packs)
        conn.execute(
            text("""
                INSERT INTO stock_ledger
                    (sku, transaction_type, packs, multiplier, total_pieces_affected,
                     reason_code, reason, updated_by)
                VALUES
                    (:sku, :action, :pieces, 1, :pieces, :reason_code, :r, :updated_by)
            """),
            {
                "sku": sku, "action": action_type, "pieces": pieces,
                "reason_code": reason_code, "r": reason, "updated_by": updated_by,
            }
        )
        # engine.begin() auto-commits on clean exit
```

**Note on ledger fields:** For shop adjustments, `packs` and `total_pieces_affected` are both set to `pieces` (the unit input), and `multiplier` is `1`. This accurately reflects that shop stock is tracked in pieces, not packs. The ledger can distinguish godown vs shop entries by the reason_code: godown adjustments use RECEIVED/SALE/DAMAGED; shop adjustments should use SALE/DAMAGED/COUNT_CORRECTION with context from the notes field.

**Update Tab 2 UI in inventory_manager.py:**

After the existing `action = c2.selectbox("Action", ["ADD", "REMOVE"])`, add a location selector and dynamically adjust the form:

```python
with st.form("stock_form"):
    c1, c2, c3 = st.columns(3)
    sku = c1.selectbox("Select SKU", df_for_select['sku'].unique())
    location = c2.selectbox("Location", ["Godown", "Shop"])
    action = c3.selectbox("Action", ["ADD", "REMOVE"])

    current_row = df_for_select[df_for_select['sku'] == sku].iloc[0]

    if location == "Godown":
        try:
            default_mult = int(current_row['pack_multiplier'])
        except (ValueError, TypeError):
            default_mult = 1

        c4, c5 = st.columns(2)
        num_packs = c4.number_input("Number of Packs", min_value=1, step=1)
        pack_size = c5.number_input("Pieces per Pack", min_value=1, value=default_mult)
        
        current_balance = int(current_row['godown_stock_packs'])
        st.caption(f"Current godown balance: **{current_balance} packs**")

        REASON_CODES_BY_ACTION = {
            "ADD":    ["RECEIVED", "COUNT_CORRECTION", "ADJUSTMENT"],
            "REMOVE": ["SALE", "DAMAGED", "LOST", "COUNT_CORRECTION", "ADJUSTMENT"],
        }
        reason_code = st.selectbox("Reason", REASON_CODES_BY_ACTION[action])
        reason_note = st.text_input("Notes (Optional)")

        if st.form_submit_button("Update Godown Stock"):
            try:
                service.manage_godown_stock(
                    sku, num_packs, pack_size, action, reason_note, reason_code
                )
                clear_inventory_cache()
                st.success(f"✅ {action} {num_packs} packs for {sku}")
                st.rerun()
            except Exception as e:
                st.error(f"Update failed: {str(e)}")

    else:  # Shop
        # Show current shop balance for reference
        df_with_shop = service.get_inventory_status()
        shop_row = df_with_shop[df_with_shop['sku'] == sku]
        current_shop_pieces = int(shop_row['shop_stock_pieces'].iloc[0]) if not shop_row.empty else 0
        
        num_pieces = st.number_input("Number of Pieces", min_value=1, step=1)
        st.caption(f"Current shop balance: **{current_shop_pieces} pieces**")

        SHOP_REASON_CODES = {
            "ADD":    ["COUNT_CORRECTION", "RETURN", "ADJUSTMENT"],
            "REMOVE": ["SALE", "DAMAGED", "LOST", "COUNT_CORRECTION", "ADJUSTMENT"],
        }
        reason_code = st.selectbox("Reason", SHOP_REASON_CODES[action])
        reason_note = st.text_input("Notes (Optional)")

        if st.form_submit_button("Update Shop Stock"):
            try:
                service.manage_shop_stock(sku, num_pieces, action, reason_code, reason_note)
                clear_inventory_cache()
                st.success(f"✅ {action} {num_pieces} pieces for {sku} (Shop)")
                st.rerun()
            except Exception as e:
                st.error(f"Update failed: {str(e)}")
```

---

#### Risks

**Low.** The new `manage_shop_stock()` method is structurally identical to the fixed `manage_godown_stock()` and follows all the same patterns. The UI change extends the existing Tab 2 form rather than replacing it.

One UX consideration: Streamlit forms rebuild when a selectbox inside the form changes. The Location selector will cause the form to re-render when switched between Godown and Shop. This is normal Streamlit behaviour and is acceptable.

`get_inventory_status()` is called inside the Shop branch to get the current shop pieces balance for display. This is a read-only query and adds minimal overhead.

---

#### Acceptance Criteria

- [ ] Tab 2 has a "Location" dropdown: "Godown" and "Shop"
- [ ] When "Godown" is selected, the form shows packs + multiplier inputs (existing behaviour)
- [ ] When "Shop" is selected, the form shows a pieces input
- [ ] Current balance is shown as a caption for both locations
- [ ] Shop ADD operation increments `shop_stock_pieces` and writes a ledger entry
- [ ] Shop REMOVE operation decrements `shop_stock_pieces` and writes a ledger entry
- [ ] Shop REMOVE blocked if pieces > current `shop_stock_pieces` (DataValidationException)
- [ ] Reason codes are contextually appropriate for each location and action
- [ ] Godown operations are functionally unchanged
- [ ] Ledger entries for shop operations are visible in Tab 3 Recent History

---

#### Test Cases

| # | Setup | Action | Expected Result |
|---|---|---|---|
| T9.1 | SKU-A: 20 shop pieces | Shop ADD 10 pieces, reason=COUNT_CORRECTION | shop_stock_pieces=30; ledger: ADD, packs=10, multiplier=1, reason_code=COUNT_CORRECTION |
| T9.2 | SKU-A: 20 shop pieces | Shop REMOVE 5 pieces, reason=SALE | shop_stock_pieces=15; ledger: REMOVE, packs=5, reason_code=SALE |
| T9.3 | SKU-A: 5 shop pieces | Shop REMOVE 10 pieces | Error: "Cannot remove 10 pieces: only 5 in shop"; balance unchanged |
| T9.4 | Location = Godown | Submit godown ADD | Works exactly as before Task 9; no regression |
| T9.5 | Location = Shop, action = ADD | Reason dropdown | Shows COUNT_CORRECTION, RETURN, ADJUSTMENT — not SALE or DAMAGED |
| T9.6 | Location = Shop, action = REMOVE | Reason dropdown | Shows SALE, DAMAGED, LOST, COUNT_CORRECTION, ADJUSTMENT — not RECEIVED |
| T9.7 | After shop adjustment | Check Tab 3 | Ledger row visible with correct reason_code and pieces values |

---

---

## Sprint 1.1 + 1.2 Completion Checklist

### Sprint 1.1 — All tasks done when:

- [ ] No debug output in Tab 4 UI (Task 1)
- [ ] `manage_godown_stock()` uses `engine.begin()` (Task 2)
- [ ] REMOVE operations blocked when packs > current balance (Task 3)
- [ ] Bulk import writes `STOCK_TAKE` ledger entries for every changed SKU (Task 4)
- [ ] Demand Planner reads from `inventory_master` when no stock file uploaded (Task 5)

### Sprint 1.2 — All tasks done when:

- [ ] `stock_ledger` has `updated_by` column; populated on every write (Task 6)
- [ ] `stock_ledger` has `reason_code` column; Tab 2 shows structured dropdown (Task 7)
- [ ] Tab 5 "Transfer to Shop" is functional and writes TRANSFER ledger entries (Task 8)
- [ ] Tab 2 has Location selector; shop stock adjustments work with ledger audit trail (Task 9)

### Phase 1 Sprint 1.1 + 1.2 Definition of Done:

- Every stock change — manual, bulk, or transfer — has a ledger entry
- No operation can drive any stock balance below zero
- The Demand Planner uses live inventory by default
- The audit trail shows who made a change and why (structured reason code)
- All inventory movements have a supported UI path
- No debug output in the production interface

---

## Schema State at End of Sprint 1.2

```sql
CREATE TABLE IF NOT EXISTS stock_ledger (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    sku                  VARCHAR(50),
    transaction_type     VARCHAR(20),   -- 'ADD', 'REMOVE', 'STOCK_TAKE'
    packs                INT,
    multiplier           INT,
    total_pieces_affected INT,
    reason_code          VARCHAR(50) DEFAULT 'ADJUSTMENT',   -- added Task 7
    reason               TEXT,
    updated_by           VARCHAR(100) DEFAULT 'system',      -- added Task 6
    timestamp            DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

`inventory_master` schema is **unchanged** across all Sprint 1.1 and 1.2 tasks. All changes are additive columns on `stock_ledger` only.
