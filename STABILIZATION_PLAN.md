# Stabilization Plan — Ecommerce ERP

**Context:** Internal ERP · Single company · 1–10 users · SQLite · Streamlit  
**Goal:** Make the application stable, safe, and trustworthy to use daily  
**Not in scope:** Scalability, PostgreSQL, Redis, enterprise architecture  
**Source:** AUDIT.md + full codebase review (2026-06-10)

---

## Reading This Document

Items within each group are ranked by risk — highest first. Fix them in order.  
Do not skip within a group to work on a later group. Group 1 must be fully resolved before Group 2 becomes the priority.

Total estimated effort: **3–5 days** for a single developer who knows the codebase.

---

## Group 1 — Critical Bugs

These items cause runtime crashes or return demonstrably wrong numbers today. They are broken right now, not theoretically broken under future load.

---

### 1.1 — Inventory Ledger Is Completely Non-Functional

**Rank:** 1 of 4 in this group

**Problem**

`add_stock()` and `transfer_to_shop()` in `inventory_service.py` INSERT into the `stock_ledger` table using column names that do not exist in the schema. The schema was refactored at some point and these two methods were not updated. `manage_godown_stock()` — the newer method in the same file — uses the correct column names, which confirms exactly when the divergence happened.

```
# What the code sends:
INSERT INTO stock_ledger (sku, transaction_type, location, qty_change, unit_type, reason)

# What the schema actually has:
stock_ledger: id, sku, transaction_type, packs, multiplier, total_pieces_affected, reason, timestamp
```

The columns `location`, `qty_change`, and `unit_type` do not exist. SQLite raises `OperationalError: table stock_ledger has no column named location` on every single call. There is no try/except in either method, so the error propagates to the UI. Both methods also fail to commit the inventory update that precedes the ledger insert, meaning stock adjustments are also rolled back.

**Business Impact**

The Inventory Manager page is a visible, named feature of the application. Any user who attempts to add stock, remove stock, or transfer from godown to shop receives an unhandled error. No inventory movement is recorded. The ledger — meant to be the audit trail for all stock changes — has never successfully written a row via these methods. Any inventory data users believe they have entered through these paths does not exist in the database.

**Files Affected**

- `src/core/services/inventory_service.py` — lines 191–219 (`add_stock`, `transfer_to_shop`)
- `src/infrastructure/schema.sql` — lines 81–90 (reference for correct column names)

**Implementation Approach**

Update `add_stock()` and `transfer_to_shop()` to use the column names that `manage_godown_stock()` already uses correctly: `packs`, `multiplier`, `total_pieces_affected`. Map the method parameters to these columns. Do not change the schema — the schema is correct. The code is wrong.

For `add_stock()`, the mapping is:
- `qty` → `packs`
- No existing multiplier parameter — add a `multiplier` parameter with default of 1, or derive `total_pieces_affected = qty * 1`
- Remove `location`, `qty_change`, `unit_type` from the INSERT

For `transfer_to_shop()`, the two ledger INSERT calls need the same treatment. The godown deduction should log as a REMOVE transaction; the shop addition should log as an ADD.

Also wrap both methods in `engine.begin()` so the inventory update and the ledger insert are in the same transaction. Currently, even if the ledger insert were fixed, a crash between the UPDATE and the INSERT would leave stock changed but unlogged.

**Acceptance Criteria**

- Calling `add_stock()` with valid parameters completes without exception
- A row appears in `stock_ledger` with correct values after the call
- Calling `transfer_to_shop()` decreases godown packs and increases shop pieces atomically
- Two rows appear in `stock_ledger` (one REMOVE, one ADD)
- If the ledger insert fails for any reason, the stock update is also rolled back
- The Inventory Manager page can add and transfer stock without showing an error

**Estimated Effort:** 1–2 hours

---

### 1.2 — Finance Service Connection Leaks on Any Exception

**Rank:** 2 of 4 in this group

**Problem**

`calculate_profitability()` in `finance_service.py` opens a database connection manually at line 69 and closes it manually at line 114. There are approximately 45 lines of business logic between open and close. If any exception is raised in that block — a missing column, a malformed rule, a NaN propagation error — the `conn.close()` call is never reached.

```python
conn = self.engine.connect()    # line 69 — connection opened
# ... rule loading, DataFrame joins, apply() calls ...
conn.close()                    # line 114 — only reached on success
```

For SQLite, an unreleased connection holds a read lock on the database file. Once this happens, all subsequent write operations from any other service (adding a product, saving a listing, updating inventory) fail with `database is locked`. The only recovery is restarting the Streamlit process.

