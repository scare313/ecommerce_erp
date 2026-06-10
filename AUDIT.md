# Production Readiness Audit — Ecommerce ERP

**Audit Date:** 2026-06-09  
**Audited Branch:** `claude_refactored`  
**Auditor:** Principal Engineer Review (Claude Sonnet 4.6)

---

## Executive Summary

**Verdict: NOT APPROVED for production.**

This is a functional single-user internal tool that has been architecturally stretched toward a multi-user ERP. It is not production-ready for the following reasons:

1. **Runtime-fatal bugs** in inventory functions reference database columns that do not exist in the schema. These throw `OperationalError` every time they are called.
2. Three UI data-save operations use `to_sql(..., if_exists='replace')`, which silently **drops and recreates tables**, destroying foreign key constraints and schema columns on every save.
3. **Zero authentication.** Any user with network access reads and writes all financial data.
4. The application is built on **Streamlit**, a single-user analytics tool, not a multi-user application framework. Under concurrent load it produces data races on the SQLite file, cache corruption, and session state collisions.

The financial calculation logic is solid and the onboarding workflow is well-designed. The problems are architectural and deployment-level.

**Estimated remediation before production deployment: 6–10 weeks of engineering.**

---

## Architecture Review

### Separation of Concerns — Partial

The UI → Services → Infrastructure layering is correctly defined on paper but breaks down in the UI layer. `data_manager.py` calls `pd.read_sql()` and `to_sql()` directly against the engine, bypassing all service logic. Bulk edits skip input validation, COGS recalculation, audit logging, and FK safety.

| Issue | Severity | Location |
|---|---|---|
| UI layer directly calls `pd.read_sql/to_sql` against raw engine | High | `data_manager.py:85–96` |
| `get_engine()` creates a new `create_engine()` call on every invocation | Medium | `database.py:9` |
| Services hold engine references in `__init__` with no lifecycle management | Medium | All services |

**`get_engine()` is not a singleton.** Every service instantiation creates an independent connection pool. Under load this exhausts file handles and produces lock contention on the SQLite file.

```python
# database.py — every caller gets a new pool
def get_engine():
    return create_engine(DB_URL)  # No singleton, no pool reuse
```

### Coupling Issues

- `normalize_parsed_category()` in `listing_parsers.py:141` and `map_fuzzy_category()` in `finance_service.py:10` are **identical functions defined twice**. They will diverge.
- `config_rules.py` calls `seed_default_excel_rules()` inside `load_excel_sheet()` — reading a sheet can write a file. This side effect makes the function non-pure and difficult to test.

---

## Security Review

### Authentication and Authorization — CRITICAL

**There is no authentication.** `main.py` has no login gate, no session token, no API key. Any user who reaches the Streamlit port has full unrestricted read/write access to:

- Manufacturing costs and supplier pricing (COGS)
- Complete inventory levels
- Marketplace pricing strategy
- All supplier names and codes

**Fix:** Add `streamlit-authenticator` or Nginx + OAuth2 proxy before any deployment outside a single trusted workstation.

### Secrets Management — High

- No `.env` file support. No `python-dotenv`.
- Database path hardcoded in source: `database.py:6`
- Excel rules path hardcoded in source: `config_rules.py:13`
- No mechanism to configure per-environment or rotate credentials.

### Input Validation — Medium

`st.error(f"Error: {e}")` appears in 12+ locations across `data_manager.py`, leaking internal exception messages, database paths, and schema details to the browser.

**Hardcoded statutory tax rates** (must not be hardcoded):
```python
# finance_service.py:216-217 — Indian statutory rates, change via government notification
tcs_amt = round(taxable_val * 0.005, 2)   # TCS: 0.5%
tds_amt = round(price * 0.001, 2)          # TDS: 0.1%
```
When rates change, profit calculations silently produce wrong numbers without any deployment.

### Database Security — High

- SQLite has no access control.
- `data/db/ecommerce.db` is **committed to the git repository**. If this contains real data, it is permanently in git history.
- `src/logs/*.log` files are also committed to git.

---

## Performance Review

### Database Access — High

No database indexes are defined anywhere in `schema.sql`. Every query on `channel_listings` by marketplace is a full table scan. Every JOIN is O(n×m).

