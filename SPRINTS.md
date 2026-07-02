# Implementation Sprints

Derived from [BACKLOG.md](BACKLOG.md) and [DESIGN.md](DESIGN.md).
Team: **one senior engineer (Claude)**. Delivery model: **iterative, trunk-based, app functional after every sprint.**

## Delivery principles
- **Short-lived branches** — one branch per story (or per sprint at most); merge within a day or two. No long-lived feature branches, no big-bang merges.
- **Every sprint is shippable** — deploy to the VPS at sprint end; the ERP must be fully usable after each.
- **Risky/irreversible work is isolated** — data migrations (E9, E11) get dedicated sprints, always preceded by a verified backup (E2).
- **Small PRs** — each story is independently reviewable and revertible.
- **Guard changes behind confirmation** rather than flags where a UI confirm is the natural safety (e.g., E4).

## Standard Sprint Exit Review (run at the end of EVERY sprint)
1. **Architecture validation** — layering intact: no new `get_engine`/`text(` under `ui/`; no new `import streamlit` under `core/`; services remain the mutation boundary.
2. **Regression review** — smoke every module (Home, Catalog, Inventory, Profit, Gap, Demand, Supplier, Onboarding) on desktop; smoke Inventory + camera on Android; run `pytest`.
3. **Performance review** — page loads feel unchanged or better; no new full-table reads shipped to the client; cache behavior verified.
4. **Security review** — no new secrets in the repo; no raw exceptions rendered; auth still gates all routes; destructive paths still confirmed.

*Each sprint below adds a sprint-specific focus to these four gates.*

---

## Sprint 1 — Secure & Protect
- **Sprint Goal:** Close the live security exposure and guarantee data recoverability before any further change.
- **Expected Duration:** ~2–3 working days.
- **Stories Included:**
  - E1-F1 (edge basic_auth): E1-F1-T1-S1, S2; E1-F1-T2-S1
  - E2-F1 (backups): E2-F1-T1-S1, S2; E2-F1-T2-S1
  - *(E1-F2 app login deferred to a later sprint — optional)*
- **Dependencies:** None. Must land **before** E9/E11 (which rely on backups) and before E5 (health-path allow-list depends on E1 existing).
- **Testing Requirements:** `curl` 401/200 auth checks; browser + Android smoke incl. camera; backup script produces an openable snapshot; **restore drill** into scratch path boots the app with correct product count.
- **Definition of Done:** All routes require auth; nightly backup timer enabled and verified; restore drill documented in `DEPLOY.md`; app fully usable post-change.
- **Potential Risks:** Caddy lockout (mitigate: `caddy validate` + open SSH); backup consistency (use SQLite online backup); offsite credential handling.
- **Exit Review focus:** Security (primary) — verify unauthenticated 401 everywhere; confirm backup + restore actually work.

---

## Sprint 2 — Hardening Quick Wins
- **Sprint Goal:** Remove the CDN dependency, make deletes safe, and stop leaking exceptions — small, high-value robustness.
- **Expected Duration:** ~2–3 working days.
- **Stories Included:**
  - E3-F1: E3-F1-T1-S1, S2 (self-host scanner)
  - E4-F1: E4-F1-T1-S1, S2; E4-F1-T2-S1 (safe deletes + audit)
  - E6-F1: E6-F1-T1-S1; E6-F1-T2-S1 (error helper + adopt on inventory/catalog)
- **Dependencies:** Sprint 1 (deploy on a secured app). E4 and E6 pair naturally (delete result uses the humane-error helper).
- **Testing Requirements:** Android scan smoke with **no unpkg request** (DevTools); `pytest` for scoped delete + count; forced-error checks show friendly message + full server log; grep confirms no raw delete/exception text in touched pages.
- **Definition of Done:** Scanner served from app origin; no UI-level destructive SQL without confirm; high-traffic pages show humane errors; app functional.
- **Potential Risks:** iframe static-path resolution (fallback: inline JS); delete scope-predicate parity (unit test guards it).
- **Exit Review focus:** Regression (scanner + catalog deletes) and Security (no leaked internals, deletes guarded).

---

## Sprint 3 — Deploy Discipline
- **Sprint Goal:** Make deploys safe (green-tests gate) and outages visible (uptime alerting).
- **Expected Duration:** ~1–2 working days.
- **Stories Included:**
  - E5-F1: E5-F1-T1-S1 (deploy.sh: pull → test → restart)
  - E5-F2: E5-F2-T1-S1 (health path allow-listed past auth), S2 (uptime monitor + alert)
  - E6-F1-T2-S2 (finish humane-error rollout to remaining pages + `main.py`)
- **Dependencies:** Sprint 1 (health path must be allow-listed in the E1 auth config).
- **Testing Requirements:** Force a failing test → deploy aborts before restart; revert → deploy proceeds; health path returns 200 without creds; test uptime alert received; grep shows no raw exceptions anywhere in `ui/`.
- **Definition of Done:** All deploys gated on green `pytest`; uptime monitor active with alerting; humane errors app-wide; app functional.
- **Potential Risks:** Auth blocking the monitor (allow-list health path only, keep it non-sensitive); flaky tests blocking deploy (keep suite deterministic).
- **Exit Review focus:** Architecture + Security (health path scoped, no sensitive data exposed) and a full Regression pass (error rollout touched every page).

---

## Sprint 4 — Architecture Enabler (decouple core)
- **Sprint Goal:** Make the core layer framework-free so it's testable/headless — unblocks the margin-config work.
- **Expected Duration:** ~2–4 working days.
- **Stories Included:**
  - E7-F1: E7-F1-T1-S1 (extract uncached core data functions), S2 (UI cache adapter + repoint callers)
