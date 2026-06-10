# Supplier Master Implementation Plan — Principal Engineer Review

**Reviewer Role:** Principal Engineer  
**Document reviewed:** SUPPLIER_MASTER_IMPLEMENTATION_PLAN.md  
**Date:** 2026-06-10  
**Verdict:** Yes with modifications — see Section 9

---

## Scoring Summary

| Area | Score | Comment |
|---|---|---|
| Problem statement | ✅ Correct | The right problem is being solved |
| Schema design | ⚠️ Mostly correct | One structural flaw, one mismatch |
| Migration strategy | ❌ Broken | Seeding guard logic fails in the most likely real scenario |
| Service layer design | ⚠️ Over-engineered | Two dead methods, one unnecessary return-value change |
| UI design | ❌ Two Streamlit antipatterns | Same bugs we just fixed are being re-introduced |
| Integration design | ⚠️ One unnecessary complexity | Denorm column sync is pointless work |
| Risk coverage | ✅ Good | Most risks are correctly identified |
| Test cases | ✅ Thorough | Nothing significant missing |

---

## Issue 1 — Seeding Guard Logic Is Broken in the Most Likely Scenario

**Severity: High**

**Rationale:**

The seeding migration uses this guard: `SELECT COUNT(*) FROM supplier_master — if > 0, skip`.

The plan's own analysis (Section 2.1) confirms that the `'suppier_code'` typo in `migration_script.py` means that on any database populated from a correctly-spelled Excel column, `product_master.supplier_code` is NULL for every single row. That is the normal state of any existing database that was loaded via the onboarding wizard with a properly-spelled Excel file.

The seeding logic reads DISTINCT supplier_codes from product_master, finds zero non-NULL rows, inserts nothing, and leaves `supplier_master` empty. The guard checks `COUNT(*) FROM supplier_master` — which is 0 — and concludes "not seeded yet." On every subsequent startup it runs again, finds zero non-NULL codes again, inserts nothing again, and loops forever. The migration never terminates its own condition because it found nothing to seed.

This is not a corner case. It is the expected state of the production database given the typo analysis the plan itself documents.

**Recommendation:**

The seeding guard must track completion independently of what it found. Use a separate boolean: run once, mark done regardless of how many rows were inserted. Options:

1. Add a `schema_migrations` table (a standard approach) with a row per completed migration and a unique migration name. The seeding function inserts `('seed_supplier_master_v1', CURRENT_TIMESTAMP)` on completion and checks for that row as the guard.
2. Simpler: add a `_seed_completed` INTEGER column to supplier_master itself as a sentinel, OR use a dedicated one-row config table that already exists in the application.
3. Simplest: check for the existence of the supplier_master table AND whether `product_master` has any non-null supplier_codes at all. If none exist, log a clear message and mark the migration as "completed with 0 rows — manual entry required" using approach 1 or 2.

The typo in `migration_script.py` must also be fixed **in this sprint, not deferred**. See Issue 10.

---

## Issue 2 — Denormalized `product_master.supplier` Column Maintenance Is Pointless Complexity

**Severity: Medium**

**Rationale:**

The plan retains `product_master.supplier` (freetext) as a denormalized cache of the supplier name, with `update_supplier()` syncing it on every name change. The stated justification is: "keeps existing queries that GROUP BY supplier working without a JOIN."

The only query that does `GROUP BY supplier` is `generate_purchase_plan()`. That query is already being rewritten in this sprint (Section 8.1) to JOIN `supplier_master` on `supplier_code`. Once that rewrite is done, nothing reads `product_master.supplier` for grouping purposes. The denormalized column is being maintained for the benefit of a query that is simultaneously being migrated away from needing it.

This creates an ongoing obligation: every future `update_supplier()` call must fire a second UPDATE on `product_master`. Every future code path that writes to product_master must remember to keep `supplier` in sync. The migration_script.py onboarding wizard (which already has the typo problem) will overwrite `product_master.supplier` with whatever is in the Excel file on any catalog reimport, silently breaking the sync.

The denormalized column is a maintenance trap with no surviving consumer after the one query that needed it is fixed.

**Recommendation:**

Drop the denormalized sync entirely. In `generate_purchase_plan()`, after the supplier_master merge, use `supplier_master.name` (returned as a new column from the lead time merge) as the grouping and display label. The `product_master.supplier` column can remain as a historical data field with no active maintenance — it is stale by definition and that is acceptable. Remove the `UPDATE product_master SET supplier = :name` from `update_supplier()`. Remove the section of `add_product()` that auto-populates `data['supplier']` from the master. This eliminates the sync obligation entirely.

