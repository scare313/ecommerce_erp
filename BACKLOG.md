# Engineering Backlog

Derived from [ROADMAP.md](ROADMAP.md). Structure: **Epic → Feature → Task → Story.**
Stories are the unit of work — each is independently completable and PR-sized.

## How to use this file
- Import each **Epic** as a parent issue/label; each **Story** becomes a ticket. IDs are stable (`E1-F1-T1-S1`) — safe to reference in commits/PRs.
- Labels suggested: `phase-1`…`phase-7`, `type:security|infra|backend|frontend|ux|perf|docs`, `complexity:XS…XL`, `risk:low|med|high`.
- **Complexity:** XS ≤1h · S ≤½day · M ~1–2 days · L ~3–5 days · XL >1 week.

## Backlog scope decision (EM note)
Only **committed** roadmap work is decomposed into stories: **Phase 1 (E1–E6)** and the discrete scheduled items **2.1 (E7), 3.1 (E8), 3.2 (E9), 4.1 (E10), 5.1 (E11)**. Everything trigger-gated or "as-you-touch" (2.2, 2.3, 3.3, 4.2, 4.3, 5.2, Phases 6–7) is kept in the **Icebox (E90+)** as epics only — no stories until the trigger fires. Writing detailed stories for deferred work contradicts the roadmap and rots before pickup.

Global testing note: pure logic → `pytest` under `tests/`. UI/Streamlit changes have no automated harness today → **manual verification** on the deployed app (desktop + Android) is the acceptance gate. "Testing requirements" per story reflects this reality.

---

# E1 — Authentication gate on the public app  `phase-1` `type:security` `risk:high`
Close the live exposure: the app is internet-public with no auth.

## E1-F1 — Edge auth at the reverse proxy (fast mitigation)
Gate all traffic at Caddy before app-level identity exists.

### E1-F1-T1 — Add HTTP Basic auth in Caddy
- **E1-F1-T1-S1 — Generate and store a bcrypt credential**
  - **Goal:** Have a hashed credential ready for Caddy `basic_auth`.
  - **Description:** Use `caddy hash-password` to create a bcrypt hash for one shared or per-user account; store the hash in the Caddyfile (not plaintext).
  - **Acceptance criteria:** Hash generated; no plaintext password stored on disk; credential recorded in the team password manager.
  - **Dependencies:** None.
  - **Complexity:** XS.
  - **Risk:** low.
  - **Files likely affected:** `/etc/caddy/Caddyfile` (VPS, not repo).
  - **Testing:** Manual — confirm hash format accepted by `caddy validate`.
- **E1-F1-T1-S2 — Apply `basic_auth` to the site block and reload**
  - **Goal:** Require credentials for every request to the app hostname.
  - **Description:** Add a `basic_auth` directive to the `deepak-erp.duckdns.org` block; `caddy validate` then `systemctl reload caddy`.
  - **Acceptance criteria:** Unauthenticated request returns 401; correct credentials load the app; camera scan flow still works over HTTPS.
  - **Dependencies:** S1.
  - **Complexity:** XS.
  - **Risk:** med (lockout if mistyped) — validate before reload; keep an SSH session open.
  - **Files likely affected:** `/etc/caddy/Caddyfile`.
  - **Testing:** Manual — `curl -I` (expect 401), `curl -u` (expect 200); browser + phone smoke test.

### E1-F1-T2 — Document the edge-auth setup
- **E1-F1-T2-S1 — Record auth setup in ops docs**
  - **Goal:** Make the auth config reproducible/recoverable.
  - **Description:** Add a short "Access & Auth" section to a repo `DEPLOY.md` (or README) describing the Caddy basic_auth setup and how to rotate the credential.
  - **Acceptance criteria:** Doc explains add/rotate/remove credential; no secrets committed.
  - **Dependencies:** S2.
  - **Complexity:** XS.
  - **Risk:** low.
  - **Files likely affected:** `DEPLOY.md` (new) or `README.md`.
  - **Testing:** Peer read-through.