For 1–10 users, this means one failed profit calculation can make the entire application read-only until someone restarts it. Users have no way to know why saving suddenly stopped working.

**Business Impact**

A locked SQLite file silently breaks every write operation in the application — not just profit calculation. A user could run a profit calculation that fails due to a missing pricing rule, then find that their next attempt to add a product also fails. The two failures appear unrelated, making diagnosis difficult.

**Files Affected**

- `src/core/services/finance_service.py` — lines 69–114

**Implementation Approach**

Replace the manual `conn = engine.connect()` / `conn.close()` pattern with a `with engine.connect() as conn:` context manager. SQLAlchemy's context manager guarantees the connection is returned to the pool — and the file lock released — whether the block exits normally or via exception.

The `conn.close()` call at line 114 is removed. The `pd.read_sql()` call and all subsequent uses of `conn` sit inside the `with` block. This is a structural change to indentation only — no logic changes.

**Acceptance Criteria**

- A simulated exception inside `calculate_profitability()` (e.g., monkey-patching `pd.read_sql` to raise) does not leave a connection open
- After such a simulated failure, an `add_product()` call in `CatalogService` succeeds immediately without restart
- `SQLite database is locked` does not appear in logs after a failed profit calculation

**Estimated Effort:** 30 minutes

---

### 1.3 — Application Starts Successfully Even When Database Initialisation Failed

**Rank:** 3 of 4 in this group

**Problem**

`_check_and_migrate_existing_db()` in `init_db.py` returns `True` through at least four distinct exception-handling paths, including cases where the migration raised an exception and the database is in an unknown state. `main.py` treats a `True` return as "database is ready" and proceeds to render the full application.

```python
except Exception as migration_error:
    logger.error(f"Migration also failed: {str(migration_error)}", exc_info=True)
    logger.warning("You may still use the Onboarding Wizard.")
    return True   # ← tells main.py everything is fine
```

The result: a user opens the app, it loads without error, they navigate to Profit Dashboard, and get an unrelated crash because a required table is missing or partially populated. The real error — database initialisation failed — happened silently at startup and was discarded.

**Business Impact**

This makes debugging significantly harder. The actual error is in the logs, but users see a confusing crash on a page that has nothing to do with the real problem. A developer chasing that crash will look at the wrong code first.

**Files Affected**

- `src/infrastructure/init_db.py` — `_check_and_migrate_existing_db()`, lines 52–113

**Implementation Approach**

The distinction to preserve is: "database is empty, needs onboarding" is a valid startup state that should return `True`. "Database operation raised an exception" is not a valid startup state and should return `False`.

Currently the code conflates these two cases. The fix is to only return `True` from an exception path when the exception is specifically `DataValidationException` containing "no excel" or "not found" — meaning the Excel file is absent, which is the documented empty-DB case. All other exceptions from migration should return `False`.

`main.py` already handles a `False` return correctly — it shows an error and calls `st.stop()`. That path works. The problem is it is never reached.

**Acceptance Criteria**

- If `run_migration()` raises a `DatabaseException`, `init_database()` returns `False`
- `main.py` displays an error message and stops rather than loading the full UI
- If the Excel file is absent (legitimate empty state), the app still loads and directs the user to the Onboarding Wizard
- No change in behaviour for a healthy database

**Estimated Effort:** 30 minutes

---

### 1.4 — Inventory Status Shows Wrong Numbers for Multi-Pack Products

**Rank:** 4 of 4 in this group

**Problem**

`get_inventory_status()` in `inventory_service.py` joins `product_master`, `inventory_master`, and `pack_master`, then does `GROUP BY p.sku`. Because one product can have multiple packs (e.g., TSHIRT-BLK-PK1, TSHIRT-BLK-PK2), the JOIN produces multiple rows per product. `GROUP BY p.sku` collapses them but picks an arbitrary value for `pm.quantity` — whichever row SQLite happens to return first.

```sql
FROM product_master p
LEFT JOIN inventory_master i ON p.sku = i.sku
LEFT JOIN pack_master pm ON p.sku = pm.master_sku
GROUP BY p.sku
```

The `multiplier` column in the result (used to calculate `total_pieces = godown_packs × multiplier`) is therefore unreliable for any product that has more than one pack configuration. The Inventory Manager page displays these wrong totals to users without any indication that the numbers may be incorrect.

**Business Impact**

Users make purchase planning decisions based on these numbers. An incorrect multiplier means the "total pieces" column is wrong, which feeds into how much stock a user believes they have. Over-ordering or under-ordering stock because of a display bug is a direct business cost.

**Files Affected**

- `src/core/services/inventory_service.py` — `get_inventory_status()`, lines 171–188

**Implementation Approach**

