"""Test runner voor v_hourly_energy_flows.

Voert de view uit voor de laatste 48 uur en print elke rij — bedoeld om met
het oog te valideren dat de SQL doet wat we verwachten.
"""
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

from backend.views import query_hourly_flows

load_dotenv()
db_path = Path(os.environ["HA_DB_PATH"])

now = datetime.now(timezone.utc)
to_ts = int(now.timestamp())
from_ts = int((now - timedelta(hours=48)).timestamp())

# Floor naar hele uur — LTS rows hebben start_ts op :00
to_ts = (to_ts // 3600) * 3600
from_ts = (from_ts // 3600) * 3600

conn = sqlite3.connect(str(db_path), timeout=5.0)
conn.execute("PRAGMA query_only = 1")
conn.row_factory = sqlite3.Row

rows = query_hourly_flows(conn, from_ts, to_ts)
conn.close()

print(f"\nQuery range: {datetime.fromtimestamp(from_ts, tz=timezone.utc)} -> "
      f"{datetime.fromtimestamp(to_ts, tz=timezone.utc)}")
print(f"Rows returned: {len(rows)}\n")

# Header
cols = ["hour", "import", "export", "pv", "hp_in", "hp_out", "quook", "afwas", "spot"]
widths = [17, 8, 8, 8, 8, 8, 8, 8, 10]
header = " ".join(f"{c:<{w}}" for c, w in zip(cols, widths))
print(header)
print("-" * len(header))

for r in rows:
    hour_str = datetime.fromtimestamp(r["hour_ts"], tz=timezone.utc).strftime("%m-%d %H:%M UTC")
    def fmt(v, fmt_str=".3f"):
        return f"{v:{fmt_str}}" if v is not None else "    -"
    line = " ".join([
        f"{hour_str:<17}",
        f"{fmt(r['import_kwh']):<8}",
        f"{fmt(r['export_kwh']):<8}",
        f"{fmt(r['pv_kwh']):<8}",
        f"{fmt(r['heatpump_kwh']):<8}",
        f"{fmt(r['heatpump_supplied_kwh']):<8}",
        f"{fmt(r['quooker_kwh']):<8}",
        f"{fmt(r['afwasmachine_kwh']):<8}",
        f"{fmt(r['spot_price'], '.4f'):<10}",
    ])
    print(line)