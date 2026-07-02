# Deployment & Operations Runbook

Operational procedures for the production deployment. Deploys are done via
`./scripts/deploy.sh` (§3), which only restarts the app if the test suite
passes. This file also covers access control (§1) and backups (§2) — the
Sprint 1/2/3 work that lives partly on the server.

## Production topology
- **Host:** Oracle VPS, Ubuntu. App at `/home/ubuntu/ecommerce_erp`, venv at `.venv`.
- **Process:** `erp` systemd service runs Streamlit bound to `127.0.0.1:8501`.
- **Edge:** Caddy terminates HTTPS (`deepak-erp.duckdns.org`) and reverse-proxies to the app.
- **Data:** single SQLite file at `data/db/ecommerce.db` (not tracked in git).

---

## 1. Access control — Caddy Basic Auth (Roadmap E1)

The app must never be served without authentication. Auth is enforced at the
**edge (Caddy)** so no unauthenticated request reaches the app.

### Initial setup
1. Generate a bcrypt hash for the password (never store plaintext):
   ```
   caddy hash-password --plaintext 'YOUR_STRONG_PASSWORD'
   ```
2. Add a `basic_auth` block to the site in `/etc/caddy/Caddyfile`. The health
   path is exempted (matched separately, before the auth-gated `handle`) so
   the uptime monitor set up in §3 can poll it without credentials — every
   other path still requires the password:
   ```
   deepak-erp.duckdns.org {
       @health path /_stcore/health
       handle @health {
           reverse_proxy 127.0.0.1:8501
       }

       handle {
           basic_auth {
               erp <PASTE_BCRYPT_HASH_HERE>
           }
           reverse_proxy 127.0.0.1:8501
       }
   }
   ```
   (`erp` is the username; add more `user hash` lines for more accounts.)
3. Validate **before** reloading (a bad config can lock you out):
   ```
   caddy validate --config /etc/caddy/Caddyfile
   sudo systemctl reload caddy
   ```
4. Verify:
   - `curl -I https://deepak-erp.duckdns.org` → **401**
   - `curl -I -u erp:PASSWORD https://deepak-erp.duckdns.org` → **200**
   - `curl -I https://deepak-erp.duckdns.org/_stcore/health` → **200, no credentials needed**
   - Confirm the app + camera scan still work in a browser and on Android.

### Rotate / remove a credential
- **Rotate:** regenerate the hash (step 1), replace it in the Caddyfile, `validate`, `reload`.
- **Remove a user:** delete their `user hash` line, `validate`, `reload`.
- Record credentials in the team password manager. **Never commit them to git.**

---

## 2. Database backups (Roadmap E2)

Nightly, integrity-checked snapshots via `scripts/backup_db.py`, scheduled by a
systemd timer. Snapshots are written **outside** the repo (default `~/erp_backups`).

### Install
```
# From the repo on the VPS:
sudo cp deploy/erp-backup.service /etc/systemd/system/
sudo cp deploy/erp-backup.timer   /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now erp-backup.timer
```

### Configure (optional)
Defaults: DB at `<repo>/data/db/ecommerce.db`, snapshots in `~/erp_backups`,
14-day retention. Override via `Environment=` lines in `erp-backup.service`
(see the commented examples there and the docstring in `scripts/backup_db.py`).

### Off-box copies (important)
Local snapshots protect against DB corruption and accidental deletes, but **not
against loss of the VM**. For real disaster recovery, set
`ERP_BACKUP_OFFSITE_CMD` (e.g. an `rclone`/`rsync` command) so each snapshot is
pushed to remote storage. Until then, backups are on-box only — call this out
in any DR assessment.

### Verify
```
systemctl list-timers | grep erp-backup          # shows next run time
sudo systemctl start erp-backup.service          # run once now
journalctl -u erp-backup.service --no-pager -n 20  # expect "snapshot OK"
ls -la ~/erp_backups                             # a fresh ecommerce-<ts>.db
```

### Restore drill (do this at least once)
Never restore over the live DB blindly. Restore into a scratch path and boot the
app against it first:
```
# 1. Pick a snapshot and copy it to a scratch location
cp ~/erp_backups/ecommerce-<timestamp>.db /tmp/restore-test.db

# 2. Sanity-check it opens and has data
python3 - <<'PY'
import sqlite3
c = sqlite3.connect("/tmp/restore-test.db")
print("integrity:", c.execute("PRAGMA integrity_check").fetchone()[0])
print("products:", c.execute("SELECT COUNT(*) FROM product_master").fetchone()[0])
PY
```
A real restore = stop `erp`, copy the chosen snapshot over
`data/db/ecommerce.db`, start `erp`. Keep a copy of the current file first.
```
sudo systemctl stop erp
cp data/db/ecommerce.db data/db/ecommerce.db.pre-restore
cp ~/erp_backups/ecommerce-<timestamp>.db data/db/ecommerce.db
sudo systemctl start erp
```

---

## 3. Deploy discipline & uptime monitoring (Roadmap E5)

Replaces the bare `git pull` + `systemctl restart erp` deploy with a script
that only restarts the app if the test suite is green, plus an external
uptime check so an outage is noticed within minutes instead of by chance.

### Deploy via `scripts/deploy.sh`
```
cd /home/ubuntu/ecommerce_erp
./scripts/deploy.sh
```
It pulls, runs `pytest` via the venv, and **only restarts `erp.service` if
the suite passes.** A failing test leaves the currently-running app untouched
and exits non-zero — re-run after fixing the failure.

**One-time setup — passwordless restart for the deploy user.** The script
calls `sudo systemctl restart erp`, so the `ubuntu` user needs permission to
run *that exact command* without a password prompt (deploys are often
unattended). Scope it narrowly with `visudo`:
```
# /etc/sudoers.d/erp-deploy — add via `sudo visudo -f /etc/sudoers.d/erp-deploy`
ubuntu ALL=(root) NOPASSWD: /usr/bin/systemctl restart erp
```
This grants nothing beyond restarting the one service — not a general sudo
grant.

### Health check path
Streamlit exposes a built-in health endpoint at `/_stcore/health` (no config
needed — it's part of the framework). It's allow-listed past Basic Auth in
the Caddyfile above (§1) so external monitors can poll it without credentials.
Verify: `curl -I https://deepak-erp.duckdns.org/_stcore/health` → **200**.

### Register an uptime monitor
Use any uptime service (e.g. UptimeRobot, Better Uptime, healthchecks.io — a
free tier is enough for this traffic level):
1. Monitor URL: `https://deepak-erp.duckdns.org/_stcore/health`
2. Expected: HTTP 200, check interval ≤5 minutes
3. Alert channel: email (or whatever the service supports)
4. Trigger a test alert (most services offer a "test notification" button) to
   confirm delivery actually works before relying on it.

### Verify the whole loop
```
# Force a failing test to prove the gate works, then revert:
#   (temporarily break a test, run ./scripts/deploy.sh, confirm exit != 0
#    and that `systemctl status erp` still shows the OLD process running)
sudo systemctl status erp   # confirm running + note the start time
./scripts/deploy.sh         # should pull, test, and restart on success
sudo systemctl status erp   # start time should now be newer
```