Remove `pack_master` from this query entirely. `get_inventory_status()` should report stock levels, not pack configurations. The `multiplier` for display purposes should come from `inventory_master.pack_multiplier`, which is already stored there for exactly this purpose.

Rewrite the query to join only `product_master` and `inventory_master`. Derive `total_pieces` from `inventory_master.godown_stock_packs * inventory_master.pack_multiplier + inventory_master.shop_stock_pieces`. This removes the ambiguous JOIN and uses the stored multiplier that was already being maintained by `manage_godown_stock()`.

**Acceptance Criteria**

- A product with two packs (quantity 1 and quantity 2) shows correct total pieces based on `inventory_master.pack_multiplier`
- `GROUP BY` on `p.sku` produces one row per product with no arbitrary column selection
- The Inventory Manager page total pieces column matches manually calculated expected values
- No test fixture with a single pack should show any change in behaviour

**Estimated Effort:** 45 minutes

---

## Group 2 — Data Integrity

These items do not crash the application but cause data to be silently lost, silently corrupted, or silently wrong. They are the items most likely to result in a problem that is only discovered long after the damage was done.

---

### 2.1 — Saving Edits in Data Manager Destroys the Database Schema

**Rank:** 1 of 5 in this group

**Problem**

The "Save Changes" button for Products, Packs, and Listings in `data_manager.py` uses `pandas.to_sql()` with `if_exists='replace'`. This parameter does not mean "update existing rows." It means DROP TABLE, then CREATE TABLE, then INSERT all rows. Pandas creates the new table from the DataFrame's dtypes, not from `schema.sql`.

The consequences of one click on "Save Product Changes":

1. `product_master` is dropped — all rows deleted
2. Pandas creates a new `product_master` with no PRIMARY KEY, no FOREIGN KEY references, no DEFAULT values
3. The `godown_stock_packs` and `shop_stock_pieces` columns — which were added to the original table via `ALTER TABLE` in `schema.sql` — are not in the DataFrame unless they happened to be in the `SELECT *` result. If they are NULL for all rows, pandas may omit them
4. The rows are re-inserted without constraints
5. No transaction wraps this — a crash mid-operation leaves the table missing entirely

The same pattern exists for `pack_master` (line 149) and `channel_listings` (line 216).

A user editing one product's cost and clicking save can silently corrupt the database in a way that is not immediately visible but causes downstream failures in FK lookups, inventory joins, and schema-dependent operations.

**Business Impact**

This is the highest data-loss risk in the application. The Data Manager is a routine operational page. Every person using the system will eventually use it. There is no warning before the operation, no confirmation, and no way to recover without a backup (which does not currently exist as a documented procedure). If a user saves edits and the schema columns are lost, inventory data becomes unreliable and the application may behave incorrectly in silent ways for days before anyone notices.

**Files Affected**

- `src/ui/pages/data_manager.py` — lines 87–96 (products), 146–155 (packs), 212–221 (listings)

**Implementation Approach**

Replace `to_sql(if_exists='replace')` with a safe update pattern using SQLAlchemy's `engine.begin()` for transaction safety.

The correct approach for each table differs slightly:

For `product_master` and `pack_master`: Use `INSERT OR REPLACE INTO` (SQLite upsert syntax) row-by-row, or use a DELETE followed by INSERT inside a single `engine.begin()` transaction. The `engine.begin()` context manager guarantees rollback on failure. The table is never dropped. Schema constraints are preserved.

For `channel_listings`: The same approach applies. The composite primary key `(channel_sku, marketplace)` means `INSERT OR REPLACE` handles both inserts and updates correctly.

The simplest safe replacement:
```
with engine.begin() as conn:
    conn.execute(text("DELETE FROM product_master"))
    df.to_sql('product_master', conn, if_exists='append', index=False)
```

This is not the most elegant pattern but it is safe: the DELETE and INSERT are in one transaction, the table is never dropped, and if the INSERT fails the DELETE is rolled back. The table and its schema survive intact.

Do not use `if_exists='replace'` anywhere in the codebase.

**Acceptance Criteria**

- Editing a product's `mfg_cost` and clicking "Save Product Changes" updates only that value; all other products are unchanged
- `product_master` retains its PRIMARY KEY constraint after a save
- `product_master` retains its `godown_stock_packs` and `shop_stock_pieces` columns after a save
- A simulated crash (exception during INSERT) rolls back the DELETE; the table is intact with original data
- Same criteria apply for `pack_master` and `channel_listings`

**Estimated Effort:** 2–3 hours

---

### 2.2 — Bulk Import Leaves Database in a Partially-Updated State on Failure

**Rank:** 2 of 5 in this group

