"""/api/heatpump/status — DHW-actief / verwarming-actief tijdlijn + live status.

Entities (gevonden via scripts/discover_hp_status.py, 2026-07-05):
  - binary_sensor.boiler_heating_active  (on/off)
  - binary_sensor.boiler_dhw_charging    (on/off)
  - sensor.boiler_compressor_activity    (heating / hot water / off) — bonus,
    combineert beide in één "waar is de compressor nu mee bezig"-status.

Deze binaire/enum sensoren staan niet in Long-Term Statistics (alleen
numerieke sensoren met state_class krijgen daar een rij), dus we lezen
rechtstreeks uit `states` en bouwen zelf aaneengesloten aan/uit-segmenten op
uit de losse state-change rijen (query_binary_state_history in views.py).

Caching: zelfde patroon als heatpump.py — 5 min TTL fresh + serve-stale-on-error.
"""
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional
from zoneinfo import ZoneInfo

from cachetools import TTLCache
from fastapi import APIRouter, HTTPException, Query

from ..db import ha_db
from ..views import query_binary_state_history

router = APIRouter()

_status_cache: TTLCache = TTLCache(maxsize=16, ttl=300)
_status_stale: dict = {}
_live_cache: TTLCache = TTLCache(maxsize=1, ttl=30)
_live_stale: dict = {}

NL_TZ = ZoneInfo("Europe/Amsterdam")

HEATING_ENTITY = "binary_sensor.boiler_heating_active"
DHW_ENTITY = "binary_sensor.boiler_dhw_charging"
COMPRESSOR_ACTIVITY_ENTITY = "sensor.boiler_compressor_activity"


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

    return int(from_dt.timestamp()), int(to_dt.timestamp())


def _build_on_segments(rows: list[dict], from_ts: int, to_ts: int) -> list[dict]:
    """Zet losse state-change rijen om in aaneengesloten 'on'-segmenten.

    `rows` is het resultaat van query_binary_state_history: gesorteerd op
    ts, met als eerste rij (indien aanwezig) de laatst bekende state van
    vóór from_ts. Alles behalve state == 'on' (dus ook 'off', 'unavailable',
    'unknown') wordt behandeld als "niet actief".
    """
    if not rows:
        return []

    seed = rows[0] if rows[0]["ts"] < from_ts else None
    rest = rows[1:] if seed else rows

    current_state = seed["state"] if seed else None
    current_start = from_ts
    segments = []

    for r in rest:
        ts = min(max(r["ts"], from_ts), to_ts)
        if r["state"] != current_state:
            if current_state == "on" and ts > current_start:
                segments.append({"start_ts": current_start, "end_ts": ts})
            current_state = r["state"]
            current_start = ts

    if current_state == "on" and to_ts > current_start:
        segments.append({"start_ts": current_start, "end_ts": to_ts})

    return segments


def _split_by_nl_day(start_ts: int, end_ts: int) -> list[tuple[str, float]]:
    """Splits een [start_ts, end_ts) interval op in minuten per NL-kalenderdag."""
    result = []
    cur = datetime.fromtimestamp(start_ts, tz=timezone.utc).astimezone(NL_TZ)
    end_dt = datetime.fromtimestamp(end_ts, tz=timezone.utc).astimezone(NL_TZ)
    while cur < end_dt:
        day_str = cur.strftime("%Y-%m-%d")
        next_midnight = (cur.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1))
        chunk_end = min(next_midnight, end_dt)
        minutes = (chunk_end - cur).total_seconds() / 60.0
        result.append((day_str, minutes))
        cur = chunk_end
    return result


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


