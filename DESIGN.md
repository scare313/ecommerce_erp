# Technical Design Documents

Design specs for the committed epics in [BACKLOG.md](BACKLOG.md) (E1–E11).
**Status: DESIGN — not approved for implementation.** No production code until each design is signed off.

## Conventions
- **"API Changes"** = service-layer interface changes. This system has **no HTTP API** — the UI (Streamlit) calls in-process services directly. Where an epic touches only infra (Caddy/systemd) this is "N/A".
- **Sequence diagrams** are text (`A -> B: message`).
- **Stack context:** Streamlit app (`main.py`) → services (`src/core/services/*`) → SQLAlchemy over **SQLite** (`src/infrastructure/database.py`). Served by **Caddy** (HTTPS) → `erp` **systemd** service on an Oracle VPS. Deploy = `git pull` + `systemctl restart erp`. Production data = a single SQLite file.
- **Icebox (E90–E97):** design deferred until each epic's stated trigger fires (see end of doc).

---

# E1 — Authentication gate on the public app

**1. Problem Statement**
The app is internet-public with zero authentication; anyone with the URL has full read/write/delete access to business and financial data. This is a live, critical exposure.

**2. Current Architecture**
Caddy terminates HTTPS and reverse-proxies all traffic to Streamlit on `127.0.0.1:8501` with no access control. The app has no concept of a user or session identity.

**3. Proposed Architecture**
Two tiers, adopt tier 1 now, tier 2 optional:
- **Tier 1 (edge):** Caddy `basic_auth` on the site block — a bcrypt-hashed credential gates every request before it reaches Streamlit. Health path (E5) allow-listed to bypass.
- **Tier 2 (app, optional):** a session-based login gate in `main.py` validating against `st.secrets`, enabling per-user identity for later light roles.

**4. Alternative Designs Considered**
- **OAuth/SSO (Google) via an auth proxy (oauth2-proxy):** robust, but heavy for 1–10 internal users.
- **Streamlit-native auth component/library:** ties auth to app runtime; weaker than an edge gate; still worth it for identity (tier 2).
- **IP allow-list only:** breaks for mobile/remote staff on changing IPs.
- **VPN/Tailscale-only access:** we explicitly moved away from this earlier for public convenience.

**5. Tradeoffs**
Edge basic_auth = minimal effort, instant protection, but shared credential + no per-user audit. App login = more effort, gives identity, but must run before any page logic. Chosen: edge now, app login as follow-on when identity is actually needed.

**6. Data Flow**
Request → Caddy (verify credential) → [401 if absent/invalid] → Streamlit → (optional) session check → page.

**7. Sequence Diagram**
```
User -> Caddy: GET / (no creds)
Caddy -> User: 401 WWW-Authenticate
User -> Caddy: GET / (Basic creds)
Caddy -> Streamlit: proxy request
Streamlit -> User: app (or login form if tier 2)
Monitor -> Caddy: GET /_stcore/health (allow-listed) -> 200
```

**8. Database Changes** — None (tier 1). Tier 2: none (credentials in `st.secrets`).

**9. API Changes** — None (tier 1). Tier 2: a small `auth` helper module interface.

**10. Frontend Changes** — None (tier 1). Tier 2: a login form + logout control in the app shell.

**11. Migration Strategy** — Config-only. Add `basic_auth` to `/etc/caddy/Caddyfile`; `caddy validate` then reload. No data migration.

**12. Backward Compatibility** — Existing bookmarks work but now prompt for credentials. Camera/HTTPS flow unaffected. The uptime monitor requires the health-path bypass (coordinate with E5).

**13. Testing Strategy** — `curl -I` expects 401; `curl -u` expects 200; browser + Android smoke incl. camera scan; confirm health path returns 200 without creds.

**14. Deployment Strategy** — Edit Caddyfile → `caddy validate` → `systemctl reload caddy` (zero app downtime). Keep an SSH session open during change.

**15. Rollback Strategy** — Revert Caddyfile change → `systemctl reload caddy`. Instant.