**Problem**

`upload_full_catalog()` in `bulk_service.py` processes six tables in sequence: Config, Product Master, Pack Master, Channel Listings, Pricing Rules, Shipping Rules. Each table operation (DELETE + INSERT) has its own `conn.commit()`. There is no outer transaction wrapping all six operations.

If the import succeeds for products and packs but fails on channel_listings — due to a malformed row, a type mismatch, or a file buffer issue — the result is:
- `product_master` has been replaced with new data ✓
- `pack_master` has been replaced with new data ✓
- `channel_listings` is empty (the DELETE ran) ✗
- Listings are now gone, but the products and packs they reference still exist

The function also reads the same file buffer multiple times. `pd.ExcelFile(file_buffer)` at line 109 opens the buffer, then each call to `pd.read_excel(file_buffer, sheet_name=...)` inside `update_table()` seeks back through the same buffer. For `BytesIO` objects this works because they are seekable, but the function does not verify this, and it is not documented as a requirement.

**Business Impact**

A bulk import is the primary way to make large catalog updates. If an import fails midway, the user has no indication of which tables updated and which did not. The displayed log messages say "✅ Updated product_master" and "❌ Error updating channel_listings" but the application continues running as if everything is fine. The next time profit calculations run, they join against the old channel_listings data while using new product_master data — producing results that are internally inconsistent.

**Files Affected**

- `src/core/services/bulk_service.py` — `upload_full_catalog()`, lines 90–200

**Implementation Approach**

Two changes:

First, wrap all database table operations in a single `engine.begin()` transaction. The Config, Pricing Rules, and Shipping Rules operations write to the Excel file rather than the database, so they cannot be part of the same transaction. Structure the function as: (1) update Excel files, (2) update database tables inside one transaction. If any database table fails, all database table changes roll back together.

Second, read all sheets from the buffer upfront using `pd.ExcelFile` before any writes begin. Store the results in a dictionary of DataFrames. Then process from the in-memory dictionary, not by re-reading the buffer multiple times.

The existing per-table success/failure logging can remain — just ensure the outer `engine.begin()` transaction means the commit only happens if all tables succeed.

**Acceptance Criteria**

- Importing a file where `channel_listings` sheet has a malformed row causes the entire database portion of the import to roll back; `product_master` is unchanged
- After a failed import, the database contains exactly the data it contained before the import began
- The log shown to the user clearly indicates which operation failed and states that no changes were saved
- A successful import commits all six tables as one unit

**Estimated Effort:** 2 hours

---

### 2.3 — TCS and TDS Rates Are Hardcoded; a Regulatory Change Produces Silent Wrong Profit Numbers

**Rank:** 3 of 5 in this group

**Problem**

TCS (Tax Collected at Source) at 0.5% and TDS (Tax Deducted at Source) at 0.1% are written as literal constants inside the `get_fees()` inner function in `finance_service.py`:

```python
tcs_amt = round(taxable_val * 0.005, 2)
tds_amt = round(price * 0.001, 2)
```

These are Indian statutory rates set by government notification. They are not permanent. When the government changes them — which has happened multiple times — every profit calculation in the system produces wrong numbers. There is no error. No warning. The dashboard simply shows incorrect margins for every product on every marketplace. The business makes pricing and procurement decisions on those numbers.

The `config` table and the Excel `market_rules.xlsx` file already exist as the designated home for marketplace configuration. These rates belong there.

**Business Impact**

Incorrect profit margins are a direct business risk. If TCS changes from 0.5% to 1% and the system still calculates at 0.5%, the displayed margin is overstated by 0.5% of revenue on every listing. For a business with ₹10L monthly revenue, that is ₹5,000/month of incorrectly reported margin. Decisions made on that data — which products to promote, which prices to change — are based on wrong inputs.

**Files Affected**

- `src/core/services/finance_service.py` — lines 213–218
- `src/infrastructure/config_rules.py` — `seed_default_excel_rules()`, to add default values
- `data/config/market_rules.xlsx` — Config sheet, to add `tcs_rate` and `tds_rate` columns

**Implementation Approach**

Add `tcs_rate` and `tds_rate` columns to the Config sheet in `market_rules.xlsx` (and to the `seed_default_excel_rules()` defaults so new installs get them). The values are per-marketplace since different marketplaces may have different applicable rates.

In `finance_service.py`, load these values from the config DataFrame that is already being merged into `df` earlier in the function. Replace the hardcoded constants with `row['tcs_rate']` and `row['tds_rate']`, with a `pd.notnull()` guard that falls back to the current hardcoded values if the column is missing (for backward compatibility with existing installs that have not yet updated their Excel config).

