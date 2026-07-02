# ERP Improvement Roadmap

> Guiding principles: **incremental over rewrite · evidence over rubric · right-sized for 1–10 internal users.**
> The current SQLite + Streamlit foundation is a deliberate, correct choice for this scale and is **not** slated for replacement. Later phases are **trigger-gated** — we do that work only when a stated threshold is actually crossed.

Effort key: **S** ≤1 day · **M** ~2–4 days · **L** ~1–2 weeks · **XL** >2 weeks.

---

## Phase 1 — Foundation (do now; strong evidence, low cost, real risk)

### 1.1 Authentication gate on the public app
- **Objective:** Require a login before any access to the internet-exposed app.
- **Business value:** Closes a catastrophic, live confidentiality/tampering exposure (deletes + financials are currently open to anyone with the URL).
- **Technical value:** Establishes an identity boundary reusable later for light role separation.
- **Dependencies:** None. (Caddy is already the entry point.)
- **Risk:** Minor login friction for 1–10 users; misconfiguration could lock out — mitigate by testing before switching DNS traffic.
- **Effort:** S (proxy/basic auth) → M (per-user login).
- **Expected outcome:** No unauthenticated request reaches the app.
- **Success metrics:** 100% of routes require auth; verified 401 on unauthenticated access; zero anonymous sessions in logs.

### 1.2 Automated off-box database backup
- **Objective:** Nightly copy of the SQLite file to off-VM storage, with a tested restore.
- **Business value:** Eliminates single-point-of-total-data-loss.
- **Technical value:** Restore drill documents recovery.
- **Dependencies:** None.
- **Risk:** Negligible.
- **Effort:** S.
- **Expected outcome:** Recoverable daily snapshots retained (e.g., 7–14 days).
- **Success metrics:** Backup present each morning; a restore test succeeds; RPO ≤ 24h.

### 1.3 Self-host the barcode-scanner library
- **Objective:** Serve the scanner JS (currently loaded from unpkg CDN) from the app itself.
- **Business value:** The core floor action (scanning) stops depending on an external CDN — better reliability on poor networks and improved privacy.
- **Technical value:** Removes a runtime third-party dependency.
- **Dependencies:** None.
- **Risk:** Version pinning; test camera flow after change.
- **Effort:** S.
- **Expected outcome:** Scanner works with no external CDN request.
- **Success metrics:** Zero external requests for the scanner lib; camera scan success unchanged on phone.

### 1.4 Make destructive actions safe
- **Objective:** Route the raw destructive SQL in the UI (e.g., `DELETE FROM channel_listings` in `data_manager.py`) through a service method with an explicit confirmation step.
- **Business value:** Prevents accidental irreversible data loss.
- **Technical value:** Restores the service layer as the boundary for mutations (safety, not architectural purism).
- **Dependencies:** None.
- **Risk:** Low; localized change.
- **Effort:** S.
- **Expected outcome:** No destructive DB operation is triggerable directly from the UI without confirmation.
- **Success metrics:** All deletes require confirm; no `DELETE`/`get_engine` in UI destructive paths.

### 1.5 Test-before-deploy + single uptime check
- **Objective:** Run the existing test suite before each deploy; add one external uptime ping.
- **Business value:** Catches regressions before users; know within minutes if the app is down.
- **Technical value:** Right-sized ops without a CI platform.
- **Dependencies:** None.
- **Risk:** None.
- **Effort:** S.
- **Expected outcome:** Broken builds don't ship; outages surface proactively.
- **Success metrics:** 100% of deploys preceded by a green test run; uptime alert fires on downtime.

### 1.6 Humane error messages
- **Objective:** Replace raw exception text in the UI with plain, actionable messages; log detail server-side.
- **Business value:** Less alarming, more trustworthy UX.
- **Technical value:** Centralized error presentation.
- **Dependencies:** None.
- **Risk:** Ensure server logs still capture full detail.
- **Effort:** S–M.
- **Expected outcome:** Users never see a stack trace.
- **Success metrics:** No raw exceptions rendered; errors still fully logged.

