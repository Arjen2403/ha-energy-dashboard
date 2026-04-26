"""Verkenning: welke entity_ids hebben we in LTS, wat zijn hun units, en hoeveel
rows staan er voor elk in de afgelopen 7 dagen?

Dit is een onderzoeks-script — output is bedoeld om te lezen, niet om te
verwerken. Gebruik het om te bepalen welke statistic_ids we straks in
v_hourly_energy_flows gaan opnemen.
"""
import sqlite3
from pathlib import Path
from dotenv import load_dotenv
import os

load_dotenv()
db_path = Path(os.environ["HA_DB_PATH"])

conn = sqlite3.connect(str(db_path), timeout=5.0)
conn.execute("PRAGMA query_only = 1")
conn.row_factory = sqlite3.Row

# Stap 1: alle metadata uit statistics_meta — wat zit er in LTS, hoe is het getypeerd?
print("\n=== ALLE STATISTIC_IDS IN LTS ===\n")
print(f"{'statistic_id':<70} {'unit':<10} {'mean_type':<10} {'has_sum':<8}")
print("-" * 100)
rows = conn.execute("""
    SELECT statistic_id, unit_of_measurement, mean_type, has_sum
    FROM statistics_meta
    ORDER BY statistic_id
""").fetchall()
for r in rows:
    unit = r["unit_of_measurement"] or "-"
    print(f"{r['statistic_id']:<70} {unit:<10} {r['mean_type']!s:<10} {r['has_sum']!s:<8}")

# Stap 2: voor has_sum=1 entities (cumulatief kWh), check hoeveel uur-rows
# we hebben en wat het tijdsbereik is
print("\n\n=== HAS_SUM=1 ENTITIES — uur-rows + tijdsbereik ===\n")
print(f"{'statistic_id':<70} {'rows':<8} {'first':<22} {'last':<22}")
print("-" * 130)
rows = conn.execute("""
    SELECT
        sm.statistic_id,
        COUNT(*) AS n,
        MIN(s.start_ts) AS first_ts,
        MAX(s.start_ts) AS last_ts
    FROM statistics s
    JOIN statistics_meta sm ON sm.id = s.metadata_id
    WHERE sm.has_sum = 1
    GROUP BY sm.statistic_id
    ORDER BY sm.statistic_id
""").fetchall()
from datetime import datetime, timezone
for r in rows:
    first = datetime.fromtimestamp(r["first_ts"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
    last = datetime.fromtimestamp(r["last_ts"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
    print(f"{r['statistic_id']:<70} {r['n']:<8} {first:<22} {last:<22}")

# Stap 3: idem voor mean_type=1 (instant values: power W, temperature °C, prijs €/kWh)
print("\n\n=== MEAN_TYPE=1 ENTITIES — uur-rows + tijdsbereik ===\n")
print(f"{'statistic_id':<70} {'rows':<8} {'first':<22} {'last':<22}")
print("-" * 130)
rows = conn.execute("""
    SELECT
        sm.statistic_id,
        COUNT(*) AS n,
        MIN(s.start_ts) AS first_ts,
        MAX(s.start_ts) AS last_ts
    FROM statistics s
    JOIN statistics_meta sm ON sm.id = s.metadata_id
    WHERE sm.mean_type = 1
    GROUP BY sm.statistic_id
    ORDER BY sm.statistic_id
""").fetchall()
for r in rows:
    first = datetime.fromtimestamp(r["first_ts"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
    last = datetime.fromtimestamp(r["last_ts"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
    print(f"{r['statistic_id']:<70} {r['n']:<8} {first:<22} {last:<22}")

conn.close()