This means a rate change requires updating one cell in `market_rules.xlsx` — no code change, no redeployment.

**Acceptance Criteria**

- `tcs_rate` and `tds_rate` columns exist in the Config sheet and in the default seeded rules
- Changing `tcs_rate` to 0.01 in the Excel file changes the TCS calculation in the next profit calculation run (after cache clear)
- If the columns are absent from an old Excel file, the system falls back to the current hardcoded values and logs a warning
- Existing unit tests for TCS/TDS calculation still pass (update expected values if seeded test data changes)

**Estimated Effort:** 2 hours

---

### 2.4 — Test Suite Runs Against a Different Schema Than Production

**Rank:** 4 of 5 in this group

**Problem**

`tests/conftest.py` defines the database schema as hardcoded inline SQL strings in the `in_memory_engine` fixture. This schema is not loaded from `src/infrastructure/schema.sql`. The two schemas have already diverged: `conftest.py` includes `godown_stock_packs` and `shop_stock_pieces` inline in `CREATE TABLE product_master`, while `schema.sql` adds them via `ALTER TABLE` after creation.

The consequence of this divergence is that the runtime crash in `add_stock()` was not caught by tests. The tests ran against a schema that was assumed to be correct but was independently maintained. Any future change to `schema.sql` — adding a column, changing a type, renaming a table — will not be reflected in the test schema unless someone manually updates `conftest.py`. The tests will continue passing while the production application breaks.

**Business Impact**

The test suite's primary value is catching regressions before they reach users. If the test schema diverges from production, the suite cannot catch schema-related bugs — exactly the category of bug that already caused a broken inventory ledger. This is not a theoretical future risk. It is a pattern that already produced a production defect.

**Files Affected**

- `tests/conftest.py` — `in_memory_engine` fixture, lines 33–131
- `src/infrastructure/schema.sql` — the authoritative schema

**Implementation Approach**

Replace the inline `schema_statements` list in `in_memory_engine` with code that reads and executes `src/infrastructure/schema.sql` directly. The existing `_create_new_database()` logic in `init_db.py` already does this — split the schema execution logic into a utility function that both `init_db.py` and `conftest.py` can call.

The one complication: `schema.sql` begins with `DROP TABLE IF EXISTS` statements, which must be harmless on a fresh in-memory database (they are — DROP TABLE IF EXISTS on a nonexistent table is a no-op in SQLite).

After this change, any modification to `schema.sql` is automatically reflected in all test runs.

**Acceptance Criteria**

- `conftest.py` contains no hardcoded `CREATE TABLE` SQL
- The `in_memory_engine` fixture reads schema from `schema.sql`
- Adding a new column to `schema.sql` is immediately visible in test fixtures without any change to `conftest.py`
- All existing tests continue to pass after the change
- A test that calls `add_stock()` against the in-memory engine now fails with the current broken code, confirming the safety net works

**Estimated Effort:** 1 hour

---

### 2.5 — Dead Code in Migration Script Contains an Out-of-Scope Variable Reference

**Rank:** 5 of 5 in this group

**Problem**

`migration_script.py` has two `if __name__ == "__main__":` blocks. The first is at line 303 (correct — calls `run_migration()` and exits). The second is at line 443. Because the first block calls `exit(1)` on failure and falls through on success to the second block... actually no — the first block only calls `exit(1)` inside an exception handler. On success, `run_migration()` returns and Python continues to line 443, entering the second `if __name__ == "__main__":` block.

This second block references `excel_path`, a variable that is local to `run_migration()` and is out of scope at module level. Any execution of this script that reaches line 443 raises `NameError: name 'excel_path' is not defined`.

Additionally, the second block duplicates 130 lines of migration logic that already exists inside `run_migration()`. If someone updates the migration logic inside `run_migration()`, they may not realise the duplicate block exists, and the two implementations will diverge.

**Business Impact**

Running `python migration_script.py` directly — something a developer would do to reinitialise data from an Excel file — crashes with a `NameError` rather than completing the migration. This breaks the documented recovery procedure. The error message (`NameError: name 'excel_path' is not defined`) does not explain what went wrong or how to fix it.

**Files Affected**

- `src/infrastructure/migration_script.py` — lines 311–443

**Implementation Approach**

Delete lines 311–443 entirely. The first `if __name__ == "__main__":` block (lines 303–309) is correct and sufficient. It calls `run_migration()`, handles errors, and exits with an appropriate code.

Before deleting, verify that the second block does not contain any logic not present in `run_migration()`. Based on code review it does not — it is a copy of the same operations with slightly different print statements instead of logger calls.

**Acceptance Criteria**