---

## Issue 3 — Return Signature Change Is a Breaking Change for an Informational Feature

**Severity: Medium**

**Rationale:**

The plan changes `generate_purchase_plan()` from returning `(plan, orphans)` to returning `(plan, orphans, lead_time_used_df)`. Section 10 correctly identifies this as "the highest-risk breaking change in this sprint" — it crashes the Demand Planner with `ValueError: too many values to unpack` if the service is updated before the UI.

The change is made entirely to power an informational expander ("📊 Lead Times Applied per Supplier"). After the merge in step 5.5, `plan_df` already has a `lead_time_days` column per row. A `lead_time_source` column (value: "Supplier Master" or "Default") can be added to `plan_df` directly in the same step. The UI then reads `plan_df[['supplier_code', 'lead_time_days', 'lead_time_source']].drop_duplicates()` from the first return value. No signature change needed. No breaking change. No atomic-commit requirement.

**Recommendation:**

Add `lead_time_source` as a column in `plan_df` at the merge step. Keep the return signature as `(plan, orphans)`. The expander data comes from `plan_df` directly. The test case T15 (return value unpack does not crash) becomes irrelevant — there is nothing to break.

---

## Issue 4 — `st.caption` Inside Form and Checkbox Confirmation Are Streamlit Antipatterns

**Severity: Medium**

**Rationale:**

**Antipattern 1 (Section 10, Risk R6 mitigation):** The plan proposes `st.caption(f"Will be stored as: {input.upper().strip()}")` below the supplier_code text_input. The supplier_code input is inside `st.form("add_supplier_form")`. Inside a Streamlit form, no widget triggers a rerender until submission. The caption will show the normalised version of whatever was in the input at the previous render — either empty string on first render, or the last submitted value. The preview will never reflect what the user is currently typing. This is the exact Stale-Inside-Form bug that was just fixed across three places in the inventory manager during the Defect 1 and 2 fixes. Moving the `supplier_code` text_input outside the form (key: `"add_supplier_code"`) and referencing it in the caption would work correctly, with the form containing only the remaining fields.

**Antipattern 2 (Section 7.3 Sub-section B):** The plan proposes a `st.checkbox("I confirm...")` inside `st.form("deactivate_form")` with the statement "Checkbox must be checked to enable submit." In Streamlit, you cannot conditionally enable or disable a `st.form_submit_button` based on the state of a widget inside the same form — the submit button is always clickable regardless of other form widget states. The form would submit regardless of checkbox state, and validation would have to happen on submission with an error shown if unchecked. The checkbox confirmation should be placed OUTSIDE the form so it triggers a rerender and the submit button (or a separate non-form button) can visibly appear/disappear based on the checkbox state.

**Recommendation:**

For the code preview: move `supplier_code` text_input outside the form. For the deactivation confirmation: place the `st.checkbox` outside any form, with the "Deactivate" button appearing conditionally (`if confirmed: st.button("Deactivate")`) or enabled/disabled based on session state. This is consistent with the fix pattern already established in the inventory manager.

---

## Issue 5 — `get_lead_times_map()` and `update_product_supplier()` Are Dead Methods

**Severity: Low**

**Rationale:**

`get_lead_times_map()` (Section 5.1) returns `{supplier_code: lead_time_days, ...}` and is described as "Used by `generate_purchase_plan()` to look up lead times without a JOIN on every row." But Section 8.1 describes `generate_purchase_plan()` using a direct SQL query + `pd.merge()` — not `get_lead_times_map()`. The method is defined in SupplierService but has no described caller in the actual implementation.

`update_product_supplier(sku, supplier_code)` (Section 6.5) is explicitly described as "For future use by the supplier edit workflow." It has no caller in this sprint. The plan adds a public method with no current consumer.

The Roadmap's own implementation principle #5 states: "No orphaned code. Dead public methods with complex behaviour are maintenance traps." The plan violates its own stated principle twice in the same sprint.

**Recommendation:**

Remove `get_lead_times_map()` from the SupplierService spec. The lead time lookup is done in `generate_purchase_plan()` via SQL query + merge and does not need a separate service method. Remove `update_product_supplier()` from the CatalogService spec. It belongs in the PO sprint (2.2) when a concrete caller exists. Do not add it now.

---