**16. Risk Assessment** — *Lockout* (mistyped hash): mitigated by `validate` + open SSH. *Health-path exposure*: keep it non-sensitive. *Shared-credential blast radius*: acceptable interim; tier 2 addresses. Overall risk: **medium to implement, extreme if not done.**

---

# E2 — Automated off-box database backup

**1. Problem Statement**
The entire business lives in one SQLite file with no automated backup; a disk/VM loss is unrecoverable.

**2. Current Architecture**
SQLite file on the VPS; only a manual copy has ever been made.

**3. Proposed Architecture**
A backup script using SQLite's **online backup** (`.backup`/`VACUUM INTO`) writes a timestamped snapshot, copies it off-VM, and prunes by retention. A **systemd timer** runs it nightly. A documented restore drill validates recoverability.

**4. Alternative Designs Considered**
- **Raw `cp` of the live file:** risks copying mid-write (corruption). Rejected.
- **Filesystem/VM snapshots:** depends on provider tooling; coarser; less portable.
- **Litestream (continuous replication):** excellent RPO but added daemon/complexity beyond current need.

**5. Tradeoffs** Online backup + nightly timer = simple, consistent, ~24h RPO. Continuous replication would cut RPO further at higher operational cost — deferred.

**6. Data Flow** Timer → script → SQLite online-backup → snapshot file → off-VM copy → prune old snapshots.

**7. Sequence Diagram**
```
systemd.timer -> backup.sh: trigger (nightly)
backup.sh -> sqlite: .backup db -> snapshot_<ts>.db
backup.sh -> offsite: upload snapshot
backup.sh -> local: prune snapshots older than N days
backup.sh -> log: record result / exit code
```

**8. Database Changes** — None (read-only snapshot).

**9. API Changes** — None.

**10. Frontend Changes** — None.

**11. Migration Strategy** — None.

**12. Backward Compatibility** — Fully transparent; no app impact.

**13. Testing Strategy** — Run script manually; open snapshot (`sqlite3 .tables`); perform a **restore drill** into a scratch path and boot the app against it (product count matches).

**14. Deployment Strategy** — Install script + systemd service/timer; `systemctl enable --now`; verify `list-timers`.

**15. Rollback Strategy** — `systemctl disable --now` the timer; remove script. No data impact.

**16. Risk Assessment** — *Backup inconsistency*: mitigated by online-backup API. *Offsite creds leakage*: store securely, not in repo. *Silent failure*: script exits non-zero + logs; consider surfacing failures via E5 monitor later. Risk: **low.**

---

# E3 — Self-host the barcode-scanner library

**1. Problem Statement**
The scanner loads `html5-qrcode` from the unpkg CDN — an external runtime dependency for the core floor action (reliability + privacy risk).

**2. Current Architecture**
`src/ui/components/barcode_scanner.py` embeds camera HTML in a srcdoc iframe that `<script src="https://unpkg.com/html5-qrcode@2.3.8/...">`.

**3. Proposed Architecture**
Vendor the pinned minified build into the repo and reference it from the app origin (via Streamlit static serving, or inline the JS into the srcdoc as a fallback). No external request at runtime.

**4. Alternative Designs Considered**
- **Keep CDN with SRI hash:** adds integrity but still needs network + third party. Rejected.
- **npm/bundler pipeline:** overkill; no JS build system here.
- **Inline the JS in the HTML string:** guaranteed to load inside the iframe; larger Python string. Viable fallback.

**5. Tradeoffs** Static-served file = clean separation but must resolve inside the srcdoc iframe (path nuance). Inlining = bulletproof loading, uglier source. Choose static serving; fall back to inline if iframe path fails.

**6. Data Flow** Page render → iframe loads scanner JS from app origin → camera → scan → bridge input → Streamlit rerun (unchanged downstream).

**7. Sequence Diagram**
```
Browser -> App: load Inventory page
App -> Browser: srcdoc iframe (script src=/static/html5-qrcode.min.js)
Browser -> App: GET /static/html5-qrcode.min.js (same origin)
Browser: camera scan -> bridge input -> Enter -> Streamlit rerun
```

**8. Database Changes** — None.

