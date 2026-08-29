"""Rolling incremental SQLite database backups."""

from __future__ import annotations

import re
import shutil
import sqlite3
from pathlib import Path

from campaign.constants import DATA_DIR, DEFAULT_BACKUP_RETENTION, DEFAULT_DB_PATH
from reporting.production_db import utc_now_iso

BACKUP_PATTERN = re.compile(r"^production_backup_(\d{3})\.db$")
GENERIC_BACKUP_PATTERN = re.compile(r"^(.+)_backup_(\d{3})\.db$")


def list_backups(data_dir: Path = DATA_DIR, *, backup_prefix: str = "production") -> list[Path]:
    backups = []
    pattern = re.compile(rf"^{re.escape(backup_prefix)}_backup_(\d{{3}})\.db$")
    for path in sorted(data_dir.glob(f"{backup_prefix}_backup_*.db")):
        if pattern.match(path.name):
            backups.append(path)
    return backups


def next_backup_path(data_dir: Path = DATA_DIR, *, backup_prefix: str = "production") -> Path:
    existing = list_backups(data_dir, backup_prefix=backup_prefix)
    if not existing:
        return data_dir / f"{backup_prefix}_backup_001.db"
    last = existing[-1]
    match = re.match(rf"^{re.escape(backup_prefix)}_backup_(\d{{3}})\.db$", last.name)
    seq = int(match.group(1)) + 1 if match else len(existing) + 1
    return data_dir / f"{backup_prefix}_backup_{seq:03d}.db"


def list_production_backups(data_dir: Path = DATA_DIR) -> list[Path]:
    """Legacy helper — production numbered backups only."""
    backups = []
    for path in sorted(data_dir.glob("production_backup_*.db")):
        if BACKUP_PATTERN.match(path.name):
            backups.append(path)
    return backups


def next_production_backup_path(data_dir: Path = DATA_DIR) -> Path:
    """Legacy helper — next production backup filename."""
    return next_backup_path(data_dir, backup_prefix="production")


def backup_database(
    db_path: Path = DEFAULT_DB_PATH,
    *,
    data_dir: Path | None = None,
    retention: int = DEFAULT_BACKUP_RETENTION,
    backup_prefix: str = "production",
) -> Path | None:
    """Create a numbered backup copy; never overwrites the primary database."""
    data_dir = data_dir or db_path.parent
    if not db_path.exists():
        return None

    data_dir.mkdir(parents=True, exist_ok=True)
    dest = next_backup_path(data_dir, backup_prefix=backup_prefix)

    # Ensure WAL is checkpointed into the main file before copying.
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.commit()

    shutil.copy2(db_path, dest)
    _rotate_backups(data_dir, retention, backup_prefix=backup_prefix)
    return dest


def _rotate_backups(data_dir: Path, retention: int, *, backup_prefix: str = "production") -> None:
    backups = list_backups(data_dir, backup_prefix=backup_prefix)
    while len(backups) > retention:
        oldest = backups.pop(0)
        oldest.unlink(missing_ok=True)


def restore_database(
    backup_path: Path,
    db_path: Path = DEFAULT_DB_PATH,
    *,
    create_safety_copy: bool = True,
) -> Path:
    """Restore primary database from a numbered backup."""
    if not backup_path.exists():
        raise FileNotFoundError(f"Backup not found: {backup_path}")
    if create_safety_copy and db_path.exists():
        safety = db_path.with_name(
            f"production_pre_restore_{utc_now_iso().replace(':', '').replace('+', '')}.db"
        )
        shutil.copy2(db_path, safety)
    shutil.copy2(backup_path, db_path)
    return db_path