## Issue 6 — `min_order_qty` in supplier_master Is Premature

**Severity: Low**

**Rationale:**

Section 1.2 explicitly lists "MOQ constraints per SKU — Out of Scope (Sprint 2.2)." Sprint 2.2's `purchase_order_lines` table will have `qty_ordered` and likely per-SKU MOQ constraints. The supplier-level `min_order_qty` added in this sprint has no consumer, no integration, no validation, and no UI behaviour beyond storing the value. When Sprint 2.2 defines its MOQ model, it will find a pre-existing supplier-level field with unclear semantics relative to its new SKU-level fields.

`payment_terms` has the same issue — it's freetext with no integration anywhere in this sprint or the next.

**Recommendation:**

Remove `min_order_qty` from the supplier_master schema. It belongs in Sprint 2.2 where its semantics can be defined alongside the PO model. `payment_terms` can stay — it's genuinely informational and has no future semantic conflict risk. Remove `min_order_qty` from the Add Supplier form, the validation rules, and the test cases.

---

## Issue 7 — VARCHAR Length Mismatch Between Tables

**Severity: Low**

**Rationale:**

`supplier_master.supplier_code` is defined as `VARCHAR(50)`. `product_master.supplier_code` is defined as `VARCHAR(100)` (from the existing schema). SQLite does not enforce VARCHAR lengths — both columns accept any string length regardless of the declared size. However, the mismatch is confusing in schema documentation, and if any existing product has a supplier_code longer than 50 characters, the service-layer validation ("Max 50 chars") added by this sprint will start rejecting a supplier code that was previously accepted. This is a behavior change with no warning.

**Recommendation:**

Align both columns to the same length declaration. Since `product_master.supplier_code` already exists at `VARCHAR(100)`, define `supplier_master.supplier_code` as `VARCHAR(100)` as well. Update the service-layer max length validation to 100. SQLite won't enforce it at the DB level anyway, but the documentation is consistent.

---

## Issue 8 — Deactivation Workflow Blocks Entire Feature Without a Bulk Reassign Path

**Severity: Low**

**Rationale:**

The deactivation guard (Section 7.3 Sub-section B) correctly prevents deactivation when products are assigned. But the only mechanism for reassigning products is editing each one individually through the Data Manager product edit form. For a business with 50 products under one supplier, reassigning them one-by-one is operationally unacceptable.

The plan acknowledges the immutable PK problem (Risk R6) and says "deactivation + recreation is the correct workflow" — but then provides no UI path to bulk-reassign the products from the old code to the new one. The deactivation guard effectively makes the feature unusable in the scenario it's designed for.

**Recommendation:**

Add `get_products_for_supplier()` result to the deactivation view (already planned — this part is correct). Add a "Reassign all to:" selectbox beneath the product list, populated with other active suppliers. Clicking "Reassign and Deactivate" executes the bulk UPDATE in a single transaction, then deactivates. This is a two-query operation inside `engine.begin()` and eliminates the need for per-product editing. This should be part of this sprint, not deferred, because without it the deactivation feature is nearly unusable.

---

## Issue 9 — Tab 3 Has Two Selectboxes for the Same Concept

**Severity: Low**

**Rationale:**

Section 7.3 describes Tab 3 with two separate selectboxes: one for Edit (key: `"tab3_edit_supplier_code"`) and one for Deactivate (key: `"tab3_deactivate_supplier_code"`). Both select a supplier. The user sees two "Select Supplier" dropdowns on the same tab, which is confusing and redundant.

**Recommendation:**

Use a single selectbox at the top of Tab 3 (key: `"tab3_supplier_code"`). Below it, show the supplier's details and two distinct action areas: an edit form and a deactivate section. Both sections respond to the same selection. This is simpler, uses one less widget, and is more consistent with standard master data management UI patterns.

---

## Issue 10 — migration_script.py Typo Is a Dependency, Not Out-of-Scope

**Severity: Medium**

**Rationale:**

Section 9.5 states: "The typo in migration_script.py must also be fixed as a separate item (outside this sprint's scope but documented here as a dependency)."

This is incorrect prioritisation. The typo means that on any database bootstrapped via the Onboarding Wizard, `product_master.supplier_code` is NULL for every row. It also means that any catalog reimport (running the Onboarding Wizard again) will set `supplier_code` back to NULL for every product, silently destroying all supplier assignments made through the new UI. The Supplier Master feature is worthless if a routine catalog refresh wipes every product-to-supplier assignment.