**Required indexes not present:**
```sql
CREATE INDEX idx_listings_marketplace ON channel_listings(marketplace);
CREATE INDEX idx_listings_sku ON channel_listings(internal_sku);
CREATE INDEX idx_pack_master_sku ON pack_master(master_sku);
CREATE INDEX idx_inventory_sku ON inventory_master(sku);
```

`calculate_profitability()` uses `df.apply(get_fees, axis=1)` — a Python-level row-by-row loop on the entire listing table (`finance_service.py:226`). At 10,000 listings this processes 10,000 rows in Python. At 100,000 it will time out.

### Connection Management — High

`finance_service.py` opens a connection manually and closes it manually:

```python
conn = self.engine.connect()   # line 69 — no context manager
# ... 45 lines of code ...
conn.close()                   # line 114 — only reached if no exception
```

Any exception between lines 69 and 114 leaks the connection. Under load this exhausts the pool.

### Caching — Medium

`st.cache_data` is process-level. Under a multi-worker deployment, each process has its own cache. A write on one instance does not invalidate caches on others — users see stale data.

`clear_all_caches()` calls `st.cache_data.clear()`, flushing every cached function for all users simultaneously. A single inventory update forces every user's next render to re-query the database.

---

## Scalability Review — CRITICAL Architectural Mismatch

Streamlit is not a multi-user application framework. Its design assumptions:
- One user, one session
- Single-threaded per session
- No shared mutable state between sessions

For 10,000 users, Streamlit requires horizontal scaling with multiple worker processes. Under that configuration:

- **SQLite cannot handle concurrent writes across processes** (file-level locking)
- `st.cache_data` is not shared across processes (no Redis/Memcached backend)
- `market_rules.xlsx` is a single file — concurrent writes will corrupt it
- Cache invalidation only affects the local process instance

**Migration path required:** PostgreSQL + Redis cache + Streamlit behind gunicorn/multiple workers.

---

## Reliability Review

### CRITICAL BUG 1: Inventory Ledger Completely Broken

`add_stock()` and `transfer_to_shop()` in `inventory_service.py` INSERT into `stock_ledger` using columns that **do not exist** in the schema:

```python
# inventory_service.py:200-204 — WILL THROW OperationalError on every call
conn.execute(text("""INSERT INTO stock_ledger 
    (sku, transaction_type, location, qty_change, unit_type, reason) 
    VALUES (:sku, 'ADJUSTMENT', :loc, :qty, :unit, :reason)"""), ...)
```