- `python src/infrastructure/migration_script.py` completes without `NameError`
- The file contains exactly one `if __name__ == "__main__":` block
- Running the script with a valid Excel file in `data/raw_reports/` successfully migrates data
- Running the script without an Excel file exits with a clear error message

**Estimated Effort:** 15 minutes

---

## Group 3 — Security & Operations

These items do not break the application today but create exposure — to data loss, to unauthorised access, or to the application becoming suddenly undeployable. For 1–10 internal users the risk is lower than for a public-facing system, but it is not zero.

---

### 3.1 — No Authentication on a Page That Contains COGS, Supplier Pricing, and Inventory

**Rank:** 1 of 5 in this group

**Problem**

`main.py` has no authentication gate. The Streamlit application starts and renders fully for any connection, with no login required. This means any person on the same network as the machine running the application can access every page, read all financial data, and make edits.

What is exposed without authentication:
- Manufacturing costs per unit (competitive intelligence if seen by a supplier)
- Net margin per SKU per marketplace
- Supplier names, supplier codes, and cost structures
- Complete inventory levels at godown and shop

For a machine running on a Windows desktop in an office, this is accessible to every device on the office WiFi or LAN. For a machine accessible via a fixed IP or port-forwarding, it is accessible to the internet.

**Business Impact**

The data in this application — particularly COGS and margins — is commercially sensitive. If a supplier sees what you pay another supplier, or sees your actual margins, it changes your negotiating position. This is not a hypothetical: the application is running, the data is live, and the port is open.

**Files Affected**

- `main.py` — entry point, before any page rendering
- `requirements.txt` — to add `streamlit-authenticator`

**Implementation Approach**

Add `streamlit-authenticator` (pip package). It provides username/password authentication that integrates directly with Streamlit's session state. Configuration (usernames, hashed passwords) lives in a YAML file that is not committed to git.

The implementation is roughly 15 lines added to the top of `main.py`: load credentials YAML, initialise the authenticator, call `login()`, check `authentication_status`, and call `st.stop()` if not authenticated. All existing page routing code runs only if authentication passes.

Keep it simple. One shared password for all internal users is acceptable for a 1–10 person internal tool. The goal is to prevent casual access from unauthorised network connections, not to implement enterprise IAM.

Store the credentials YAML outside the repository root or add it to `.gitignore` immediately.

**Acceptance Criteria**

- Opening the application URL without logging in shows only a login form
- Entering incorrect credentials rejects the user with an error message
- Entering correct credentials loads the full application as before
- The credentials YAML file is listed in `.gitignore` and is not committed to git
- Existing functionality is unchanged after a successful login

**Estimated Effort:** 2–3 hours

---

### 3.2 — Database File and Log Files Are Committed to the Git Repository

**Rank:** 2 of 5 in this group

**Problem**

`data/db/ecommerce.db` is tracked by git. This binary SQLite file contains all live business data: product costs, supplier information, inventory levels, marketplace listings, and pricing structures. Every `git push` copies this data to wherever the repository is hosted. Every developer who clones the repository gets a copy of the live database.

`src/logs/ecommerce_erp.log`, `src/logs/ecommerce_erp_errors.log`, `src/logs/erp_application.log`, and `src/logs/erp_errors.log` are also tracked. Log files contain operational details including SKU names, pricing data, error messages with file paths, and timestamps of every operation.

Git history is permanent by default. Even if these files are removed from the repository today, they remain in every commit that includes them, accessible via `git log` and `git show`.

**Business Impact**

If the repository is hosted on any service (GitHub, GitLab, Bitbucket — private or public), the business data is accessible to anyone with repository access. This includes past employees who retain access, third-party integrations with repository read permissions, and any future security breach of the hosting service. For a privately-held business, supplier cost data and margin data are among the most sensitive assets.

**Files Affected**

- `.gitignore` — does not currently exclude these paths
- `data/db/ecommerce.db` — tracked binary database
- `src/logs/*.log` — tracked log files

**Implementation Approach**

Two steps, both required:

Step 1 — Stop tracking going forward. Add the following to `.gitignore`:
```
data/db/
src/logs/
*.log
venv/
__pycache__/
*.pyc
.env
*.yaml  # credentials file once auth is added
```

Step 2 — Remove from git index without deleting the files:
```
git rm --cached data/db/ecommerce.db
git rm --cached src/logs/ecommerce_erp.log
git rm --cached src/logs/ecommerce_erp_errors.log
git rm --cached src/logs/erp_application.log
git rm --cached src/logs/erp_errors.log
```

Commit the `.gitignore` changes and the removal of these files in one commit. From that point forward, `git status` will not show the database or log files as modified, and they will not be included in future pushes.