**9. API Changes** — None.

**10. Frontend Changes** — `barcode_scanner.py`: replace CDN `<script src>` with local path (or inline). Add vendored asset under `src/ui/static/` (+ NOTICE/version).

**11. Migration Strategy** — None (asset swap).

**12. Backward Compatibility** — Identical scanner UX; camera flow unchanged.

**13. Testing Strategy** — Android HTTPS scan smoke test; DevTools Network shows **no unpkg request**; verify auto-fill + rerun still work.

**14. Deployment Strategy** — Standard `git pull` + restart.

**15. Rollback Strategy** — Revert `barcode_scanner.py` to the CDN `<script src>`.

**16. Risk Assessment** — *Static path not resolving in iframe*: mitigated by inline fallback. *Version drift*: pinned + documented. Risk: **low.**

---

# E4 — Make destructive actions safe

**1. Problem Statement**
The UI executes raw destructive SQL (e.g., `DELETE FROM channel_listings`) directly, bypassing the service layer and any confirmation — accidental irreversible data loss is possible.

**2. Current Architecture**
`src/ui/pages/data_manager.py` imports `get_engine` and runs `text("DELETE …")` inline; no confirmation step.

**3. Proposed Architecture**
Add a guarded service method (parameterized, transactional, logged, returns affected count). The page calls it behind an explicit two-step confirmation and shows a humane result (pairs with E6).

**4. Alternative Designs Considered**
- **Soft delete (is_deleted flag):** reversible but a schema + query-wide change; larger scope than warranted now.
- **Keep inline SQL, add only a confirm:** leaves the layer violation and duplication. Rejected.

**5. Tradeoffs** Service method + confirm = correct boundary, safe, small effort. Soft delete would add recoverability at broader cost — deferred unless accidental deletes recur.

**6. Data Flow** UI confirm → service method → transactional scoped DELETE → count → UI success/error.

**7. Sequence Diagram**
```
User -> Page: click Delete (marketplace X)
Page -> User: require confirm (checkbox/second click)
User -> Page: confirm
Page -> Service: delete_listings_for_marketplace(X)
Service -> DB: BEGIN; DELETE ... WHERE marketplace=:X; COMMIT
Service -> Page: rows_deleted
Page -> User: "Deleted N listings for X"
```

**8. Database Changes** — None (behavioral; same tables).

**9. API Changes** — New service method, e.g. `CatalogService.delete_listings_for_marketplace(marketplace) -> int`.

**10. Frontend Changes** — `data_manager.py`: remove inline SQL/`get_engine`; add confirm UI + result messaging.

**11. Migration Strategy** — None.

**12. Backward Compatibility** — Same end result; adds a confirmation step (intended behavior change).

**13. Testing Strategy** — `pytest` on temp SQLite: asserts only scoped rows deleted + correct count; manual: delete blocked without confirm, works with confirm; grep confirms no raw delete in UI.

**14. Deployment Strategy** — Standard deploy.

**15. Rollback Strategy** — Revert page + service changes.

**16. Risk Assessment** — *Scope predicate mismatch* (deletes too much/little): mitigate by matching the exact previous WHERE clause + unit test. Risk: **medium.**

---

# E5 — Test-before-deploy + uptime check

**1. Problem Statement**
Deploys can ship regressions (tests never auto-run), and there's no signal when the app goes down.

**2. Current Architecture**
Manual `git pull` + `systemctl restart erp`; file logs only; no monitoring.

**3. Proposed Architecture**
A `deploy.sh` that pulls → runs `pytest` → restarts only on green. Expose/allow-list a health path; register it with an external uptime monitor with email alerting.

**4. Alternative Designs Considered**
- **Full CI (GitHub Actions) + container image:** proper but heavy for a solo, systemd deploy. Deferred (icebox).
- **Self-hosted monitoring (Prometheus/Grafana/Sentry):** over-scale for 10 users. Rejected now.

**5. Tradeoffs** Script + external monitor = near-zero ops, catches the two real failure modes. CI/observability stack would add rigor at disproportionate cost — deferred.