- **Dependencies:** Best done before E9 (E9 reads config through these paths). Independent of Sprints 1–3 but sequenced after for stability.
- **Testing Requirements:** Import core module in a **no-streamlit** context (`pytest`); value-parity vs current for every moved function; manual "Refresh Data" + TTL behavior; verify `clear_*` invalidation still works. **Preserve the Excel/SQL config fallback exactly.**
- **Definition of Done:** `grep` shows no `import streamlit` under `core/`; pages behave identically; caching + invalidation unchanged; app functional.
- **Potential Risks:** Broken cache invalidation or lost config-fallback semantics — mitigate with function-by-function migration + parity tests; keep old `cache.py` as shim until proven.
- **Exit Review focus:** Architecture (primary — the whole point) and Performance (cache hit/TTL parity, no extra queries).

---

## Sprint 5 — Margin Engine Integrity (data-sensitive)
- **Sprint Goal:** Single source of truth for marketplace config/rules + visible freshness, without changing any margin figure.
- **Expected Duration:** ~2–4 working days.
- **Stories Included:**
  - E9-F1: E9-F1-T1-S1 (choose authoritative source), S2 (remove fallback, single path), E9-F1-T2-S1 (freshness indicator)
- **Dependencies:** E2 (verified backup — **mandatory** before running), E7 (clean config read paths).
- **Testing Requirements:** `pytest` on config read; **manual parity check of Profit dashboard figures before/after (critical gate)**; staleness warning appears for an old date; run consolidation on a **restored copy first**.
- **Definition of Done:** One config code path; existing values preserved (parity proven); freshness date shown with stale warning; app functional.
- **Potential Risks:** Value divergence → wrong margins (highest care): snapshot, parity-test, verify before deleting fallback; Excel now import-only (workflow change — communicate to users).
- **Exit Review focus:** Regression (Profit/Gap numbers unchanged) and a data-integrity spot-check; Security (backup exists, migration reversible via snapshot).

---

## Sprint 6 — UX Quick Wins
- **Sprint Goal:** Clearer navigation and a thumb-first mobile inventory entry.
- **Expected Duration:** ~2–3 working days.
- **Stories Included:**
  - E8-F1: E8-F1-T1-S1 (group nav), S2 (rename "Data Manager" → "Catalog")
  - E10-F1: E10-F1-T1-S1 (mobile landing on launcher), S2 (mobile-only bottom nav)
- **Dependencies:** E8 before E10 label consistency; both are UI-only, low risk.
- **Testing Requirements:** Manual — reach every module via new grouped nav (no dead entries); Android portrait: one-tap to Update/Transfer/Scan, bottom nav visible; desktop: bottom nav hidden, nav unchanged.
- **Definition of Done:** Grouped, correctly-named nav; mobile bottom nav functional ≤640px and hidden on desktop; app functional on both.
- **Potential Risks:** Streamlit internal class-name fragility for bottom nav (document selectors; buttons still work even if styling regresses).
- **Exit Review focus:** Regression (all modules reachable; desktop unaffected) and a mobile usability pass.

---

## Sprint 7 — SKU Normalization (data-sensitive)
- **Sprint Goal:** Canonicalize SKU casing, retire `UPPER()` filters — cleaner data, index-ready — with zero lost/merged SKUs.
- **Expected Duration:** ~2–4 working days.
- **Stories Included:**
  - E11-F1: E11-F1-T1-S1 (uppercase on write), S2 (one-time normalization migration), S3 (remove `UPPER()` from queries)
- **Dependencies:** E2 (verified backup — **mandatory**). Isolated in its own sprint due to irreversible data migration.
- **Testing Requirements:** `pytest` write paths store canonical case; lookups with mixed-case input succeed with no `UPPER()`; **pre-migration case-collision check** (distinct SKUs differing only by case); run migration on a **restored copy first**; verify row counts + FK joins unchanged; manual SKU search + movement report.
- **Definition of Done:** SKUs canonical across tables; no `UPPER(` in `src/core/services`; lookups correct; collision check clean; app functional.
- **Potential Risks:** Case-collision silently merging distinct SKUs (mandatory pre-check + manual resolution); FK breakage (verify post-migration). Rollback = restore snapshot (migration not auto-reversible).
- **Exit Review focus:** Regression (SKU-driven flows: update, transfer, movement report, listings) and data-integrity verification (counts/joins).

---

## Optional follow-on (schedule when wanted)
- **E1-F2 — App-level session login** (per-user identity, prereq for light roles). Small dedicated sprint; do when identity/roles are actually needed.

## Icebox (no sprints until triggered)
E90, E92–E97 and the as-you-touch items (E91, E93) remain unscheduled per the roadmap triggers. E91 (boundary hygiene) is enforced as a **definition-of-done rule on any touched file**, not a sprint.

---

## Sprint summary

| Sprint | Theme | Epics | Duration | Data-risk |
|---|---|---|---|---|
| 1 | Secure & Protect | E1, E2 | 2–3d | none |
| 2 | Hardening Quick Wins | E3, E4, E6 | 2–3d | low |
| 3 | Deploy Discipline | E5 (+E6 finish) | 1–2d | none |
| 4 | Architecture Enabler | E7 | 2–4d | low (cache) |
| 5 | Margin Engine Integrity | E9 | 2–4d | **high (parity)** |
| 6 | UX Quick Wins | E8, E10 | 2–3d | none |
| 7 | SKU Normalization | E11 | 2–4d | **high (migration)** |

**Sequencing rationale:** safety (S1) → cheap robustness (S2–S3) → enabler (S4) → the two data-sensitive epics isolated and backup-gated (S5, S7) with low-risk UX (S6) between them. Every sprint ends with the four-part Exit Review and a deploy, keeping the ERP continuously functional.
