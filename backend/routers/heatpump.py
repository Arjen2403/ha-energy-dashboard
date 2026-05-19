"""/api/heatpump — daily heat pump performance over een gekozen tijdrange.

Caching: 5 min TTL fresh + serve-stale-on-error fallback.
"""
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional
from zoneinfo import ZoneInfo

from cachetools import TTLCache
from fastapi import APIRouter, HTTPException, Query

from ..db import ha_db
from ..views import query_hourly_heatpump

router = APIRouter()

_heatpump_cache: TTLCache = TTLCache(maxsize=16, ttl=300)
_heatpump_stale: dict = {}

NL_TZ = ZoneInfo("Europe/Amsterdam")


class DailyHeatpump:
    pass


from pydantic import BaseModel

class DailyHeatpump(BaseModel):
    date: str
    consumption_total_kwh: float
    supplied_total_kwh: float
    consumption_heating_kwh: float
    consumption_dhw_kwh: float
    supplied_heating_kwh: float
    supplied_dhw_kwh: float
    avg_outside_temp_c: Optional[float] = None
    avg_flow_temp_c: Optional[float] = None
    avg_return_temp_c: Optional[float] = None
    delta_t_c: Optional[float] = None
    uptime_min: Optional[float] = None
    cop_total: Optional[float] = None
    cop_heating: Optional[float] = None
    cop_dhw: Optional[float] = None


class HeatpumpResponse(BaseModel):
    range: str
    from_ts: datetime
    to_ts: datetime
    day_count: int
    days: list[DailyHeatpump]


