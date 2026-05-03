"""/api/solar — daily PV stats + day-curve + inverter temp.

Caching: 5 min TTL fresh + serve-stale-on-error fallback.
"""
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional
from zoneinfo import ZoneInfo

from cachetools import TTLCache
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ..db import ha_db
from ..views import query_hourly_solar

router = APIRouter()

_solar_cache: TTLCache = TTLCache(maxsize=16, ttl=300)
_solar_stale: dict = {}

NL_TZ = ZoneInfo("Europe/Amsterdam")


class DailySolar(BaseModel):
    date: str
    pv_kwh: float
    peak_power_w: Optional[float] = None
    avg_inverter_temp_c: Optional[float] = None
    self_consumed_kwh: float
    exported_kwh: float
    self_consumption_ratio: Optional[float] = None


class DayCurvePoint(BaseModel):
    hour: int
    avg_pv_w: Optional[float] = None
    sample_count: int


class SolarResponse(BaseModel):
    range: str
    from_ts: datetime
    to_ts: datetime
    day_count: int
    days: list[DailySolar]
    day_curve: list[DayCurvePoint]


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


@router.get("/solar", response_model=SolarResponse)
def get_solar(
    range_: Literal["today", "7d", "30d", "ytd", "custom"] = Query("7d", alias="range"),
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = Query(None),
):
    """Daily PV + day-curve."""
    cache_key = (range_, from_, to)
    cached = _solar_cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        from_ts, to_ts = _resolve_range(range_, from_, to)

        with ha_db() as conn:
            hourly_rows = query_hourly_solar(conn, from_ts, to_ts)

        daily: dict[str, dict] = defaultdict(lambda: {
            "pv_kwh": 0.0,
            "export_kwh": 0.0,
            "peak_pv_w": None,
            "inverter_temps": [],
        })

        hour_buckets: dict[int, list[float]] = defaultdict(list)

        for r in hourly_rows:
            hour_dt_utc = datetime.fromtimestamp(r["hour_ts"], tz=timezone.utc)
            hour_dt_nl = hour_dt_utc.astimezone(NL_TZ)
            day_key = hour_dt_nl.strftime("%Y-%m-%d")
            hour_of_day = hour_dt_nl.hour

            b = daily[day_key]
            if r.get("pv_kwh") is not None:
                b["pv_kwh"] += r["pv_kwh"]
            if r.get("export_kwh") is not None:
                b["export_kwh"] += r["export_kwh"]
            if r.get("pv_w") is not None:
                if b["peak_pv_w"] is None or r["pv_w"] > b["peak_pv_w"]:
                    b["peak_pv_w"] = r["pv_w"]
                hour_buckets[hour_of_day].append(r["pv_w"])
            if r.get("inverter_temp_c") is not None:
                b["inverter_temps"].append(r["inverter_temp_c"])

        days = []
        for date_str in sorted(daily.keys()):
            b = daily[date_str]
            pv = b["pv_kwh"]
            export = b["export_kwh"]
            self_consumed = max(0.0, pv - export)
            ratio = (self_consumed / pv) if pv > 0 else None
            avg_temp = (
                sum(b["inverter_temps"]) / len(b["inverter_temps"])
                if b["inverter_temps"] else None
            )

            days.append(DailySolar(
                date=date_str,
                pv_kwh=round(pv, 2),
                peak_power_w=round(b["peak_pv_w"], 0) if b["peak_pv_w"] is not None else None,
                avg_inverter_temp_c=round(avg_temp, 1) if avg_temp is not None else None,
                self_consumed_kwh=round(self_consumed, 2),
                exported_kwh=round(export, 2),
                self_consumption_ratio=round(ratio, 3) if ratio is not None else None,
            ))

        day_curve = []
        for hour in range(24):
            samples = hour_buckets.get(hour, [])
            avg = sum(samples) / len(samples) if samples else None
            day_curve.append(DayCurvePoint(
                hour=hour,
                avg_pv_w=round(avg, 0) if avg is not None else None,
                sample_count=len(samples),
            ))

        response = SolarResponse(
            range=range_,
            from_ts=datetime.fromtimestamp(from_ts, tz=timezone.utc),
            to_ts=datetime.fromtimestamp(to_ts, tz=timezone.utc),
            day_count=len(days),
            days=days,
            day_curve=day_curve,
        )
        _solar_cache[cache_key] = response
        _solar_stale[cache_key] = response
        return response
    except Exception:
        stale = _solar_stale.get(cache_key)
        if stale is not None:
            return stale
        raise