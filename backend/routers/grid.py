"""/api/grid — live fase-vermogen + 6h trend voor de Grid/Fases pagina.

Fase-entiteiten worden dynamisch ontdekt via states_meta zodat de code
werkt ongeacht de exacte HomeWizard P1 entiteitnaam-versie.
Ondersteunt twee naamschema's:
  - Nieuw: sensor.*_power_phase_1/2/3  en  sensor.*_voltage_phase_1/2/3
  - Oud:   sensor.*active_power_l1/l2/l3  en  sensor.*active_voltage_l1/l2/l3
"""
from datetime import datetime, timezone
from typing import Optional

from cachetools import TTLCache
from fastapi import APIRouter

from ..db import ha_db

router = APIRouter()

_grid_live_cache: TTLCache  = TTLCache(maxsize=1, ttl=30)
_grid_trend_cache: TTLCache = TTLCache(maxsize=1, ttl=60)
_grid_live_stale: dict  = {}
_grid_trend_stale: dict = {}

# Twee naamschema's per fase: nieuw (power_phase_N) en oud (active_power_lN).
# Elk element is een lijst van patronen die op volgorde geprobeerd worden.
_POWER_PATTERN_SETS = [
    ["%_power_phase_1", "%active_power_l1%"],
    ["%_power_phase_2", "%active_power_l2%"],
    ["%_power_phase_3", "%active_power_l3%"],
]
_VOLTAGE_PATTERN_SETS = [
    ["%meter_voltage_phase_1", "%active_voltage_l1%"],
    ["%meter_voltage_phase_2", "%active_voltage_l2%"],
    ["%meter_voltage_phase_3", "%active_voltage_l3%"],
]

_PHASE_LABELS = ["L1", "L2", "L3"]


def _discover_entities(conn, pattern_sets: list[list[str]]) -> list[Optional[str]]:
    """Zoek per fase één entiteitnaam op via meerdere fallback-patronen."""
    result = []
    for patterns in pattern_sets:
        found = None
        for pat in patterns:
            row = conn.execute(
                "SELECT entity_id FROM states_meta WHERE entity_id LIKE ? "
                "ORDER BY entity_id LIMIT 1",
                (pat,),
            ).fetchone()
            if row:
                found = row["entity_id"]
                break
        result.append(found)
    return result


def _query_latest(conn, entity_ids: list[str]) -> dict:
    ids = [e for e in entity_ids if e]
    if not ids:
        return {}
    placeholders = ",".join(["?"] * len(ids))
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
    """, ids).fetchall()
    return {r["entity_id"]: r["state"] for r in rows}


def _safe_float(s) -> Optional[float]:
    try:
        return float(s) if s not in (None, "unknown", "unavailable", "") else None
    except (TypeError, ValueError):
        return None


def _query_6h_trend(conn, power_entities: list[Optional[str]]) -> list[dict]:
    ids = [e for e in power_entities if e]
    if not ids:
        return []
    now_ts = int(datetime.now(timezone.utc).timestamp())
    from_ts = now_ts - 6 * 3600
    placeholders = ",".join(["?"] * len(ids))
    rows = conn.execute(f"""
        SELECT
            sm.entity_id,
            CAST(s.last_updated_ts / 300 AS INTEGER) * 300 AS bucket_ts,
            AVG(CAST(s.state AS REAL)) AS avg_w
        FROM states s
        JOIN states_meta sm ON sm.metadata_id = s.metadata_id
        WHERE sm.entity_id IN ({placeholders})
          AND s.last_updated_ts >= ?
          AND s.state NOT IN ('unknown', 'unavailable', '')
        GROUP BY sm.entity_id, bucket_ts
        ORDER BY bucket_ts
    """, ids + [from_ts]).fetchall()

    # Bucket → {entity_id: w}
    by_bucket: dict[int, dict] = {}
    for r in rows:
        b = r["bucket_ts"]
        by_bucket.setdefault(b, {})[r["entity_id"]] = r["avg_w"]

    trend = []
    for ts in sorted(by_bucket.keys()):
        bucket = by_bucket[ts]
        trend.append({
            "ts": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
            "l1_w": bucket.get(power_entities[0]),
            "l2_w": bucket.get(power_entities[1]) if len(power_entities) > 1 else None,
            "l3_w": bucket.get(power_entities[2]) if len(power_entities) > 2 else None,
        })
    return trend


@router.get("/grid/live")
def get_grid_live():
    """Live fase-vermogens (W), spanning (V) en onbalans-indicator."""
    cache_key = "live"
    cached = _grid_live_cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        with ha_db() as conn:
            power_ids   = _discover_entities(conn, _POWER_PATTERN_SETS)
            voltage_ids = _discover_entities(conn, _VOLTAGE_PATTERN_SETS)
            all_ids = [e for e in power_ids + voltage_ids if e]
            states = _query_latest(conn, all_ids) if all_ids else {}

        phases = []
        powers = []
        for i, (pid, vid) in enumerate(zip(power_ids, voltage_ids)):
            w = _safe_float(states.get(pid)) if pid else None
            v = _safe_float(states.get(vid)) if vid else None
            phases.append({"label": _PHASE_LABELS[i], "power_w": w, "voltage_v": v})
            if w is not None:
                powers.append(w)

        # Onbalans = spread als % van hoogste absolute waarde (alle fasen beschikbaar)
        imbalance_pct = None
        if len(powers) == 3:
            spread = max(powers) - min(powers)
            denom = max(abs(p) for p in powers)
            imbalance_pct = round(spread / denom * 100, 1) if denom > 0 else 0.0

        result = {
            "phases": phases,
            "imbalance_pct": imbalance_pct,
            "total_w": round(sum(p for p in powers if p is not None), 1) if powers else None,
            "entities_found": [e for e in power_ids if e],
        }
        _grid_live_cache[cache_key] = result
        _grid_live_stale[cache_key] = result
        return result
    except Exception:
        stale = _grid_live_stale.get(cache_key)
        if stale is not None:
            return stale
        raise


@router.get("/grid/trend")
def get_grid_trend():
    """6-uurs trend per fase (5-min gemiddelden), voor de trendgrafiek."""
    cache_key = "trend"
    cached = _grid_trend_cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        with ha_db() as conn:
            power_ids = _discover_entities(conn, _POWER_PATTERN_SETS)
            trend = _query_6h_trend(conn, power_ids)

        result = {"trend": trend, "entities": power_ids}
        _grid_trend_cache[cache_key] = result
        _grid_trend_stale[cache_key] = result
        return result
    except Exception:
        stale = _grid_trend_stale.get(cache_key)
        if stale is not None:
            return stale
        raise