**Actual `stock_ledger` schema** (`schema.sql:81–90`):
```sql
CREATE TABLE IF NOT EXISTS stock_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sku VARCHAR(50),
    transaction_type VARCHAR(20),
    packs INT,
    multiplier INT,
    total_pieces_affected INT,
    reason TEXT,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

There is no `location` column. No `qty_change` column. No `unit_type` column.

`transfer_to_shop()` has the same bug on two INSERT statements. The newer `manage_godown_stock()` uses the correct column names, confirming this is a schema-code divergence from a refactor that was not applied to all callsites.

**Every call to `add_stock()` and `transfer_to_shop()` fails with `OperationalError`. The inventory ledger is non-functional.**

### CRITICAL BUG 2: Destructive Save Pattern

Three locations in `data_manager.py` save edits using:

```python
edited_prod.to_sql('product_master', conn, if_exists='replace', index=False)
# Same at lines 148-150 (pack_master) and 215-217 (channel_listings)
```

Pandas `to_sql` with `if_exists='replace'` executes `DROP TABLE` then `CREATE TABLE`. The recreated table:
- Has **no primary key constraint**
- Has **no foreign key constraints**
- Has **no DEFAULT values**
- **Loses ALTER TABLE columns** (`godown_stock_packs`, `shop_stock_pieces` — added via ALTER in schema.sql — will not appear in the recreated table)
- Is not wrapped in a transaction — a crash mid-operation leaves the table missing

### Dead Code with NameError

`migration_script.py` has two `if __name__ == "__main__":` blocks (lines 303 and 443). The second block (lines 311–442) is unreachable but references `excel_path`, a variable local to `run_migration()` that is out of scope. If the code ever reached this block, it would immediately throw `NameError: name 'excel_path' is not defined`.

### Error Suppression in Init

`_check_and_migrate_existing_db()` returns `True` in at least four different exception paths (`init_db.py:92,112`), including cases where migration actually failed. The application signals "success" to `main.py` even when the database is in an unknown state.

### `get_inventory_status()` GROUP BY Bug

```sql
-- inventory_service.py:173-184
FROM product_master p
LEFT JOIN inventory_master i ON p.sku = i.sku
LEFT JOIN pack_master pm ON p.sku = pm.master_sku
GROUP BY p.sku
```

If a product has multiple packs (one-to-many), this multiplies rows and `GROUP BY p.sku` picks an **arbitrary** value for `pm.quantity` (the multiplier). Inventory totals are silently wrong for multi-pack products.

---

## Developer Experience Review

### Onboarding

Adequate for a solo developer on Windows. Not adequate for a team:
- No Docker setup
- No environment variable documentation
- No database reset/seed script for local development
- No Makefile or task runner
- No pre-commit hooks

### Maintainability Issues

- `migration_script.py` contains the full import logic twice. Any migration change must be applied in two places or one copy rots.
- `config_rules.py:load_excel_sheet()` can write a file as a side effect of a read operation.
- `requirements.txt` includes `pytest`, `pytest-cov`, `pytest-mock` — test dependencies deploying to production.
- No version pinning: `streamlit`, `pandas`, `sqlalchemy` all have no version constraints. Breaking changes deploy silently via `pip install`.

---

## Testing Review

### What Is Covered

`FinanceService` and `GapService` have thorough parametrized unit test coverage. `conftest.py` fixtures are well-structured. The seeded engine pattern is correct.

### Zero Coverage Exists For

| Component | Risk Level |
|---|---|
| `OnboardingService` | **Highest** — most complex workflow, highest data mutation risk |
| `BulkService` | **Critical** — deletes and rewrites entire tables |
| `CatalogService` | High — core CRUD called by every write path |
| `InventoryService` | High — the `add_stock`/`transfer_to_shop` runtime bugs would have been caught here |
| All UI pages | Medium |
| `config_rules.py` | Medium — no tests for `save_excel_sheet` race condition |
| `init_db.py` | Medium — no tests for initialization paths |

**No integration tests exist.** The `commit_catalog()` path in `OnboardingService` executes 3 upsert loops in a single transaction — the most critical operation in the system — with zero test coverage.

### Test Infrastructure Gap

`conftest.py` defines the schema inline, not by loading `schema.sql`. If `schema.sql` changes, tests continue passing against the old schema. The `add_stock()` runtime bug exists precisely because no test exercised that code path against the real schema.

---

## Technical Debt (Ranked by Business Risk)

| Rank | Issue | Risk |
|---|---|---|
| 1 | `add_stock()`/`transfer_to_shop()` use nonexistent schema columns | Inventory ledger is completely broken at runtime |
| 2 | `to_sql(if_exists='replace')` in 3 save paths | Any save in Data Manager can corrupt schema and destroy FK constraints |
| 3 | Zero authentication | Complete financial data exposure |
| 4 | `get_engine()` creates new engine per call | Connection pool exhaustion under load |
| 5 | Streamlit on SQLite for multi-user deployment | Architectural mismatch; cannot scale |
| 6 | No database indexes | Query performance collapses beyond ~5,000 rows |
| 7 | TCS/TDS hardcoded as constants | Regulatory rate changes silently produce wrong profit figures |
| 8 | No version pinning | Breaking changes can deploy silently via `pip install` |
| 9 | `migration_script.py` dead code with out-of-scope variable | Maintenance trap, NameError if ever reached |
| 10 | `market_rules.xlsx` write with no concurrency protection | Concurrent saves corrupt the rules file |
| 11 | Test schema diverges from production schema.sql | Tests don't detect schema regressions |
| 12 | Exception details exposed in UI (`st.error(f"Error: {e}")`) | Internal path/schema leakage to users |
| 13 | Manual `conn = engine.connect()` without context manager | Connection leak on any exception |
| 14 | `data/db/ecommerce.db` committed to git | Potential data exposure in git history |
| 15 | `src/logs/*.log` committed to git | Log data in version history |

---

## Production Readiness Scores

| Dimension | Score | Rationale |
|---|---|---|
| **Architecture** | 4/10 | Good layering concept; broken by UI bypassing services; wrong engine for multi-user |
| **Security** | 1/10 | No authentication |
| **Scalability** | 2/10 | SQLite + Streamlit + no indexes = cannot scale beyond one user |
| **Reliability** | 3/10 | Two critical runtime bugs; destructive save pattern; error suppression in init |
| **Maintainability** | 5/10 | Good exception hierarchy and logging; undercut by dead code, schema divergence, zero coverage on write paths |
| **Documentation** | 6/10 | README is thorough; no deployment docs, no runbook |

---

## Top 20 Improvements by ROI

| Rank | Improvement | Severity | File(s) |
|---|---|---|---|
| 1 | Fix `add_stock()` and `transfer_to_shop()` column names to match schema | Critical | `inventory_service.py:200–219` |
| 2 | Replace `to_sql(if_exists='replace')` with proper upsert in Data Manager | Critical | `data_manager.py:89,149,216` |
| 3 | Add authentication (streamlit-authenticator or Nginx OAuth2 proxy) | Critical | `main.py` |
| 4 | Make `get_engine()` return a module-level singleton | High | `database.py` |
| 5 | Add `DB_PATH`, `EXCEL_CONFIG_PATH`, `LOG_LEVEL` as environment variables | High | `database.py`, `config_rules.py` |
| 6 | Add database indexes on `channel_listings` and `pack_master` | High | `schema.sql` |
| 7 | Wrap `finance_service.py` connection in a context manager | High | `finance_service.py:69,114` |
| 8 | Move `pytest*` to `requirements-dev.txt` | High | `requirements.txt` |
| 9 | Pin all dependency versions | High | `requirements.txt` |
| 10 | Move `TCS_RATE` and `TDS_RATE` to `config` table or Excel rules | High | `finance_service.py:216–217` |
| 11 | Write integration tests for `OnboardingService.commit_catalog()` | High | `tests/` |
| 12 | Write tests for `BulkService.upload_full_catalog()` | High | `tests/` |
| 13 | Remove `data/db/ecommerce.db` and `src/logs/*.log` from git, add to `.gitignore` | Medium | `.gitignore` |
| 14 | Load `conftest.py` schema from `schema.sql` instead of inline DDL | Medium | `tests/conftest.py` |
| 15 | Fix `get_inventory_status()` GROUP BY bug for multi-pack products | Medium | `inventory_service.py:173–184` |
| 16 | Replace `st.error(f"Error: {e}")` with generic user-facing messages | Medium | `data_manager.py` (12 locations) |
| 17 | Wrap `bulk_service.upload_full_catalog()` in a single transaction | Medium | `bulk_service.py:161–178` |
| 18 | Remove dead code block in `migration_script.py` (second `if __name__` block) | Low | `migration_script.py:311–443` |
| 19 | Deduplicate `map_fuzzy_category` / `normalize_parsed_category` into one shared util | Low | `finance_service.py:10`, `listing_parsers.py:141` |
| 20 | Add `Makefile` with `make dev`, `make test`, `make lint` targets | Low | root |

---

## Immediate Actions (Before Next Work Session)

These three items have the highest ratio of fix simplicity to production risk:

### 1. Fix inventory ledger (30 minutes)

In `inventory_service.py`, update `add_stock()` and `transfer_to_shop()` to use the correct column names: `packs`, `multiplier`, `total_pieces_affected` — matching what `manage_godown_stock()` already uses correctly.

### 2. Fix Data Manager saves (1–2 hours)

Replace the three `to_sql(if_exists='replace')` calls with `INSERT OR REPLACE` statements via the existing `CatalogService._insert()` method, or at minimum wrap the `to_sql` calls in `engine.begin()` transactions and use `if_exists='append'` after a manual DELETE.

### 3. Add to `.gitignore`

```
data/db/
src/logs/
*.log
venv/
__pycache__/
*.pyc
.env
```

Then remove the committed db and log files: `git rm --cached data/db/ecommerce.db src/logs/*.log`