@router.get("/heatpump/status/live")
def get_heatpump_status_live():
    """Live status: is de warmtepomp nu aan het verwarmen / tapwater laden,
    en waar is de compressor op dit moment precies mee bezig."""
    cache_key = "live"
    cached = _live_cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        entities = [HEATING_ENTITY, DHW_ENTITY, COMPRESSOR_ACTIVITY_ENTITY]
        with ha_db() as conn:
            placeholders = ",".join(["?"] * len(entities))
            rows = conn.execute(f"""
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
                JOIN latest l ON l.metadata_id = s.metadata_id AND l.ts = s.last_updated_ts
            """, entities).fetchall()

        states = {r["entity_id"]: {"state": r["state"], "ts": r["ts"]} for r in rows}

        def since_iso(entity: str) -> Optional[str]:
            r = states.get(entity)
            return _iso(r["ts"]) if r else None

        heating_state = states.get(HEATING_ENTITY, {}).get("state")
        dhw_state = states.get(DHW_ENTITY, {}).get("state")

        result = {
            "heating_active": heating_state == "on",
            "heating_since": since_iso(HEATING_ENTITY),
            "dhw_charging": dhw_state == "on",
            "dhw_since": since_iso(DHW_ENTITY),
            "compressor_activity": states.get(COMPRESSOR_ACTIVITY_ENTITY, {}).get("state"),
        }
        _live_cache[cache_key] = result
        _live_stale[cache_key] = result
        return result
    except Exception:
        stale = _live_stale.get(cache_key)
        if stale is not None:
            return stale
        raise


@router.get("/heatpump/status")
def get_heatpump_status(
    range: Literal["today", "7d", "30d", "ytd", "custom"] = Query("7d"),
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = Query(None),
):
    """Aan/uit-tijdlijn + dag-aggregaten voor verwarming en tapwater (DHW)."""
    cache_key = (range, from_, to)
    cached = _status_cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        from_ts, to_ts = _resolve_range(range, from_, to)

        with ha_db() as conn:
            heating_rows = query_binary_state_history(conn, HEATING_ENTITY, from_ts, to_ts)
            dhw_rows = query_binary_state_history(conn, DHW_ENTITY, from_ts, to_ts)

        heating_segments = _build_on_segments(heating_rows, from_ts, to_ts)
        dhw_segments = _build_on_segments(dhw_rows, from_ts, to_ts)

        daily: dict[str, dict] = defaultdict(lambda: {
            "heating_on_min": 0.0, "dhw_on_min": 0.0,
            "heating_cycles": 0, "dhw_cycles": 0,
        })

        for seg in heating_segments:
            for day_str, minutes in _split_by_nl_day(seg["start_ts"], seg["end_ts"]):
                daily[day_str]["heating_on_min"] += minutes
            start_day = datetime.fromtimestamp(seg["start_ts"], tz=timezone.utc).astimezone(NL_TZ).strftime("%Y-%m-%d")
            daily[start_day]["heating_cycles"] += 1

        for seg in dhw_segments:
            for day_str, minutes in _split_by_nl_day(seg["start_ts"], seg["end_ts"]):
                daily[day_str]["dhw_on_min"] += minutes
            start_day = datetime.fromtimestamp(seg["start_ts"], tz=timezone.utc).astimezone(NL_TZ).strftime("%Y-%m-%d")
            daily[start_day]["dhw_cycles"] += 1

        daily_list = [
            {
                "date": d,
                "heating_on_min": round(v["heating_on_min"], 1),
                "dhw_on_min": round(v["dhw_on_min"], 1),
                "heating_cycles": v["heating_cycles"],
                "dhw_cycles": v["dhw_cycles"],
            }
            for d, v in sorted(daily.items())
        ]

        response = {
            "range": range,
            "from_ts": _iso(from_ts),
            "to_ts": _iso(to_ts),
            "segments": {
                "heating": [{"start": _iso(s["start_ts"]), "end": _iso(s["end_ts"])} for s in heating_segments],
                "dhw": [{"start": _iso(s["start_ts"]), "end": _iso(s["end_ts"])} for s in dhw_segments],
            },
            "daily": daily_list,
        }
        _status_cache[cache_key] = response
        _status_stale[cache_key] = response
        return response
    except Exception:
        stale = _status_stale.get(cache_key)
        if stale is not None:
            return stale
        raise