## E1-F2 — App-level login (durable identity) — *optional follow-on*
Only if per-user identity is wanted (prereq for light roles later).

### E1-F2-T1 — Minimal session login in the app shell
- **E1-F2-T1-S1 — Add a login gate before page routing**
  - **Goal:** Block rendering of any page until authenticated within the app.
  - **Description:** In the app entry, check a session flag; if unset, render a login form and `st.stop()`. Validate against credentials from `st.secrets` (never hardcoded).
  - **Acceptance criteria:** No page renders pre-login; wrong password rejected; refresh keeps session; logout clears it.
  - **Dependencies:** None (can coexist with E1-F1).
  - **Complexity:** M.
  - **Risk:** med — ensure DB-init still runs and no page logic executes before the gate.
  - **Files likely affected:** `main.py`; new `src/ui/components/auth.py`; `.streamlit/secrets.toml` (gitignored).
  - **Testing:** Manual — login/logout/refresh matrix; confirm `secrets.toml` not committed.

---

# E2 — Automated off-box database backup  `phase-1` `type:infra` `risk:low`
Protect the single SQLite file from total-loss.

## E2-F1 — Scheduled backup job
### E2-F1-T1 — Backup script + schedule
- **E2-F1-T1-S1 — Write a backup script using SQLite online backup**
  - **Goal:** Produce a consistent daily snapshot without stopping the app.
  - **Description:** Script runs `sqlite3 <db> ".backup"` (or `VACUUM INTO`) to a timestamped file, then uploads/copies off-VM; prunes older than N days.
  - **Acceptance criteria:** Running the script yields a valid, openable copy; retention prunes correctly; exit code non-zero on failure.
  - **Dependencies:** None.
  - **Complexity:** S.
  - **Risk:** low — use online backup API, not a raw file copy mid-write.
  - **Files likely affected:** `scripts/backup_db.sh` (new).
  - **Testing:** Manual — run script; open snapshot with `sqlite3 .tables`.
- **E2-F1-T1-S2 — Schedule via systemd timer (or cron)**
  - **Goal:** Run the backup nightly, unattended, surviving reboot.
  - **Description:** Add a systemd service+timer (or crontab) invoking the script; log output.
  - **Acceptance criteria:** Timer enabled; fires on schedule; produces a snapshot each night.
  - **Dependencies:** S1.
  - **Complexity:** XS.
  - **Risk:** low.
  - **Files likely affected:** `scripts/backup_db.sh`; systemd unit files (VPS); `DEPLOY.md`.
  - **Testing:** Manual — `systemctl list-timers`; verify next-morning snapshot.

### E2-F1-T2 — Restore drill
- **E2-F1-T2-S1 — Document and perform a restore test**
  - **Goal:** Prove backups are actually recoverable.
  - **Description:** Restore a snapshot into a scratch path, launch app against it, confirm data loads; write the steps down.
  - **Acceptance criteria:** App runs on restored DB; product count matches; restore steps documented.
  - **Dependencies:** S2.
  - **Complexity:** S.
  - **Risk:** low — never restore over live DB; use a scratch copy.
  - **Files likely affected:** `DEPLOY.md`.
  - **Testing:** Manual restore drill.

---

# E3 — Self-host the barcode-scanner library  `phase-1` `type:frontend` `risk:low`
Remove the runtime unpkg CDN dependency for the core floor action.

## E3-F1 — Vendor and serve the scanner JS locally
### E3-F1-T1 — Bundle the library as a static asset
- **E3-F1-T1-S1 — Vendor a pinned `html5-qrcode` build into the repo**
  - **Goal:** Own the exact scanner JS version.
  - **Description:** Download the pinned `html5-qrcode@2.3.8` minified build into a static assets folder; record version + source in a NOTICE/README.
  - **Acceptance criteria:** File present, version pinned, license/attribution noted.
  - **Dependencies:** None.
  - **Complexity:** XS.
  - **Risk:** low.
  - **Files likely affected:** `src/ui/static/html5-qrcode.min.js` (new) or Streamlit `static/`; `NOTICE`/README.
  - **Testing:** File integrity check (size/version).
