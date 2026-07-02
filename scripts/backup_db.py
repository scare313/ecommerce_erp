#!/usr/bin/env python3
"""Create a consistent, verified backup of the ERP SQLite database.

Roadmap E2 (see BACKLOG.md / DESIGN.md). Designed to run unattended from a
systemd timer (deploy/erp-backup.timer).

Why this exists: the whole business lives in one SQLite file. This produces a
timestamped, integrity-checked snapshot using SQLite's online-backup API — safe
to run against the live database while the app is serving requests — then prunes
old snapshots and (optionally) pushes the snapshot off-box.

Design notes:
- Uses the Python stdlib `sqlite3` module's Connection.backup(), which is the
  canonical safe way to copy a live SQLite DB. No `sqlite3` CLI dependency.
- Snapshots are written OUTSIDE the repository (default: ~/erp_backups) so a
  `git pull` never touches them and they are never accidentally committed.
- Everything is configurable via environment variables so the systemd unit can
  override paths without editing this file.

Configuration (all optional, sensible defaults):
    ERP_DB_PATH              Path to the live DB. Default: <repo>/data/db/ecommerce.db
    ERP_BACKUP_DIR           Where snapshots are written. Default: ~/erp_backups
    ERP_BACKUP_RETENTION_DAYS Days to keep snapshots. Default: 14
    ERP_BACKUP_OFFSITE_CMD   Optional shell command to push a snapshot off-box.
                             "{snapshot}" is substituted with the snapshot path.
                             Example: 'rclone copy "{snapshot}" remote:erp-backups'

Exit code: 0 on success, non-zero on any failure (so systemd marks the unit failed).
"""
import os
import sys
import glob
import time
import shlex
import sqlite3
import subprocess
from datetime import datetime, timedelta

# Resolve <repo> as the parent of this script's directory (scripts/ -> repo root).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_DB = os.path.join(_REPO_ROOT, "data", "db", "ecommerce.db")
_DEFAULT_BACKUP_DIR = os.path.join(os.path.expanduser("~"), "erp_backups")


def _log(message: str) -> None:
    """Print a timestamped line (captured by the systemd journal)."""
    print(f"{datetime.now().isoformat(timespec='seconds')} backup_db: {message}", flush=True)


def _create_snapshot(db_path: str, backup_dir: str) -> str:
    """Copy the live DB to a timestamped snapshot via the online-backup API."""
    os.makedirs(backup_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    snapshot = os.path.join(backup_dir, f"ecommerce-{stamp}.db")

    # Connection.backup() copies a live database safely without blocking writers
    # for the whole operation. Read-only source open to avoid any accidental write.
    source = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        dest = sqlite3.connect(snapshot)
        try:
            source.backup(dest)
        finally:
            dest.close()
    finally:
        source.close()
    return snapshot


def _verify(snapshot: str) -> None:
    """Fail loudly if the snapshot does not pass SQLite's integrity check."""
    conn = sqlite3.connect(snapshot)
    try:
        result = conn.execute("PRAGMA integrity_check").fetchone()
    finally:
        conn.close()
    if not result or result[0] != "ok":
        raise RuntimeError(f"integrity check failed for {snapshot}: {result}")


def _prune(backup_dir: str, retention_days: int) -> int:
    """Delete snapshots older than the retention window. Returns count removed."""
    cutoff = time.time() - retention_days * 86400
    removed = 0
    for path in glob.glob(os.path.join(backup_dir, "ecommerce-*.db")):
        if os.path.getmtime(path) < cutoff:
            os.remove(path)
            removed += 1
    return removed


def _offsite(snapshot: str, command_template: str) -> None:
    """Run the optional off-box copy command. Raises on non-zero exit."""
    command = command_template.replace("{snapshot}", snapshot)
    _log(f"offsite copy: {command}")
    subprocess.run(shlex.split(command), check=True)


def main() -> int:
    db_path = os.environ.get("ERP_DB_PATH", _DEFAULT_DB)
    backup_dir = os.environ.get("ERP_BACKUP_DIR", _DEFAULT_BACKUP_DIR)
    retention_days = int(os.environ.get("ERP_BACKUP_RETENTION_DAYS", "14"))
    offsite_cmd = os.environ.get("ERP_BACKUP_OFFSITE_CMD", "").strip()

    if not os.path.exists(db_path):
        _log(f"ERROR: database not found at {db_path}")
        return 1

    try:
        snapshot = _create_snapshot(db_path, backup_dir)
        _verify(snapshot)
        size_kb = os.path.getsize(snapshot) // 1024
        _log(f"snapshot OK: {snapshot} ({size_kb} KB)")

        if offsite_cmd:
            _offsite(snapshot, offsite_cmd)
            _log("offsite copy OK")
        else:
            _log("offsite copy skipped (ERP_BACKUP_OFFSITE_CMD not set)")

        removed = _prune(backup_dir, retention_days)
        _log(f"prune complete: removed {removed} snapshot(s) older than {retention_days}d")
        return 0
    except Exception as exc:  # unattended job: log and signal failure to systemd
        _log(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