The fix is a single line change in the rename map: `'suppier_code': 'supplier_code'` → `'supplier_code': 'supplier_code'` (or simply removing the key since no rename is needed). This is a 30-second fix. Deferring it makes the entire sprint's value conditional on users never re-running the onboarding wizard.

**Recommendation:**

Fix `migration_script.py` in Step 1 of the implementation order, before anything else. It is not scope creep — it is a prerequisite for the feature to have lasting effect.

---

## Issue 11 — Onboarding Wizard Is Not Addressed

**Severity: Low**

**Rationale:**

`onboarding_wizard.py` adds products during initial setup, using `migration_script.py` underneath. The plan only addresses Data Manager's product add form for supplier validation. The Onboarding Wizard bypasses `CatalogService.add_product()` entirely (it uses `to_sql()` directly in `migration_script.py`). This means products imported via the wizard will never have their `supplier_code` validated against `supplier_master`, regardless of what validation is added to `add_product()`.

**Recommendation:**

Acknowledge this as an explicit limitation in the plan: "Products imported via the Onboarding Wizard bypass supplier validation. After any wizard import, the user should visit the Supplier Manager to verify supplier assignments." The longer-term fix (migrating the wizard to use service methods) belongs in a separate cleanup task.

---

## Issue 12 — `PRAGMA foreign_keys = ON` Should Be Enabled Now

**Severity: Low**

**Rationale:**

The plan acknowledges that SQLite FK constraints are only enforced when `PRAGMA foreign_keys = ON` is set, calls it out as a gap, and defers enabling it because it "could introduce FK violations from legacy data." But the migration steps in this plan already clean up the data (normalise supplier_codes, remove NULLs from seeding). After the migrations run, the data should be clean enough to enable FK enforcement. The fix is one line in `database.py`. Deferring it indefinitely perpetuates the very condition that caused the original supplier freetext problem.

**Recommendation:**

Enable `PRAGMA foreign_keys = ON` in `get_engine()` (or in a SQLAlchemy event listener on `connect`) in this sprint. Before enabling it, run a validation query to confirm no existing FK violations. If violations exist (NULL supplier_codes with no parent in supplier_master), the migration steps should handle them before the pragma is enabled. The pragma is the last step, after all data is clean.

---

## Verdict

**Would you implement this plan exactly as written?**

### No. Yes with modifications.

The plan is well-structured and solves the right problem. The schema is fundamentally correct. The test cases are solid. The implementation order is sensible. None of the core design decisions are wrong.

But there are four changes I would require before approving implementation:

**Required (would block implementation):**

1. **Fix the seeding guard** (Issue 1). The `COUNT(*) > 0` guard is logically broken for the most common real-world database state. Use a migration tracking mechanism. This must be resolved before writing a single line of implementation code or the migration will silently loop on every startup.

2. **Fix `migration_script.py` typo in Step 1** (Issue 10). Not deferrable. One line. Any catalog reimport destroys all supplier assignments without this fix.

3. **Fix the two Streamlit antipatterns** (Issue 4). The `st.caption` inside the form and the confirmation checkbox inside the form are the same class of bug fixed in the inventory manager defect review. Reintroducing them here is a regression.

4. **Fix the return signature** (Issue 3). Add `lead_time_source` as a column in `plan_df` instead of adding a third return value. Eliminates a breaking change with no loss of functionality.

**Recommended (should be done but would not block):**

5. Remove the denormalized `product_master.supplier` sync from `update_supplier()` (Issue 2). Stop maintaining a column whose only consumer is being migrated away from it.
6. Remove `get_lead_times_map()` and `update_product_supplier()` dead methods (Issue 5).
7. Remove `min_order_qty` from this sprint's scope (Issue 6).
8. Add bulk reassign to the deactivation workflow (Issue 8). Without it, deactivation is nearly unusable.
9. Align `supplier_code` VARCHAR length to 100 in both tables (Issue 7).

**Accept as-is (documented, low risk):**

10. Acknowledge Onboarding Wizard bypass limitation in writing (Issue 11).
11. Single selectbox in Tab 3 rather than two (Issue 9) — cosmetic, not blocking.
12. `PRAGMA foreign_keys = ON` — enable after migration, document the step explicitly (Issue 12).

With the four required changes made, this plan is implementable. Without them, items 1 and 3 introduce runtime failures, item 2 introduces a breaking change, and item 4 means the feature breaks the moment anyone reimports their catalog.