- **E3-F1-T1-S2 — Reference the local asset from the scanner component**
  - **Goal:** Load the scanner from the app origin, not unpkg.
  - **Description:** Replace the `<script src="https://unpkg.com/...">` in the camera HTML with the locally served path (inline or via Streamlit static serving).
  - **Acceptance criteria:** DevTools shows zero external request for the scanner lib; camera opens, scans, and auto-fills exactly as before on Android over HTTPS.
  - **Dependencies:** S1.
  - **Complexity:** S.
  - **Risk:** med — Streamlit static-file serving path must resolve inside the srcdoc iframe; fallback to inlining the JS if needed.
  - **Files likely affected:** `src/ui/components/barcode_scanner.py` (the `_CAMERA_SCANNER_HTML`/`<script>` reference).
  - **Testing:** Manual — phone scan smoke test; Network tab shows no unpkg call.

---

# E4 — Make destructive actions safe  `phase-1` `type:backend` `risk:med`
No irreversible delete triggerable from the UI without a service + confirm.

## E4-F1 — Service-owned deletes with confirmation
### E4-F1-T1 — Move raw deletes into the service layer
- **E4-F1-T1-S1 — Add a delete method to the catalog/listing service**
  - **Goal:** Single, guarded code path for destructive listing operations.
  - **Description:** Add e.g. `delete_listings_for_marketplace(marketplace)` to the owning service, using a parameterized statement inside a transaction; return affected-row count; log the action.
  - **Acceptance criteria:** Method deletes only the scoped rows; parameter-bound; wrapped in a transaction; logs count.
  - **Dependencies:** None.
  - **Complexity:** S.
  - **Risk:** med — verify scoping predicate matches the previous UI query exactly.
  - **Files likely affected:** `src/core/services/catalog_service.py` (or the relevant service).
  - **Testing:** `pytest` unit test on a temp SQLite DB asserting scoped deletion + count.
- **E4-F1-T1-S2 — Replace the raw UI `DELETE` with a confirmed service call**
  - **Goal:** Remove `text("DELETE …")`/`get_engine` from the page and require explicit confirmation.
  - **Description:** In `data_manager.py`, swap the inline SQL for the service call behind a two-step confirm (checkbox/second-click) and show a humane success/error result.
  - **Acceptance criteria:** No `DELETE`/`get_engine` remain in the page's destructive path; deletion requires confirm; success and failure both messaged clearly.
  - **Dependencies:** S1; pairs with E6.
  - **Complexity:** S.
  - **Risk:** med.
  - **Files likely affected:** `src/ui/pages/data_manager.py`.
  - **Testing:** Manual — attempt delete without confirm (blocked), with confirm (works); `grep` shows no raw delete in UI.

### E4-F1-T2 — Sweep for other UI-level destructive SQL
- **E4-F1-T2-S1 — Audit `ui/` for direct destructive statements**
  - **Goal:** Ensure E4 is complete, not spot-fixed.
  - **Description:** Grep `ui/` for `DELETE`/`UPDATE`/`INSERT`/`get_engine`/`text(`; log any destructive finds as follow-up stories.
  - **Acceptance criteria:** Audit documented; each destructive hit is either migrated or ticketed.
  - **Dependencies:** None.
  - **Complexity:** XS.
  - **Risk:** low.
  - **Files likely affected:** none (audit); output → new stories.
  - **Testing:** N/A (analysis).

---

# E5 — Test-before-deploy + uptime check  `phase-1` `type:infra` `risk:low`

