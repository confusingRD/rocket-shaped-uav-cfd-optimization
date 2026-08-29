"""Thread-safe SQLite write coordination for parallel campaign workers."""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from reporting.production_db import connect

_db_write_lock = threading.Lock()


def get_db_write_lock() -> threading.Lock:
    """Return the process-wide database write lock."""
    return _db_write_lock


@contextmanager
def db_write_lock() -> Iterator[None]:
    """Serialize SQLite writes across parallel campaign workers."""
    with _db_write_lock:
        yield


@contextmanager
def locked_connection(db_path: Path) -> Iterator[sqlite3.Connection]:
    """Open a SQLite connection under the global write lock."""
    with db_write_lock():
        conn = connect(db_path)
        try:
            yield conn
        finally:
            conn.close()
