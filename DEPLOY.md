# Deployment & Operations Runbook

Operational procedures for the production deployment. Application deploys are
`git pull` + `systemctl restart erp`; this file covers the **Sprint 1** work
(access control + backups) that lives partly on the server.

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
2. Add a `basic_auth` block to the site in `/etc/caddy/Caddyfile`:
   ```
   deepak-erp.duckdns.org {
       basic_auth {
           erp <PASTE_BCRYPT_HASH_HERE>
       }
       reverse_proxy 127.0.0.1:8501
   }
   ```
   (`erp` is the username; add more `user hash` lines for more accounts.)
3. Validate **before** reloading (a bad config can lock you out):
   ```
   caddy validate --config /etc/caddy/Caddyfile
   sudo systemctl reload caddy
   ```
4. Verify: `curl -I https://deepak-erp.duckdns.org` → **401**;
   `curl -I -u erp:PASSWORD https://deepak-erp.duckdns.org` → **200**.
   Confirm the app + camera scan still work in a browser and on Android.

### Rotate / remove a credential
- **Rotate:** regenerate the hash (step 1), replace it in the Caddyfile, `validate`, `reload`.
- **Remove a user:** delete their `user hash` line, `validate`, `reload`.
- Record credentials in the team password manager. **Never commit them to git.**

### Note for a future sprint
When the uptime monitor (Sprint 3 / E5) is added, its health path must be
**allow-listed past `basic_auth`** so the monitor can poll without credentials.
Not required yet.

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