## E5-F1 — Deploy gate on green tests
### E5-F1-T1 — Deploy script that runs tests first
- **E5-F1-T1-S1 — Create a deploy script (pull → test → restart)**
  - **Goal:** Prevent shipping a red build.
  - **Description:** Script does `git pull`, activates venv, runs `pytest`, and only on success restarts the service; aborts loudly on failure.
  - **Acceptance criteria:** Failing tests abort before restart; passing tests restart the app; script is idempotent.
  - **Dependencies:** None.
  - **Complexity:** S.
  - **Risk:** low.
  - **Files likely affected:** `scripts/deploy.sh` (new); `DEPLOY.md`.
  - **Testing:** Manual — force a failing test, confirm no restart; revert, confirm deploy.

## E5-F2 — External uptime monitoring
### E5-F2-T1 — Health endpoint + monitor
- **E5-F2-T1-S1 — Expose a lightweight health signal**
  - **Goal:** Give an external monitor something cheap to poll.
  - **Description:** Provide a minimal health check (Streamlit's built-in `/_stcore/health`, or a documented reachable path) usable by an uptime service.
  - **Acceptance criteria:** Endpoint returns healthy when app is up; documented.
  - **Dependencies:** interacts with E1 (monitor must be allowed past auth — allowlist the health path).
  - **Complexity:** XS.
  - **Risk:** low — don't expose sensitive data on the path.
  - **Files likely affected:** `/etc/caddy/Caddyfile` (path allowlist); `DEPLOY.md`.
  - **Testing:** Manual — `curl` health path returns 200 without credentials.
- **E5-F2-T1-S2 — Configure an external uptime monitor + alert**
  - **Goal:** Get notified within minutes of downtime.
  - **Description:** Register the URL with a free uptime service; set an alert channel (email).
  - **Acceptance criteria:** Monitor active; test alert received; check interval ≤5 min.
  - **Dependencies:** S1.
  - **Complexity:** XS.
  - **Risk:** low.
  - **Files likely affected:** none (external); `DEPLOY.md`.
  - **Testing:** Manual — trigger a test alert.

---

# E6 — Humane error messages  `phase-1` `type:ux` `risk:low`

## E6-F1 — Centralized user-facing error presentation
### E6-F1-T1 — Error helper
- **E6-F1-T1-S1 — Add a UI error helper that hides internals**
  - **Goal:** One place that shows a friendly message and logs the real detail.
  - **Description:** Helper takes an exception + context, logs full detail (with the existing logger), and renders a generic, actionable `st.error`.
  - **Acceptance criteria:** Given an exception, UI shows no stack/exception text; full detail appears in server logs.
  - **Dependencies:** None.
  - **Complexity:** S.
  - **Risk:** low.
  - **Files likely affected:** `src/ui/components/errors.py` (new) or extend `src/infrastructure/logger.py`.
  - **Testing:** `pytest` on the log path; manual UI check.

### E6-F1-T2 — Adopt the helper across pages
- **E6-F1-T2-S1 — Replace `st.error(f"...{e}")` in inventory + data manager**
  - **Goal:** Fix the highest-traffic pages first.
  - **Description:** Swap raw exception rendering for the helper in `inventory_manager.py` and `data_manager.py`.
  - **Acceptance criteria:** No `{e}`/`str(e)` rendered in these pages; behavior otherwise unchanged.
  - **Dependencies:** E6-F1-T1-S1.
  - **Complexity:** S.
  - **Risk:** low.
  - **Files likely affected:** `src/ui/pages/inventory_manager.py`, `src/ui/pages/data_manager.py`.
  - **Testing:** Manual — force an error, confirm friendly message + logged detail.
- **E6-F1-T2-S2 — Roll out to remaining pages + top-level handler**
  - **Goal:** Consistent errors everywhere.
  - **Description:** Apply the helper to the other pages and the `main.py` top-level try/except.
  - **Acceptance criteria:** Repo-wide grep shows no raw exception text rendered in `ui/`.
  - **Dependencies:** S1.
  - **Complexity:** M.
  - **Risk:** low.
  - **Files likely affected:** `src/ui/pages/*.py`, `main.py`.
  - **Testing:** Manual smoke of each page's error path.

---

# E7 — Decouple cache/core from Streamlit  `phase-2` `type:backend` `risk:med`
Remove `import streamlit` from `src/core/cache.py` so core runs headless.

## E7-F1 — Move memoization to the UI edge
### E7-F1-T1 — Framework-free core data functions
- **E7-F1-T1-S1 — Extract uncached data functions in core**
  - **Goal:** Pure functions returning data with no `@st.cache_data`.
  - **Description:** Split each cached function in `cache.py` into a plain core function (no streamlit) holding the query logic.
  - **Acceptance criteria:** Core functions importable without streamlit installed; return same data.
  - **Dependencies:** None.
  - **Complexity:** M.
  - **Risk:** med — preserve existing fallback logic (e.g., Excel/SQL config) exactly.
  - **Files likely affected:** `src/core/cache.py` → new `src/core/data_access.py` (or similar).
  - **Testing:** `pytest` importing the module in a no-streamlit context; value parity checks.
- **E7-F1-T1-S2 — Add a UI-side cache adapter and repoint callers**
  - **Goal:** Keep caching behavior, at the UI layer.
  - **Description:** Wrap the core functions with `@st.cache_data` in a UI adapter; update page imports; keep TTLs and `clear_*` semantics.
  - **Acceptance criteria:** `grep` shows no `import streamlit` under `core/`; pages behave identically; cache-clear still works.
  - **Dependencies:** S1.
  - **Complexity:** M.
  - **Risk:** med — verify cache invalidation paths (`clear_all_caches`, `clear_inventory_cache`).
  - **Files likely affected:** new `src/ui/cache_adapter.py`; `src/ui/pages/*.py`; `src/core/cache.py` (removed/trimmed).
  - **Testing:** Manual — data refresh + "Refresh Data" button; confirm TTL behavior.

---

# E8 — Navigation grouping + rename  `phase-3` `type:ux` `risk:low`

## E8-F1 — Grouped, correctly-named navigation
### E8-F1-T1 — Restructure the sidebar
- **E8-F1-T1-S1 — Group modules into workflow sets**
  - **Goal:** Replace the flat 8-item list with grouped navigation.
  - **Description:** Organize nav into sets (e.g., Operate / Sell / Buy / Analyze) using grouped controls or captions.
  - **Acceptance criteria:** All modules reachable; grouping visible; no dead links.
  - **Dependencies:** None.
  - **Complexity:** S.
  - **Risk:** low.
  - **Files likely affected:** `main.py`.
  - **Testing:** Manual — visit every module from the new nav.
- **E8-F1-T1-S2 — Rename "Data Manager" to a meaningful label**
  - **Goal:** Fix the vague name (it's the catalog).
  - **Description:** Rename the nav label (e.g., "Catalog"); update any user-facing references.
  - **Acceptance criteria:** New label shown; routing unchanged; no stale references in UI copy.
  - **Dependencies:** S1.
  - **Complexity:** XS.
  - **Risk:** low.
  - **Files likely affected:** `main.py`; page title in `src/ui/pages/data_manager.py`.
  - **Testing:** Manual.

---

# E9 — Protect the margin engine (single config source + freshness)  `phase-3` `type:backend` `risk:med`

## E9-F1 — Single source of truth for marketplace config/rules
### E9-F1-T1 — Consolidate Excel/SQL config duality
- **E9-F1-T1-S1 — Choose and document the authoritative config source**
  - **Goal:** Decide SQL-vs-Excel-of-record and write it down.
  - **Description:** Analyze current readers (`config_rules.py`, `cache.py` `get_marketplaces`, `init_db.py` seeding); pick one source of record (Excel as import only, SQL authoritative recommended); document the decision.
  - **Acceptance criteria:** Decision recorded; all current read sites enumerated.
  - **Dependencies:** None.
  - **Complexity:** S.
  - **Risk:** low (analysis).
  - **Files likely affected:** docs; audit of `src/infrastructure/config_rules.py`, `src/core/cache.py`, `src/infrastructure/init_db.py`.
  - **Testing:** N/A.
- **E9-F1-T1-S2 — Remove fallback branching; read from one source**
  - **Goal:** Eliminate silent divergence.
  - **Description:** Refactor readers to use the authoritative source; keep Excel strictly as an import step if retained.
  - **Acceptance criteria:** One config code path; existing values preserved (back up DB first); marketplaces/rules unchanged in the UI.
  - **Dependencies:** S1; E7 helpful.
  - **Complexity:** M.
  - **Risk:** med — value-preservation is critical; snapshot DB before running.
  - **Files likely affected:** `src/core/cache.py`/`data_access.py`, `src/infrastructure/config_rules.py`, `src/infrastructure/init_db.py`.
  - **Testing:** `pytest` on config read; manual parity check of Profit dashboard numbers before/after.

### E9-F1-T2 — Rule freshness indicator
- **E9-F1-T2-S1 — Add and display "rules last verified" metadata**
  - **Goal:** Make staleness visible so margins stay trusted.
  - **Description:** Store a last-verified date for the fee rules; surface it on the config/profit surface with a warning when older than a threshold.
  - **Acceptance criteria:** Date shown; stale state visually flagged; editable when rules are re-verified.
  - **Dependencies:** S2.
  - **Complexity:** S.
  - **Risk:** low.
  - **Files likely affected:** `src/infrastructure/config_rules.py` (or schema), `src/ui/pages/data_manager.py`/`profit_dashboard.py`.
  - **Testing:** Manual — set old date → warning appears.

---

# E10 — Mobile navigation for the floor persona  `phase-4` `type:frontend` `risk:low`

## E10-F1 — Thumb-friendly mobile entry to core inventory actions
### E10-F1-T1 — Ensure phone users land on actions, not an empty drawer
- **E10-F1-T1-S1 — Auto-focus the Inventory launcher on small screens**
  - **Goal:** Phone users see the action launcher immediately.
  - **Description:** Ensure that on mobile the Inventory page (existing launcher) is the effective landing for the floor persona and the core actions are one tap away; refine launcher affordances if needed.
  - **Acceptance criteria:** On Android portrait, Update/Transfer/Scan reachable in one tap; desktop nav unaffected.
  - **Dependencies:** builds on existing launcher + `mobile_style.py`.
  - **Complexity:** M.
  - **Risk:** low — do not regress desktop.
  - **Files likely affected:** `src/ui/pages/inventory_manager.py`, `src/ui/components/mobile_style.py`.
  - **Testing:** Manual — Android portrait + desktop regression.
- **E10-F1-T1-S2 — Sticky bottom-nav for the core actions (mobile only)**
  - **Goal:** App-like, thumb-reachable navigation on phones.
  - **Description:** Add a mobile-only bottom navigation for the primary inventory actions, scoped via the existing mobile CSS media query.
  - **Acceptance criteria:** Bottom nav visible only ≤640px; switches sections; hidden on desktop; no overlap with content.
  - **Dependencies:** S1.
  - **Complexity:** M.
  - **Risk:** med — relies on Streamlit container class scoping (fragile across upgrades); document the selector.
  - **Files likely affected:** `src/ui/components/mobile_style.py`, `src/ui/pages/inventory_manager.py`.
  - **Testing:** Manual — Android portrait + desktop confirm hidden.

---

# E11 — SKU case-normalization  `phase-5` `type:backend` `risk:med`

## E11-F1 — Normalize SKU case, retire `UPPER()` filters
### E11-F1-T1 — Normalize on write
- **E11-F1-T1-S1 — Uppercase SKUs at all write/ingest points**
  - **Goal:** Store SKUs in one canonical case.
  - **Description:** Ensure services/parsers uppercase SKU on insert/update so stored values are canonical.
  - **Acceptance criteria:** New writes store uppercase SKUs; verified across manual entry, transfer, and bulk import.
  - **Dependencies:** None.
  - **Complexity:** S.
  - **Risk:** low.
  - **Files likely affected:** `src/core/services/inventory_service.py`, `catalog_service.py`, `bulk_service.py`, `src/infrastructure/parsers.py`.
  - **Testing:** `pytest` on write paths asserting canonical case.
- **E11-F1-T1-S2 — One-time normalization of existing rows**
  - **Goal:** Make historical data consistent before dropping `UPPER()`.
  - **Description:** Idempotent migration uppercasing existing SKU columns; back up DB first.
  - **Acceptance criteria:** All existing SKUs canonical; row counts unchanged; FK integrity intact.
  - **Dependencies:** S1; E2 (backup) must exist.
  - **Complexity:** M.
  - **Risk:** med — snapshot DB; run in a transaction; verify referential integrity across ledger/inventory/listings.
  - **Files likely affected:** `src/infrastructure/migration_script.py` (or `init_db.py`).
  - **Testing:** Run on a restored copy first; verify counts + joins.
- **E11-F1-T1-S3 — Remove `UPPER()` from service queries**
  - **Goal:** Simplify queries (and unblock future indexing).
  - **Description:** Replace the 18 `UPPER(sku)` predicates with direct equality on canonical SKUs.
  - **Acceptance criteria:** No `UPPER(` in `src/core/services`; SKU lookups still correct for mixed-case input (normalized at query entry).
  - **Dependencies:** S1, S2.
  - **Complexity:** S.
  - **Risk:** med — ensure inbound search terms are uppercased before querying.
  - **Files likely affected:** `src/core/services/*.py`.
  - **Testing:** `pytest` lookups with mixed-case input; manual SKU search + movement report.

---

# Icebox — trigger-gated epics (no stories until the trigger fires)

> Kept visible for planning, intentionally **not** decomposed. Create stories only when the stated trigger is met.

- **E90 — Repository seam / DI / typed contracts** (roadmap 2.3) — *trigger:* a change repeatedly blocked by DataFrame-as-contract or untestability.
- **E91 — Opportunistic boundary hygiene** (roadmap 2.2) — *not a scheduled epic;* handle inline when editing a file (definition-of-done addendum: "no new raw SQL/`get_engine` in `ui/`").
- **E92 — Orders & Returns as first-class** (roadmap 3.3) — *trigger:* owner confirms returns/RTO materially distort margin/stock. Effort L–XL.
- **E93 — Consistent table/filter/empty-state pattern** (roadmap 4.2) — *as-you-touch;* promote to stories when a shared component is first extracted.
- **E94 — Action-Center home** (roadmap 4.3) — *trigger:* demand for a "what needs me today" view.
- **E95 — Indexes + payload trimming/pagination** (roadmap 5.2) — *trigger:* data grows ~100× or a page feels slow.
- **E96 — Scalability (Postgres / Redis / re-platform)** (roadmap Phase 6) — *trigger:* measured "database is locked" under normal use / defined user thresholds.
- **E97 — Future vision (PWA, push, offline, design system, accounting boundary, role-aware delivery)** (roadmap Phase 7) — *trigger:* specific business need per item.

---

## Suggested execution order
1. **E1 (auth)** → **E2 (backups)** — safety first, both quick.
2. **E3 (scanner), E4 (safe deletes), E6 (humane errors)** — small, high-value hardening.
3. **E5 (deploy gate + uptime)** — lock in quality before further changes.
4. **E7 (decouple core)** → unblocks **E9 (margin config)**.
5. **E8 (nav), E10 (mobile nav), E11 (SKU normalization)** — fold into ongoing feature work.
6. Icebox stays closed until triggered.
