"""/api/flows — uurlijkse energy flows over een gekozen tijdrange.

Caching: 5 min TTL fresh + serve-stale-on-error fallback.
"""
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional
from zoneinfo import ZoneInfo

from cachetools import TTLCache
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ..db import ha_db
from ..views import query_hourly_flows

router = APIRouter()

_flows_cache: TTLCache = TTLCache(maxsize=16, ttl=300)
_flows_stale: dict = {}

NL_TZ = ZoneInfo("Europe/Amsterdam")


class FlowRow(BaseModel):
    hour: datetime
    import_kwh: Optional[float] = None
    export_kwh: Optional[float] = None
    pv_kwh: Optional[float] = None
    heatpump_kwh: Optional[float] = None
    heatpump_supplied_kwh: Optional[float] = None
    quooker_kwh: Optional[float] = None
    afwasmachine_kwh: Optional[float] = None
    spot_price: Optional[float] = None


class FlowsResponse(BaseModel):
    range: str
    from_ts: datetime
    to_ts: datetime
    row_count: int
    rows: list[FlowRow]


def _floor_to_hour(ts: int) -> int:
    return (ts // 3600) * 3600


def _resolve_range(
    range_name: str, from_iso: Optional[str], to_iso: Optional[str]
) -> tuple[int, int]:
    now = datetime.now(timezone.utc)

    if range_name == "today":
        nl_now = now.astimezone(NL_TZ)
        nl_midnight = nl_now.replace(hour=0, minute=0, second=0, microsecond=0)
        from_dt = nl_midnight.astimezone(timezone.utc)
        to_dt = now
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
            raise HTTPException(status_code=400, detail="Custom range requires both 'from' and 'to' (ISO 8601).")
        try:
            from_dt = datetime.fromisoformat(from_iso)
            to_dt = datetime.fromisoformat(to_iso)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"Bad ISO timestamp: {e}")
        if from_dt.tzinfo is None: from_dt = from_dt.replace(tzinfo=timezone.utc)
        if to_dt.tzinfo is None: to_dt = to_dt.replace(tzinfo=timezone.utc)
    else:
        raise HTTPException(status_code=400, detail=f"Unknown range '{range_name}'.")

    if to_dt <= from_dt:
        raise HTTPException(status_code=400, detail="'to' must be after 'from'.")

    return _floor_to_hour(int(from_dt.timestamp())), _floor_to_hour(int(to_dt.timestamp()))


@router.get("/flows", response_model=FlowsResponse)
def get_flows(
    range: Literal["today", "7d", "30d", "ytd", "custom"] = Query("7d"),
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = Query(None),
):
    """Hourly energy flows: kWh import/export/pv/heatpump/sockets + spot price."""
    cache_key = (range, from_, to)
    cached = _flows_cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        from_ts, to_ts = _resolve_range(range, from_, to)

        with ha_db() as conn:
            rows = query_hourly_flows(conn, from_ts, to_ts)

        flow_rows = [
            FlowRow(
                hour=datetime.fromtimestamp(r["hour_ts"], tz=timezone.utc),
                import_kwh=r["import_kwh"],
                export_kwh=r["export_kwh"],
                pv_kwh=r["pv_kwh"],
                heatpump_kwh=r["heatpump_kwh"],
                heatpump_supplied_kwh=r["heatpump_supplied_kwh"],
                quooker_kwh=r["quooker_kwh"],
                afwasmachine_kwh=r["afwasmachine_kwh"],
                spot_price=r["spot_price"],
            )
            for r in rows
        ]

        response = FlowsResponse(
            range=range,
            from_ts=datetime.fromtimestamp(from_ts, tz=timezone.utc),
            to_ts=datetime.fromtimestamp(to_ts, tz=timezone.utc),
            row_count=len(flow_rows),
            rows=flow_rows,
        )
        _flows_cache[cache_key] = response
        _flows_stale[cache_key] = response
        return response
    except Exception:
        stale = _flows_stale.get(cache_key)
        if stale is not None:
            return stale
        raise