**6. Data Flow** Deploy: pull → test → (pass?) restart : abort. Runtime: monitor → health path → alert on failure.

**7. Sequence Diagram**
```
Operator -> deploy.sh: run
deploy.sh -> git: pull
deploy.sh -> pytest: run suite
pytest -> deploy.sh: pass/fail
deploy.sh -> systemd: restart erp   (only if pass)
--- runtime ---
Monitor -> Caddy: GET /health (every ~5m)
Caddy -> Monitor: 200 / timeout
Monitor -> Operator: alert on failure
```

**8. Database Changes** — None.

**9. API Changes** — A health path (Streamlit's `/_stcore/health` or documented equivalent).

**10. Frontend Changes** — None.

**11. Migration Strategy** — None.

**12. Backward Compatibility** — Deploy process changes; health path must bypass E1 auth (allow-list).

**13. Testing Strategy** — Force a failing test → confirm no restart; revert → confirm deploy; trigger a test uptime alert.

**14. Deployment Strategy** — Adopt `deploy.sh` as the deploy method; register monitor.

**15. Rollback Strategy** — Fall back to manual pull + restart; disable monitor.

**16. Risk Assessment** — *Auth blocks monitor*: coordinate allow-list with E1. *Flaky tests block deploy*: keep suite fast/deterministic. Risk: **low.**

---

# E6 — Humane error messages

**1. Problem Statement**
Raw exception/stack text is rendered to users (`st.error(f"...{e}")`) — alarming and a minor info-disclosure.

**2. Current Architecture**
Pages and `main.py` render exception strings directly; the custom logger exists but user-facing detail leaks.

**3. Proposed Architecture**
A UI error helper logs full detail server-side (existing logger) and renders a generic, actionable message. Adopt across pages + the top-level handler.

**4. Alternative Designs Considered**
- **Global exception middleware:** Streamlit lacks a true middleware layer; a helper + top-level try/except is the idiomatic equivalent.
- **Error codes/catalog:** nice for support but over-scale now.

**5. Tradeoffs** Helper = simple, consistent, keeps full logs. Central middleware would be cleaner but isn't available in the framework.

**6. Data Flow** Exception → helper → logger (full detail) + `st.error` (friendly message).

**7. Sequence Diagram**
```
Page code -> try: operation
operation -> raises Exception
Page -> error_helper(e, context)
error_helper -> logger: full traceback + context
error_helper -> UI: "Something went wrong doing X. Try Y."
```

**8. Database Changes** — None.

**9. API Changes** — New helper, e.g. `show_error(exc, user_message)`.

**10. Frontend Changes** — Replace raw `st.error(f"...{e}")` across `src/ui/pages/*.py` and `main.py`.

**11. Migration Strategy** — None.

**12. Backward Compatibility** — Users see friendlier messages; log detail unchanged.

**13. Testing Strategy** — `pytest` that the log path captures full detail; manual: force errors per page, confirm no stack shown.

**14. Deployment Strategy** — Standard deploy; roll out high-traffic pages first (E6-F1-T2-S1) then the rest.

**15. Rollback Strategy** — Revert helper adoption per page.

**16. Risk Assessment** — *Losing detail in logs*: verify logger still receives traceback. Risk: **low.**

---

# E7 — Decouple cache/core from Streamlit

**1. Problem Statement**
`src/core/cache.py` imports `streamlit` and uses `@st.cache_data`, so the "core" layer can't run headless (no cron jobs, hard to unit-test).

**2. Current Architecture**
UI → `core/cache.py` (streamlit-coupled) → `get_engine`. Caching policy fused into core; some data-source fallback logic (Excel/SQL config) lives here too.

**3. Proposed Architecture**
Split into framework-free core data functions (`src/core/data_access.py`) and a UI-side cache adapter (`src/ui/cache_adapter.py`) that applies `@st.cache_data` with the same TTLs and `clear_*` semantics. Core imports no UI framework.

**4. Alternative Designs Considered**
- **Framework-agnostic cache abstraction (functools + pluggable backend):** more flexible, more code; unnecessary now.
- **Leave as-is:** blocks scheduled jobs + tests. Rejected (this is the one coupling with concrete downside).

**5. Tradeoffs** Split = clean headless core + preserved caching, at the cost of some import re-pointing across pages. Abstraction layer deferred.

**6. Data Flow** UI page → UI cache adapter (`@st.cache_data`) → core data function → engine. Cron/tests → core data function directly (no streamlit).

**7. Sequence Diagram**
```
Page -> cache_adapter.get_x(): cached call
cache_adapter -> core.data_access.get_x(): on miss/TTL
core.data_access -> DB: query
core.data_access -> cache_adapter: DataFrame
cache_adapter -> Page: DataFrame (memoized)
--- headless ---
Job -> core.data_access.get_x(): direct (uncached)
```

**8. Database Changes** — None.

**9. API Changes** — Module reorganization: functions move core→data_access; new UI adapter wrappers; page imports updated. Signatures preserved.

**10. Frontend Changes** — Update imports in `src/ui/pages/*.py` to the adapter.

**11. Migration Strategy** — Code-only; do function-by-function to limit blast radius; keep old names as thin shims during transition if helpful.

**12. Backward Compatibility** — Identical data + TTL behavior; `clear_all_caches`/`clear_inventory_cache`/etc. must behave the same. **Preserve the Excel/SQL config fallback logic exactly.**

**13. Testing Strategy** — Import core module in a no-streamlit context (`pytest`); value-parity checks vs current; manual "Refresh Data" + TTL behavior.

**14. Deployment Strategy** — Standard deploy; verify caches warm and invalidation works post-restart.

**15. Rollback Strategy** — Revert to the streamlit-coupled `cache.py` (kept until adapter proven).

**16. Risk Assessment** — *Broken cache invalidation* or *lost config fallback*: mitigated by parity tests + incremental move. Risk: **medium.**

---

# E8 — Navigation grouping + rename

**1. Problem Statement**
Flat 8-item sidebar mixes personas and includes the vague "Data Manager" name — poor discoverability.

**2. Current Architecture**
`main.py` renders `st.sidebar.radio` over 8 flat labels; routes by string match.

**3. Proposed Architecture**
Group modules into workflow sets (Operate / Sell / Buy / Analyze) via grouped controls/captions; rename "Data Manager" → "Catalog". Routing logic unchanged underneath.

**4. Alternative Designs Considered**
- **`st.navigation`/multipage app:** cleaner long-term but a larger restructure of the entry point. Deferred.
- **Icons-only nav:** discoverability risk. Rejected.

**5. Tradeoffs** In-place grouping = low effort, immediate clarity, keeps single-file routing. Full multipage migration deferred to a future nav overhaul.

**6. Data Flow** Unchanged — selection → same page dispatch.

**7. Sequence Diagram**
```
User -> Sidebar: pick module (grouped)
Sidebar -> main.py: selected label
main.py -> page module: render()
```

**8. Database Changes** — None.

**9. API Changes** — None.

**10. Frontend Changes** — `main.py` nav construction; page title text in `data_manager.py` (rename).

**11. Migration Strategy** — None.

**12. Backward Compatibility** — Routes unchanged; labels change (one-time relearn). Any deep links relying on the old label text update.

**13. Testing Strategy** — Manual: reach every module via new nav; confirm no dead entries.

**14. Deployment Strategy** — Standard deploy.

**15. Rollback Strategy** — Revert `main.py`.

**16. Risk Assessment** — Risk: **low.**

---

# E9 — Protect the margin engine (single config source + freshness)

**1. Problem Statement**
Marketplace/fee config exists in **both Excel and SQL** with scattered fallback logic — risk of silent divergence feeding wrong margins (the product's moat).

**2. Current Architecture**
`config_rules.py` (Excel), SQL `config` table, `cache.py` `get_marketplaces` (Excel/SQL fallback), `init_db.py` seeds Excel. Two sources of truth.

**3. Proposed Architecture**
Choose one authoritative source (recommended: **SQL of record, Excel as import-only**). Refactor all readers to the single source; remove fallback branches. Add a **"rules last verified" date**, surfaced in the UI with a staleness warning.

**4. Alternative Designs Considered**
- **Excel as source of record:** matches current ops habit but weaker for concurrent/consistent reads.
- **External config service:** over-scale.
- **Keep duality, add sync check:** treats the symptom, not the cause. Rejected.

**5. Tradeoffs** SQL-of-record = consistent, queryable, single path; requires an import step for Excel edits. Preserving Excel-of-record would avoid workflow change but keep the fragility. Choose SQL-of-record.

**6. Data Flow** Excel (optional import) → SQL config (authoritative) → readers → margin calc + freshness display.

**7. Sequence Diagram**
```
Admin -> Import: upload Excel rules (optional)
Import -> SQL config: upsert rules + last_verified
Page/Service -> SQL config: read rules (single path)
Profit page -> UI: show margins + "rules verified <date>" (warn if stale)
```

**8. Database Changes** — Possibly add `last_verified` (and ensure rules columns) to the `config`/rules table. Idempotent add.

**9. API Changes** — Config read functions consolidated to one source; a getter/setter for `last_verified`.

**10. Frontend Changes** — Display freshness + staleness warning on `data_manager.py` (config tab) and/or `profit_dashboard.py`; import-only path for Excel.

**11. Migration Strategy** — **Back up DB first.** Seed authoritative SQL from current effective config; add `last_verified`; verify parity; then remove fallback code.

**12. Backward Compatibility** — Existing config values preserved exactly; profit numbers must match pre/post. Excel edits now require an explicit import.

**13. Testing Strategy** — `pytest` on config read; **manual parity check of Profit dashboard figures before/after** (critical); staleness warning appears for an old date.

**14. Deployment Strategy** — Backup → deploy → run consolidation/migration → verify parity.

**15. Rollback Strategy** — Restore DB snapshot + revert code to the dual-source readers.

**16. Risk Assessment** — *Value divergence / wrong margins post-migration*: highest-care item — snapshot, parity-test, verify before removing fallback. Risk: **medium (high impact).**

---

# E10 — Mobile navigation for the floor persona

**1. Problem Statement**
On phones, users start behind an empty hamburger; core inventory actions aren't immediately thumb-reachable.

**2. Current Architecture**
`inventory_manager.py` renders an action-card launcher; `mobile_style.py` holds mobile CSS (media-query scoped). Top-level nav is the sidebar (collapsed on mobile).

**3. Proposed Architecture**
Ensure the floor persona lands on the Inventory launcher on mobile; add a **mobile-only sticky bottom navigation** for core actions (Update/Transfer/Scan/Balances), scoped via the existing media query and Streamlit container-key classes.

**4. Alternative Designs Considered**
- **FAB (floating action button) only:** good for one action, weaker for 3–4.
- **Full multipage + per-persona shells:** larger; deferred to role-aware delivery (icebox E97).

**5. Tradeoffs** Bottom nav = app-like, thumb-first, builds on existing launcher; relies on Streamlit internal class names (fragile across upgrades). Documented selector mitigates.

**6. Data Flow** Unchanged section routing; bottom-nav taps set the active inventory section.

**7. Sequence Diagram**
```
Phone user -> Inventory: land on launcher (mobile)
User -> BottomNav: tap "Update"
BottomNav -> inventory_manager: set section=update -> rerun
inventory_manager -> UI: render Update section
```

**8. Database Changes** — None.

**9. API Changes** — None.

**10. Frontend Changes** — `inventory_manager.py` (section entry on mobile), `mobile_style.py` (bottom-nav CSS, ≤640px only).

**11. Migration Strategy** — None.

**12. Backward Compatibility** — Desktop unaffected (bottom nav hidden >640px).

**13. Testing Strategy** — Android portrait: one-tap to core actions, bottom nav visible; desktop: bottom nav hidden, nav unchanged.

**14. Deployment Strategy** — Standard deploy; phone verification is the gate.

**15. Rollback Strategy** — Revert CSS + inventory changes.

**16. Risk Assessment** — *Streamlit class-name fragility*: document selectors; functionality (buttons) survives even if styling regresses. Risk: **low–medium.**

---

# E11 — SKU case-normalization

**1. Problem Statement**
18 `UPPER(sku)` filters signal inconsistent SKU casing; queries are needlessly complex and can't use indexes later.

**2. Current Architecture**
Services store SKUs as entered and compare via `UPPER()`; parsers/bulk import may introduce mixed case.

**3. Proposed Architecture**
Canonicalize SKUs to uppercase **on write** (manual, transfer, bulk, parsers); one-time idempotent normalization of existing rows; then remove `UPPER()` predicates, uppercasing inbound search terms at query entry instead.

**4. Alternative Designs Considered**
- **SQLite `COLLATE NOCASE` on SKU columns:** case-insensitive without data changes, but ties behavior to column collation and complicates joins/uniqueness; less explicit.
- **Expression index on `UPPER(sku)`:** keeps `UPPER()`, adds index — but data stays inconsistent. Rejected in favor of canonicalization.

**5. Tradeoffs** Canonicalization = clean data, simpler queries, index-ready; requires a careful one-time migration with collision handling. COLLATE would be less work but leaves messy stored values.

**6. Data Flow** Write path uppercases SKU → stored canonical. Read path uppercases search term → direct equality.

**7. Sequence Diagram**
```
Write: User/Import -> Service: sku="ab12"
Service -> DB: store UPPER("AB12")
Read: User -> Service: search "ab12"
Service -> DB: WHERE sku = 'AB12'   (no UPPER())
```

**8. Database Changes** — Data migration only (uppercase existing SKU values across `inventory_master`, `stock_ledger`, `channel_listings`, `product_master`, etc.). No schema change.

**9. API Changes** — Service query methods drop `UPPER()`; inbound terms normalized at method entry.

**10. Frontend Changes** — None (scan input already uppercases).

**11. Migration Strategy** — **Back up DB first.** Run inside a transaction; **pre-check for case-collisions** (distinct SKUs differing only by case) before uppercasing — resolve/merge those manually first. Verify FK integrity across ledger/inventory/listings after.

**12. Backward Compatibility** — Mixed-case input still matches (normalized at entry). Historical rows updated in place. **Collision edge:** if two distinct SKUs differ only by case, uppercasing merges them — must be detected and resolved before migration.

**13. Testing Strategy** — `pytest`: write paths store canonical case; lookups with mixed-case input succeed with no `UPPER()`. Run migration on a **restored copy first**; verify row counts + joins unchanged. Manual: SKU search + movement report.

**14. Deployment Strategy** — Backup → deploy code (write normalization + query change) → run one-time migration → verify.

**15. Rollback Strategy** — Restore DB snapshot; revert code. (Data migration is not auto-reversible — snapshot is the rollback.)

**16. Risk Assessment** — *Case-collision data merge* (highest): mandatory pre-check + manual resolution. *FK breakage*: verify post-migration. Risk: **medium (high if collisions unchecked).**

---

# Icebox — design deferred until trigger

Per the roadmap, these epics are **not designed now**; a full TDD is produced when the trigger fires.
- **E90 — Repository/DI/typed contracts** — trigger: a change repeatedly blocked by DataFrame-as-contract/untestability.
- **E92 — Orders & Returns first-class** — trigger: returns/RTO shown to materially distort margin/stock.
- **E93 — Shared table/filter/empty-state components** — trigger: first shared component extraction.
- **E94 — Action-Center home** — trigger: demand for a "what needs me today" view.
- **E95 — Indexes + pagination** — trigger: data ~100× or a slow page.
- **E96 — Postgres/Redis/re-platform** — trigger: measured lock contention / user thresholds.
- **E97 — PWA, offline, design system, accounting boundary, role-aware delivery** — trigger: specific per-item business need.
- **E91 — Opportunistic boundary hygiene** — no standalone design; enforced as a definition-of-done rule on any touched file.

---

## Approval
Implementation of any epic begins **only after its design section above is approved.** Suggested approval order mirrors the backlog: E1 → E2 → (E3, E4, E6) → E5 → E7 → E9 → (E8, E10, E11).
