"""SQLite read-only connection helper voor de HA database."""
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .config import settings


@contextmanager
def ha_db():
    """Open de HA SQLite read-only en geef een sqlite3.Connection terug."""
    p = Path(settings.ha_db_path)
    if not p.exists():
        raise FileNotFoundError(f"HA DB niet gevonden: {p}")

    # SQLite URI mode trips on UNC authorities (\\host\share),
    # dus open via plain path + force query_only at SQL level.
    conn = sqlite3.connect(str(p), timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = 1")
    try:
        yield conn
    finally:
        conn.close()