---

## Phase 2 — Architecture (surgical only; not a refactor program)

> Rejected as premature: full Clean Architecture/DDD, repository+DI+DTO program. Do boundary cleanup **opportunistically as you touch files**, plus one enabling decouple.

### 2.1 Decouple the cache/core from Streamlit
- **Objective:** Remove the `import streamlit` dependency from `src/core/cache.py` so core logic can run outside the web process.
- **Business value:** Enables scheduled jobs (e.g., nightly low-stock digest) and real unit tests later.
- **Technical value:** Fixes the one coupling with concrete downside; caching policy moves to the UI edge.
- **Dependencies:** None.
- **Risk:** Cache wiring must be re-established at the UI layer; regression-test cached reads.
- **Effort:** M.
- **Expected outcome:** `core/` imports no UI framework.
- **Success metrics:** `grep` shows no `streamlit` import under `core/`; cached pages behave identically.

### 2.2 Opportunistic boundary hygiene (as-you-touch)
- **Objective:** When editing a page, move any raw SQL/`get_engine` use into a service.
- **Business value:** Slows accumulation of duplicated data-access logic.
- **Technical value:** Gradually restores the service boundary without a big-bang refactor.
- **Dependencies:** Follows 1.4.
- **Risk:** None if scoped per-change.
- **Effort:** S per file (amortized).
- **Expected outcome:** New/edited UI code contains no direct DB access.
- **Success metrics:** Trend: count of `get_engine`/`text(` under `ui/` decreases over time.

### 2.3 (Deferred) Repository seam / DI / typed contracts
- **Objective:** Introduce only if a specific change is repeatedly blocked by DataFrame-as-contract or untestability.
- **Effort now:** 0. **Trigger:** a real bug or change that the current structure demonstrably obstructs.

---

## Phase 3 — Product Improvements

### 3.1 Navigation grouping + rename (quick win)
- **Objective:** Group the 8 modules into workflow sets and rename the vague "Data Manager" to a meaningful label (e.g., Catalog).
- **Business value:** Faster orientation; less confusion; better discoverability.
- **Technical value:** Nav config change only.
- **Dependencies:** None.
- **Risk:** Minimal (users relearn labels once).
- **Effort:** S–M.
- **Expected outcome:** Clear, grouped, correctly-named navigation.
- **Success metrics:** Users locate a target module in fewer clicks; qualitative confusion reports drop.

### 3.2 Protect the margin engine (single config source + freshness)
- **Objective:** End the Excel-vs-SQL config duality (one source of truth) and surface a "rules last verified" indicator on the marketplace fee rules.
- **Business value:** Defends the product's moat — trustworthy margins; prevents silent staleness feeding wrong numbers.
- **Technical value:** Removes scattered fallback branches.
- **Dependencies:** 2.1 helps but not required.
- **Risk:** Config migration must preserve current values (back up first).
- **Effort:** M.
- **Expected outcome:** One authoritative config; visible freshness state.
- **Success metrics:** Single config path in code; freshness date shown; no divergence between sources.

### 3.3 (Trigger-gated) Orders & Returns as first-class
- **Objective:** Model orders/returns explicitly if returns/RTO are shown to materially affect margin/stock.
- **Effort now:** 0. **Trigger:** owner confirms returns are a real pain point / margin distortion. **Effort when triggered:** L–XL.

---

## Phase 4 — UX Improvements

### 4.1 Mobile navigation for the Inventory (floor) persona
- **Objective:** Give the confirmed phone persona a thumb-friendly bottom-nav/launcher for core actions; ensure phone users don't land on an empty hamburger.
- **Business value:** Faster, fewer-error floor operations.
- **Technical value:** Builds on the existing launcher pattern.
- **Dependencies:** None.
- **Risk:** Keep desktop nav intact.
- **Effort:** M.
- **Expected outcome:** Core inventory actions reachable in one tap on mobile.
- **Success metrics:** Reduced taps to Update/Transfer/Scan; positive floor-staff feedback.

