"""Test runner voor v_hourly_heatpump.

Voert de view uit voor de laatste 48 uur en print elke rij — bedoeld om met
het oog te valideren dat de SQL doet wat we verwachten.
"""
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

from backend.views import query_hourly_heatpump

load_dotenv()
db_path = Path(os.environ["HA_DB_PATH"])

now = datetime.now(timezone.utc)
to_ts = (int(now.timestamp()) // 3600) * 3600
from_ts = to_ts - 48 * 3600

conn = sqlite3.connect(str(db_path), timeout=5.0)
conn.execute("PRAGMA query_only = 1")
conn.row_factory = sqlite3.Row

rows = query_hourly_heatpump(conn, from_ts, to_ts)
conn.close()

print(f"\nQuery range: {datetime.fromtimestamp(from_ts, tz=timezone.utc)} -> "
      f"{datetime.fromtimestamp(to_ts, tz=timezone.utc)}")
print(f"Rows returned: {len(rows)}\n")

cols = ["hour", "cons", "supp", "heat", "dhw", "shtg", "sdhw", "out°", "flw°", "rtn°"]
widths = [17, 6, 6, 6, 6, 6, 6, 6, 6, 6]
header = " ".join(f"{c:<{w}}" for c, w in zip(cols, widths))
print(header)
print("-" * len(header))

def fmt(v, fmt_str=".1f"):
    return f"{v:{fmt_str}}" if v is not None else "    -"

for r in rows:
    hour_str = datetime.fromtimestamp(r["hour_ts"], tz=timezone.utc).strftime("%m-%d %H:%M UTC")
    print(" ".join([
        f"{hour_str:<17}",
        f"{fmt(r['consumption_total_kwh']):<6}",
        f"{fmt(r['supplied_total_kwh']):<6}",
        f"{fmt(r['consumption_heating_kwh']):<6}",
        f"{fmt(r['consumption_dhw_kwh']):<6}",
        f"{fmt(r['supplied_heating_kwh']):<6}",
        f"{fmt(r['supplied_dhw_kwh']):<6}",
        f"{fmt(r['outside_temp_c']):<6}",
        f"{fmt(r['flow_temp_c']):<6}",
        f"{fmt(r['return_temp_c']):<6}",
    ]))