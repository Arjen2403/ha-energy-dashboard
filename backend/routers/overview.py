"""/api/overview — live KPIs + 6h trend voor de Now-pagina.

Anders dan /api/flows leest dit endpoint uit de `states` tabel (live, sub-seconde),
niet uit de `statistics` LTS tabel. Veel grotere tabel, maar we filteren op een
korte tijdrange (laatste state of laatste 6 uur).
"""
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from ..db import ha_db
from ..pricing import import_price as vandebron_import_price

router = APIRouter()


KPI_ENTITIES = [
    "sensor.p1_meter_power",                 # signed: positief = import, negatief = export
    "sensor.trannergy_actual_power",         # PV vermogen (W)
    "sensor.nord_pool_nl_current_price",     # spot prijs (EUR/kWh)
    "sensor.boiler_outside_temperature",     # buitentemp via warmtepomp
]

TREND_ENTITIES = ["sensor.p1_meter_power", "sensor.trannergy_actual_power"]


class Kpi(BaseModel):
    grid_w: Optional[float] = None
    pv_w: Optional[float] = None
    house_w: Optional[float] = None
    import_price_eur_per_kwh: Optional[float] = None
    outside_temp_c: Optional[float] = None
    last_updated: Optional[datetime] = None


class TrendPoint(BaseModel):
    ts: datetime
    pv_w: Optional[float] = None
    grid_w: Optional[float] = None
    house_w: Optional[float] = None


class OverviewResponse(BaseModel):
    kpi: Kpi
    trend_6h: list[TrendPoint]


def _safe_float(s) -> Optional[float]:
    """Cast HA state-string naar float, of None bij unknown/unavailable/garbage."""
    if s is None or s in ("unknown", "unavailable", ""):
        return None
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _query_latest_states(conn, entity_ids: list[str]) -> dict:
    """Return {entity_id: {'state': str, 'ts': int}} voor laatste state per entity."""
    placeholders = ",".join(["?"] * len(entity_ids))
    sql = f"""
        WITH latest AS (
            SELECT s.metadata_id, MAX(s.last_updated_ts) AS ts
            FROM states s
            JOIN states_meta sm ON sm.metadata_id = s.metadata_id
            WHERE sm.entity_id IN ({placeholders})
            GROUP BY s.metadata_id
        )
        SELECT sm.entity_id, s.state, s.last_updated_ts AS ts
        FROM states s
        JOIN states_meta sm ON sm.metadata_id = s.metadata_id
        JOIN latest l
            ON l.metadata_id = s.metadata_id
            AND l.ts = s.last_updated_ts
    """
    return {
        r["entity_id"]: {"state": r["state"], "ts": r["ts"]}
        for r in conn.execute(sql, entity_ids)
    }


def _query_6h_trend(conn) -> list[TrendPoint]:
    """5-min buckets met avg power voor PV + grid over laatste 6 uur."""
    now_ts = int(datetime.now(timezone.utc).timestamp())
    from_ts = now_ts - 6 * 3600

    sql = """
        SELECT
            sm.entity_id,
            CAST(s.last_updated_ts / 300 AS INTEGER) * 300 AS bucket_ts,
            AVG(CAST(s.state AS REAL)) AS avg_value
        FROM states s
        JOIN states_meta sm ON sm.metadata_id = s.metadata_id
        WHERE sm.entity_id IN (?, ?)
          AND s.last_updated_ts >= ?
          AND s.state NOT IN ('unknown', 'unavailable', '')
        GROUP BY sm.entity_id, bucket_ts
        ORDER BY bucket_ts
    """
    rows = conn.execute(sql, [
        "sensor.p1_meter_power",
        "sensor.trannergy_actual_power",
        from_ts,
    ]).fetchall()

    # Pivot: één rij per bucket met pv_w + grid_w naast elkaar
    by_bucket = {}
    for r in rows:
        b = r["bucket_ts"]
        d = by_bucket.setdefault(b, {"pv_w": None, "grid_w": None})
        if r["entity_id"] == "sensor.p1_meter_power":
            d["grid_w"] = r["avg_value"]
        else:
            d["pv_w"] = r["avg_value"]

    points = []
    for ts in sorted(by_bucket.keys()):
        d = by_bucket[ts]
        house = (
            d["pv_w"] + d["grid_w"]
            if d["pv_w"] is not None and d["grid_w"] is not None
            else None
        )
        points.append(TrendPoint(
            ts=datetime.fromtimestamp(ts, tz=timezone.utc),
            pv_w=d["pv_w"],
            grid_w=d["grid_w"],
            house_w=house,
        ))
    return points


@router.get("/overview", response_model=OverviewResponse)
def get_overview():
    with ha_db() as conn:
        latest = _query_latest_states(conn, KPI_ENTITIES)
        trend = _query_6h_trend(conn)

    def state(eid):
        return _safe_float(latest.get(eid, {}).get("state"))

    grid_w = state("sensor.p1_meter_power")
    pv_w = state("sensor.trannergy_actual_power")
    spot = state("sensor.nord_pool_nl_current_price")
    outside_c = state("sensor.boiler_outside_temperature")

    house_w = (pv_w + grid_w) if pv_w is not None and grid_w is not None else None
    import_price = vandebron_import_price(spot) if spot is not None else None

    last_ts_unix = max(
        (v["ts"] for v in latest.values() if v.get("ts") is not None),
        default=None,
    )
    last_updated = (
        datetime.fromtimestamp(last_ts_unix, tz=timezone.utc)
        if last_ts_unix is not None
        else None
    )

    return OverviewResponse(
        kpi=Kpi(
            grid_w=grid_w,
            pv_w=pv_w,
            house_w=house_w,
            import_price_eur_per_kwh=import_price,
            outside_temp_c=outside_c,
            last_updated=last_updated,
        ),
        trend_6h=trend,
    )