### 4.2 Consistent table / filter / empty-state pattern (as-you-touch)
- **Objective:** Standardize how tables, filters, and empty states look/behave across modules; convert key mobile lists to cards/detail where a screen is actually used on phones.
- **Business value:** Coherent, learnable UX; better mobile usability.
- **Technical value:** Reusable UI patterns reduce per-screen ad-hoc code.
- **Dependencies:** 3.1.
- **Risk:** Scope creep — apply incrementally, not as a big redesign.
- **Effort:** M–L (amortized).
- **Expected outcome:** Uniform data-presentation patterns.
- **Success metrics:** New screens reuse the shared pattern; fewer overflow/usability complaints.

### 4.3 (Trigger-gated) Action-Center home
- **Objective:** Replace the metric-wall home with a prioritized, clickable task feed.
- **Effort now:** 0. **Trigger:** demand for a "what needs me today" view. **Effort when triggered:** L.

---

## Phase 5 — Performance (mostly deferred; act on data growth, not theory)

### 5.1 SKU case-normalization (correctness, cheap)
- **Objective:** Normalize SKU case on write to retire the 18 `UPPER()` filters.
- **Business value:** Consistent SKU matching.
- **Technical value:** Simplifies queries; prerequisite for future indexing.
- **Dependencies:** None.
- **Risk:** One-time normalization of existing rows (back up first).
- **Effort:** S–M.
- **Expected outcome:** Case-insensitive matching without `UPPER()` scans.
- **Success metrics:** No `UPPER(` in service queries; SKU lookups still correct.

### 5.2 (Trigger-gated) Indexes + payload trimming/pagination
- **Objective:** Add indexes and paginate/limit rows shipped to the client.
- **Effort now:** 0 (≈22 products today — scans are instant). **Trigger:** data grows ~100×, or a page feels slow. **Effort when triggered:** S–M.

---

## Phase 6 — Scalability (no work now; documented triggers only)

> The current stack is correct for 1–10 users. Record the thresholds so the decision is data-driven later.

- **~100 concurrent users →** revisit SQLite write contention; consider Postgres. **Effort:** L. **Trigger:** repeated "database is locked" under normal use.
- **~1,000 users →** Postgres + connection pooling + shared cache (Redis) + multiple app replicas. **Effort:** XL.
- **~10,000+ users →** re-platform the delivery layer off Streamlit (keep domain logic + data model). **Effort:** XL.
- **Risk of acting early:** wasted spend and added ops burden for load you don't have. **Recommendation:** no action until a trigger is measured.

---

## Phase 7 — Future Vision (optional, business-driven)

- **PWA install + push notifications** (low-stock/PO reminders) — nice-to-have; **trigger:** staff want app-like/home-screen use. Effort: M.
- **Offline scan capture + sync** — **only if** warehouse network is measured to be unreliable (no evidence today). Effort: L.
- **Design-system maturity** (tokens/components/accessibility baseline) — as the app grows; Effort: L.
- **Accounting/GST boundary** — do **not** build accounting into this app; when compliance becomes core, integrate ERPNext or Tally for that slice and keep this as the tailored ops + margin brain. Effort: integration, not rewrite.
- **Role-aware delivery** (streamlined mobile shell for floor vs rich desktop for analyst) — once personas diverge further. Effort: M–L.

---

## Sequencing summary
1. **Phase 1 first, in full** — it's cheap, high-value, and closes the one real emergency (auth) plus protects data (backups).
2. **Phases 2–5 are incremental and mostly "as-you-touch"** — fold into ongoing feature work; only 2.1, 3.1, 3.2, 4.1, 5.1 are discrete small/medium efforts worth scheduling.
3. **Phases 6–7 are trigger-gated** — zero work until a stated threshold or business need is real.
4. **No rewrite. No technology swap.** SQLite + Streamlit remain until a Phase-6 trigger is measured.
