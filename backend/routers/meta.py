"""Meta endpoints — health, debug info."""
from datetime import datetime, timezone

from fastapi import APIRouter

from ..config import settings
from ..db import ha_db

router = APIRouter()


@router.get("/entities")
def entities(prefix: str = "sensor.p1_meter"):
    """Geeft entity_ids terug die beginnen met `prefix` — handig voor discovery."""
    with ha_db() as conn:
        rows = conn.execute(
            "SELECT DISTINCT entity_id FROM states_meta WHERE entity_id LIKE ? ORDER BY entity_id",
            (f"{prefix}%",),
        ).fetchall()
    return {"prefix": prefix, "entities": [r["entity_id"] for r in rows]}


@router.get("/health")
def health():
    """Bevestigt dat de app draait en de DB bereikbaar is."""
    with ha_db() as conn:
        row = conn.execute(
            "SELECT MAX(last_updated_ts) AS ts FROM states"
        ).fetchone()
    last_ts = row["ts"] if row and row["ts"] else None
    last_iso = (
        datetime.fromtimestamp(last_ts, tz=timezone.utc).isoformat()
        if last_ts
        else None
    )
    return {
        "status": "ok",
        "db_path": settings.ha_db_path,
        "last_state_ts": last_iso,
    }