Note: This does not remove the files from git history. If the repository has ever been pushed to a remote service, treat all data in those files as potentially exposed and consider rotating any sensitive values.

**Acceptance Criteria**

- `git status` does not show `data/db/ecommerce.db` or any `.log` file as tracked or modified
- Running `git add .` does not stage the database or log files
- The database and log files still exist on disk and the application works normally
- A new `git clone` of the repository does not include the database file
- `.gitignore` covers `data/db/`, `src/logs/`, `venv/`, `*.pyc`, `.env`

**Estimated Effort:** 30 minutes

---

### 3.3 — No Pinned Dependency Versions; Application Can Break on Reinstall

**Rank:** 3 of 5 in this group

**Problem**

`requirements.txt` lists every dependency without version constraints:

```
streamlit
pandas
sqlalchemy
plotly
openpyxl
xlsxwriter
numpy
matplotlib
pytest
pytest-cov
pytest-mock
xlrd>=2.0.1
```

`xlrd` is the only package with any constraint. Every other package installs at whatever the current latest version is at the time `pip install -r requirements.txt` runs.

This has two specific consequences. First: a major version release of any package — pandas 3.x, SQLAlchemy 3.x, Streamlit 2.x — may introduce breaking API changes. When that happens, the application fails to start on a fresh install, and it is not obvious why. Second: test dependencies (`pytest`, `pytest-cov`, `pytest-mock`) are in the same file as production dependencies. These packages install into production.

**Business Impact**

When the person running this application moves to a new machine, reinstalls after a failure, or sets up a second machine for another user, there is no guarantee the application installs and runs. This is a practical operational problem, not a theoretical one — package releases that break compatibility happen regularly. An application that cannot be reliably reinstalled is a liability.

**Files Affected**

- `requirements.txt`

**Implementation Approach**

Two changes:

First, pin all packages to their currently installed versions. Run `pip freeze > requirements_lock.txt` on the working machine to capture exact versions. Use this as the reference for setting pins in `requirements.txt`. Pin to minor versions (e.g., `pandas>=2.0,<3.0`) rather than exact patch versions (e.g., `pandas==2.0.3`) — minor version pins prevent breaking changes while allowing security patches.

Second, create `requirements-dev.txt` containing only the test dependencies:
```
pytest>=7.0
pytest-cov
pytest-mock
```

Remove `pytest`, `pytest-cov`, and `pytest-mock` from `requirements.txt`. Production installs run `pip install -r requirements.txt`. Developer setups additionally run `pip install -r requirements-dev.txt`.

**Acceptance Criteria**

- Every package in `requirements.txt` has a version constraint
- `pytest`, `pytest-cov`, `pytest-mock` are not in `requirements.txt`
- `requirements-dev.txt` exists and contains the test dependencies
- Running `pip install -r requirements.txt` on a clean environment installs a version combination that starts the application
- Running `pip install -r requirements.txt -r requirements-dev.txt` installs a version combination that passes all tests
- README documents the two-file install pattern

**Estimated Effort:** 1 hour

---

### 3.4 — Exception Details Are Exposed Directly in the UI

**Rank:** 4 of 5 in this group

**Problem**

`st.error(f"Error: {e}")` appears in at least 12 locations across `data_manager.py` and other pages. The exception object `e` is the raw Python exception, which for SQLAlchemy errors contains the full database error including table names, column names, file paths, and in some cases partial SQL queries.

Example of what a user sees when an operation fails:
```
Error: (sqlite3.OperationalError) no such column: location
[SQL: INSERT INTO stock_ledger (sku, transaction_type, location, qty_change, unit_type, reason) 
VALUES (?, 'ADJUSTMENT', ?, ?, ?, ?)]
```

This exposes the database schema, column names, and internal SQL to whoever is looking at the screen. For a strictly internal application with trusted users, this is low risk. However, if a supplier, customer, or external party is ever shown the screen during a demonstration or support session, this data is visible.

**Business Impact**

The direct risk is low for a fully internal tool. The practical issue is that raw exception messages are unhelpful to non-technical users. A user who sees `OperationalError: table stock_ledger has no column named location` does not know what action to take. A message like "Stock update failed. Please contact your administrator." is more useful and does not expose internals.

**Files Affected**

- `src/ui/pages/data_manager.py` — 12+ occurrences
- `src/ui/pages/inventory_manager.py` — several occurrences
- `src/ui/pages/profit_dashboard.py` — several occurrences
- `main.py` — line 101

**Implementation Approach**

Create a small helper function — `show_error(user_message, exception)` — in a shared UI utilities module or at the top of each page file. It calls `logger.error(str(exception), exc_info=True)` to write the full details to the log, then calls `st.error(user_message)` to show only the safe message to the user.

