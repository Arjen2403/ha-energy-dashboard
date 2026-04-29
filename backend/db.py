"""SQLite read-only connection helper voor de HA database."""
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from .config import settings


def _connect_with_retry(db_path: str, retries: int = 3, delay: float = 0.5):
    """Open SQLite read-only met retry op transient SMB-related errors."""
    last_err = None
    for attempt in range(retries):
        try:
            conn = sqlite3.connect(db_path, timeout=30.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only = 1")
            return conn
        except sqlite3.OperationalError as e:
            msg = str(e).lower()
            if "disk i/o error" in msg or "database is locked" in msg:
                last_err = e
                time.sleep(delay * (attempt + 1))
                continue
            raise
    raise last_err


@contextmanager
def ha_db():
    """Open de HA SQLite read-only via _connect_with_retry."""
    p = Path(settings.ha_db_path)
    if not p.exists():
        raise FileNotFoundError(f"HA DB niet gevonden: {p}")

    conn = _connect_with_retry(str(p))
    try:
        yield conn
    finally:
        conn.close()