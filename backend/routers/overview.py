"""/api/overview — live KPIs + 6h trend + Sankey flow data voor de Now-pagina."""
from datetime import datetime, timezone
from typing import Optional

from cachetools import TTLCache
from fastapi import APIRouter
from pydantic import BaseModel

from ..db import ha_db
from ..pricing import import_price as vandebron_import_price

router = APIRouter()

_overview_cache: TTLCache = TTLCache(maxsize=1, ttl=60)
_overview_stale: dict = {}


KPI_ENTITIES = [
    "sensor.p1_meter_power",
    "sensor.trannergy_actual_power",
    "sensor.nord_pool_nl_current_price",
    "sensor.boiler_outside_temperature",
    "sensor.socket_quooker_power",            # W
    "sensor.socket_afwasmachine_power",       # W
]


class Kpi(BaseModel):
    grid_w: Optional[float] = None
    pv_w: Optional[float] = None
    house_w: Optional[float] = None
    import_price_eur_per_kwh: Optional[float] = None
    outside_temp_c: Optional[float] = None
    last_updated: Optional[datetime] = None
    # Appliance breakdown voor Sankey
    heatpump_w: Optional[float] = None
    quooker_w: Optional[float] = None
    afwasmachine_w: Optional[float] = None
    overig_w: Optional[float] = None


class TrendPoint(BaseModel):
    ts: datetime
    pv_w: Optional[float] = None
    grid_w: Optional[float] = None
    house_w: Optional[float] = None


class OverviewResponse(BaseModel):
    kpi: Kpi
    trend_6h: list[TrendPoint]


def _safe_float(s) -> Optional[float]:
    if s is None or s in ("unknown", "unavailable", ""):
        return None
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _query_latest_states(conn, entity_ids: list[str]) -> dict:
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


def _query_heatpump_elec_w(conn) -> Optional[float]:
    """Derive instantaneous electrical power (W) from the cumulative kWh counter.

    Takes the oldest and newest readings of boiler_total_energy_consumption over
    the last 15 minutes and computes delta_kWh / delta_h * 1000 = W.
    Returns None when the pump is off (no state change) or data is insufficient.
    """
    now_ts = int(datetime.now(timezone.utc).timestamp())
    rows = conn.execute(
        """
        SELECT s.state, s.last_updated_ts AS ts
        FROM states s
        JOIN states_meta sm ON sm.metadata_id = s.metadata_id
        WHERE sm.entity_id = 'sensor.boiler_total_energy_consumption'
          AND s.last_updated_ts >= ?
          AND s.state NOT IN ('unknown', 'unavailable', '')
        ORDER BY s.last_updated_ts
        """,
        (now_ts - 15 * 60,),
    ).fetchall()

    if len(rows) < 2:
        return None
    try:
        kwh_old = float(rows[0]["state"])
        kwh_new = float(rows[-1]["state"])
        dt_h = (rows[-1]["ts"] - rows[0]["ts"]) / 3600.0
        if dt_h <= 0:
            return None
        return max(0.0, (kwh_new - kwh_old) / dt_h * 1000)
    except (TypeError, ValueError):
        return None


def _query_6h_trend(conn) -> list[TrendPoint]:
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
    cache_key = "data"
    cached = _overview_cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        with ha_db() as conn:
            latest = _query_latest_states(conn, KPI_ENTITIES)
            trend = _query_6h_trend(conn)
            heatpump_elec_w = _query_heatpump_elec_w(conn)

        def state(eid):
            return _safe_float(latest.get(eid, {}).get("state"))

        grid_w = state("sensor.p1_meter_power")
        pv_w = state("sensor.trannergy_actual_power")
        spot = state("sensor.nord_pool_nl_current_price")
        outside_c = state("sensor.boiler_outside_temperature")

        # Appliance powers - heat pump: electrical W derived from cumulative kWh rate of change
        # (boiler_compressor_power_output reports thermal output, not electrical consumption)
        heatpump_w = heatpump_elec_w
        quooker_w = state("sensor.socket_quooker_power")
        afwasmachine_w = state("sensor.socket_afwasmachine_power")

        house_w = (pv_w + grid_w) if pv_w is not None and grid_w is not None else None
        import_price = vandebron_import_price(spot) if spot is not None else None

        # Overig = totaal huis - bekende appliances
        if house_w is not None:
            known = (heatpump_w or 0) + (quooker_w or 0) + (afwasmachine_w or 0)
            overig_w = max(0, house_w - known)
        else:
            overig_w = None

        last_ts_unix = max(
            (v["ts"] for v in latest.values() if v.get("ts") is not None),
            default=None,
        )
        last_updated = (
            datetime.fromtimestamp(last_ts_unix, tz=timezone.utc)
            if last_ts_unix is not None
            else None
        )

        response = OverviewResponse(
            kpi=Kpi(
                grid_w=grid_w,
                pv_w=pv_w,
                house_w=house_w,
                import_price_eur_per_kwh=import_price,
                outside_temp_c=outside_c,
                last_updated=last_updated,
                heatpump_w=heatpump_w,
                quooker_w=quooker_w,
                afwasmachine_w=afwasmachine_w,
                overig_w=overig_w,
            ),
            trend_6h=trend,
        )
        _overview_cache[cache_key] = response
        _overview_stale[cache_key] = response
        return response
    except Exception:
        stale = _overview_stale.get(cache_key)
        if stale is not None:
            return stale
        raise