def _floor_to_hour(ts: int) -> int:
    return (ts // 3600) * 3600


def _resolve_range(range_name: str, from_iso: Optional[str], to_iso: Optional[str]) -> tuple[int, int]:
    now = datetime.now(timezone.utc)

    if range_name == "today":
        nl_now = now.astimezone(NL_TZ)
        nl_midnight = nl_now.replace(hour=0, minute=0, second=0, microsecond=0)
        from_dt = nl_midnight.astimezone(timezone.utc); to_dt = now
    elif range_name == "7d":
        from_dt = now - timedelta(days=7); to_dt = now
    elif range_name == "30d":
        from_dt = now - timedelta(days=30); to_dt = now
    elif range_name == "ytd":
        nl_now = now.astimezone(NL_TZ)
        nl_jan1 = nl_now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        from_dt = nl_jan1.astimezone(timezone.utc); to_dt = now
    elif range_name == "custom":
        if not (from_iso and to_iso):
            raise HTTPException(400, "Custom range requires both 'from' and 'to'.")
        try:
            from_dt = datetime.fromisoformat(from_iso)
            to_dt = datetime.fromisoformat(to_iso)
        except ValueError as e:
            raise HTTPException(400, f"Bad ISO timestamp: {e}")
        if from_dt.tzinfo is None: from_dt = from_dt.replace(tzinfo=timezone.utc)
        if to_dt.tzinfo is None: to_dt = to_dt.replace(tzinfo=timezone.utc)
    else:
        raise HTTPException(400, f"Unknown range '{range_name}'.")

    if to_dt <= from_dt:
        raise HTTPException(400, "'to' must be after 'from'.")

    return _floor_to_hour(int(from_dt.timestamp())), _floor_to_hour(int(to_dt.timestamp()))


@router.get("/heatpump/live")
def get_heatpump_live():
    """Live warmtepomp status: huidige temperaturen, delta-T en of de pomp draait.

    Noot: door de EMS-ESP naming swap is boiler_return_temperature de echte aanvoer
    en boiler_current_flow_temperature de echte retour — identiek aan de views.py fix.
    """
    import time as _time
    ENTITIES = [
        "sensor.boiler_return_temperature",
        "sensor.boiler_current_flow_temperature",
        "sensor.boiler_outside_temperature",
    ]
    with ha_db() as conn:
        placeholders = ",".join(["?"] * len(ENTITIES))
        rows = conn.execute(f"""
            WITH latest AS (
                SELECT s.metadata_id, MAX(s.last_updated_ts) AS ts
                FROM states s
                JOIN states_meta sm ON sm.metadata_id = s.metadata_id
                WHERE sm.entity_id IN ({placeholders})
                GROUP BY s.metadata_id
            )
            SELECT sm.entity_id, s.state
            FROM states s
            JOIN states_meta sm ON sm.metadata_id = s.metadata_id
            JOIN latest l ON l.metadata_id = s.metadata_id AND l.ts = s.last_updated_ts
        """, ENTITIES).fetchall()

        now_ts = int(_time.time())
        energy_rows = conn.execute("""
            SELECT s.state, s.last_updated_ts AS ts
            FROM states s
            JOIN states_meta sm ON sm.metadata_id = s.metadata_id
            WHERE sm.entity_id = 'sensor.boiler_total_energy_consumption'
              AND s.last_updated_ts >= ?
              AND s.state NOT IN ('unknown', 'unavailable', '')
            ORDER BY s.last_updated_ts
        """, (now_ts - 15 * 60,)).fetchall()

    def safe_float(s):
        try:
            return float(s) if s not in (None, "unknown", "unavailable", "") else None
        except Exception:
            return None

    states = {r["entity_id"]: safe_float(r["state"]) for r in rows}
    flow_c    = states.get("sensor.boiler_return_temperature")
    return_c  = states.get("sensor.boiler_current_flow_temperature")
    outside_c = states.get("sensor.boiler_outside_temperature")
    delta_t   = round(flow_c - return_c, 1) if flow_c is not None and return_c is not None else None

    pump_running = False
    if len(energy_rows) >= 2:
        try:
            dt_h = (energy_rows[-1]["ts"] - energy_rows[0]["ts"]) / 3600.0
            delta_kwh = float(energy_rows[-1]["state"]) - float(energy_rows[0]["state"])
            pump_running = dt_h > 0 and delta_kwh > 0
        except Exception:
            pass

    return {
        "flow_temp_c":    flow_c,
        "return_temp_c":  return_c,
        "outside_temp_c": outside_c,
        "delta_t_c":      delta_t,
        "pump_running":   pump_running,
        "delta_t_alert":  pump_running and delta_t is not None and delta_t < 2.0,
    }


@router.get("/heatpump", response_model=HeatpumpResponse)
def get_heatpump(
    range: Literal["today", "7d", "30d", "ytd", "custom"] = Query("7d"),
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = Query(None),
):
    """Daily heat pump aggregation: kWh per categorie + temperaturen + COPs."""
    cache_key = (range, from_, to)
    cached = _heatpump_cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        from_ts, to_ts = _resolve_range(range, from_, to)

        with ha_db() as conn:
            hourly_rows = query_hourly_heatpump(conn, from_ts, to_ts)

        daily: dict[str, dict] = defaultdict(lambda: {
            "consumption_total_kwh": 0.0,
            "supplied_total_kwh": 0.0,
            "consumption_heating_kwh": 0.0,
            "consumption_dhw_kwh": 0.0,
            "supplied_heating_kwh": 0.0,
            "supplied_dhw_kwh": 0.0,
            "uptime_min": 0.0,
            "outside_temps": [],
            "flow_temps": [],
            "return_temps": [],
        })

        for r in hourly_rows:
            hour_dt_utc = datetime.fromtimestamp(r["hour_ts"], tz=timezone.utc)
            day_key = hour_dt_utc.astimezone(NL_TZ).strftime("%Y-%m-%d")
            b = daily[day_key]

            for k in ("consumption_total_kwh", "supplied_total_kwh",
                      "consumption_heating_kwh", "consumption_dhw_kwh",
                      "supplied_heating_kwh", "supplied_dhw_kwh", "uptime_min"):
                v = r.get(k)
                if v is not None:
                    b[k] += v

            if r.get("outside_temp_c") is not None:
                b["outside_temps"].append(r["outside_temp_c"])
            if r.get("flow_temp_c") is not None:
                b["flow_temps"].append(r["flow_temp_c"])
            if r.get("return_temp_c") is not None:
                b["return_temps"].append(r["return_temp_c"])

        def avg(lst):
            return sum(lst) / len(lst) if lst else None

        def safe_div(a, b):
            return a / b if b and b > 0 else None

        days = []
        for date_str in sorted(daily.keys()):
            b = daily[date_str]
            avg_out    = avg(b["outside_temps"])
            avg_flow   = avg(b["flow_temps"])
            avg_return = avg(b["return_temps"])
            delta_t    = avg_flow - avg_return if avg_flow is not None and avg_return is not None else None

            days.append(DailyHeatpump(
                date=date_str,
                consumption_total_kwh=round(b["consumption_total_kwh"], 2),
                supplied_total_kwh=round(b["supplied_total_kwh"], 2),
                consumption_heating_kwh=round(b["consumption_heating_kwh"], 2),
                consumption_dhw_kwh=round(b["consumption_dhw_kwh"], 2),
                supplied_heating_kwh=round(b["supplied_heating_kwh"], 2),
                supplied_dhw_kwh=round(b["supplied_dhw_kwh"], 2),
                uptime_min=round(b["uptime_min"], 1) if b["uptime_min"] else None,
                avg_outside_temp_c=round(avg_out, 1) if avg_out is not None else None,
                avg_flow_temp_c=round(avg_flow, 1) if avg_flow is not None else None,
                avg_return_temp_c=round(avg_return, 1) if avg_return is not None else None,
                delta_t_c=round(delta_t, 1) if delta_t is not None else None,
                cop_total=round(safe_div(b["supplied_total_kwh"], b["consumption_total_kwh"]), 2)
                    if safe_div(b["supplied_total_kwh"], b["consumption_total_kwh"]) else None,
                cop_heating=round(safe_div(b["supplied_heating_kwh"], b["consumption_heating_kwh"]), 2)
                    if safe_div(b["supplied_heating_kwh"], b["consumption_heating_kwh"]) else None,
                cop_dhw=round(safe_div(b["supplied_dhw_kwh"], b["consumption_dhw_kwh"]), 2)
                    if safe_div(b["supplied_dhw_kwh"], b["consumption_dhw_kwh"]) else None,
            ))

        response = HeatpumpResponse(
            range=range,
            from_ts=datetime.fromtimestamp(from_ts, tz=timezone.utc),
            to_ts=datetime.fromtimestamp(to_ts, tz=timezone.utc),
            day_count=len(days),
            days=days,
        )
        _heatpump_cache[cache_key] = response
        _heatpump_stale[cache_key] = response
        return response
    except Exception:
        stale = _heatpump_stale.get(cache_key)
        if stale is not None:
            return stale
        raise
