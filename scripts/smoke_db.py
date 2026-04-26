"""Smoke test: kan ik de HA SQLite read-only openen en rows tellen?"""
import os
import sqlite3
from dotenv import load_dotenv

load_dotenv()
db_path = os.environ["HA_DB_PATH"]
uri = f"file:{db_path}?mode=ro&immutable=0"

with sqlite3.connect(uri, uri=True) as conn:
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM states")
    print(f"states rows: {cur.fetchone()[0]}")
    cur.execute("SELECT COUNT(*) FROM statistics")
    print(f"statistics rows: {cur.fetchone()[0]}")
