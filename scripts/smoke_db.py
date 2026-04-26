"""Smoke test: kan ik de HA SQLite read-only openen en rows tellen?"""
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
db_path = Path(os.environ["HA_DB_PATH"])

print(f"Pad: {db_path}")
print(f"Bestaat: {db_path.exists()}")
if not db_path.exists():
    raise SystemExit("Bestand niet gevonden")

print(f"Grootte: {db_path.stat().st_size / 1024 / 1024:.1f} MB")

conn = sqlite3.connect(str(db_path), timeout=5.0)
conn.execute("PRAGMA query_only = 1")
conn.row_factory = sqlite3.Row

cur = conn.cursor()
cur.execute("SELECT COUNT(*) AS n FROM states")
print(f"states rows: {cur.fetchone()['n']}")

cur.execute("SELECT COUNT(*) AS n FROM statistics")
print(f"statistics rows: {cur.fetchone()['n']}")

cur.execute("SELECT MAX(last_updated_ts) AS ts FROM states")
last_ts = cur.fetchone()["ts"]
if last_ts:
    last_iso = datetime.fromtimestamp(last_ts, tz=timezone.utc).isoformat()
    print(f"last_state_ts: {last_iso}")
else:
    print("last_state_ts: (geen rows)")

conn.close()