Replace every `st.error(f"Error: {e}")` with `show_error("Operation failed. Check logs for details.", e)` or a more specific user-facing message where context allows.

This change does not affect the log files — full exception details continue to be recorded there for debugging.

**Acceptance Criteria**

- No raw exception message containing SQL, column names, or file paths is displayed in the Streamlit UI
- The full exception is still written to the log file on every error
- User-facing error messages are in plain language
- `main.py` line 101 is updated — this is the catch-all page error handler and currently shows `str(e)` directly

**Estimated Effort:** 1–2 hours

---

### 3.5 — `get_engine()` Creates a New SQLAlchemy Engine on Every Call

**Rank:** 5 of 5 in this group

**Problem**

`database.py` defines `get_engine()` as a function that calls `create_engine(DB_URL)` and returns a new engine instance every time it is called. `create_engine()` creates a new connection pool. Every service (`FinanceService`, `CatalogService`, `GapService`, `BulkService`, `InventoryService`) calls `get_engine()` in its `__init__`. Each page render that instantiates these services creates fresh connection pools that are never explicitly disposed.

For SQLite specifically, this is not a performance concern at 1–10 users — SQLite handles it. The operational concern is recovery after the connection leak described in item 1.2. If `finance_service.py` leaks a connection before item 1.2 is fixed, having multiple pools means multiple pool objects are all trying to manage connections to the same locked file. When the lock eventually releases, the pools do not cleanly re-synchronise.

After item 1.2 is fixed, this item is lower risk. It is included here because the fix is small and closes a category of potential issues permanently.

**Business Impact**

Low for daily use. Relevant only during error recovery. The fix also makes the application's database access pattern easier to reason about and trace in logs.

**Files Affected**

- `src/infrastructure/database.py`

**Implementation Approach**

Convert `get_engine()` from a factory function to a module-level singleton. Create the engine once at module import time. All calls to `get_engine()` return the same instance.

```python
# Before: creates new engine each call
def get_engine():
    return create_engine(DB_URL)

# After: creates engine once, returns same instance
_engine = None

def get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(DB_URL)
    return _engine
```

For SQLite, add `check_same_thread=False` to the engine creation arguments, which is required when the same engine instance is used across Streamlit's threading model.

**Acceptance Criteria**

- Two calls to `get_engine()` return the same object (`id(get_engine()) == id(get_engine())`)
- All existing tests pass without modification
- The application starts and all pages function correctly
- `check_same_thread=False` is set in the SQLite connect args

**Estimated Effort:** 30 minutes

---

## Summary

| # | Item | Group | Effort | Risk if Deferred |
|---|---|---|---|---|
| 1.1 | Inventory ledger schema mismatch | Critical Bug | 1–2 hrs | Inventory module permanently broken |
| 1.2 | Finance service connection leak | Critical Bug | 30 min | Any calc error locks entire DB until restart |
| 1.3 | init_db silently swallows failures | Critical Bug | 30 min | App starts in broken state, wrong page crashes |
| 1.4 | Inventory GROUP BY wrong multiplier | Critical Bug | 45 min | Wrong inventory numbers, wrong purchase decisions |
| 2.1 | to_sql replace destroys table schema | Data Integrity | 2–3 hrs | Any Data Manager save can corrupt the database |
| 2.2 | Bulk import not atomic | Data Integrity | 2 hrs | Failed import leaves DB in inconsistent state |
| 2.3 | TCS/TDS hardcoded rates | Data Integrity | 2 hrs | Regulatory change → silent wrong margin numbers |
| 2.4 | Test schema diverges from production | Data Integrity | 1 hr | Future schema bugs won't be caught by tests |
| 2.5 | Dead code with NameError in migration | Data Integrity | 15 min | Direct script execution crashes with NameError |
| 3.1 | No authentication | Security | 2–3 hrs | All financial data readable by anyone on network |
| 3.2 | DB and logs in git | Security | 30 min | Business data in version history |
| 3.3 | No dependency version pinning | Operations | 1 hr | Reinstall may break the application silently |
| 3.4 | Exception details in UI | Security | 1–2 hrs | Schema/paths exposed on any error |
| 3.5 | get_engine() not singleton | Operations | 30 min | Multiple pools complicate error recovery |

**Total estimated effort: 3–5 days.**

Fix Group 1 items first. They require the fewest changes and produce the most immediate improvement in reliability. Group 2 item 2.1 (the destructive save) should be treated as equally urgent to Group 1 — it has not caused a visible crash yet only because not everyone has used the save button on a Data Manager grid.
