"""/api/costs — daily cost decomposition over een gekozen tijdrange.

Aggregatie:
- Per uur (UTC): variabele import-kosten + variabele export-credit (binnen saldering).
- Per dag (NL local): som hourly + vaste kosten/dag (constant -0.18836 EUR netto).
- Verzamel naar lijst van DailyCost rows.
"""
from collections import defaultdict
from datetime import datetime, timezone
from typing import Literal, Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ..db import ha_db
from ..pricing import (
    VASTE_KOSTEN_PER_DAG_NETTO,
    export_price_within_saldo,
    import_price,
)
from ..views import query_hourly_costs

router = APIRouter()

NL_TZ = ZoneInfo("Europe/Amsterdam")


class DailyCost(BaseModel):
    date: str  # ISO date string YYYY-MM-DD in NL local time
    import_kwh: float
    export_kwh: float
    variable_import_eur: float       # positief = we betalen
    variable_export_eur: float       # negatief = credit binnen saldering
    fixed_eur: float                 # constant -0.18836
    net_eur: float                   # som van bovenstaande
    avg_import_price_eur_per_kwh: Optional[float] = None
    avg_export_price_eur_per_kwh: Optional[float] = None


class CostsResponse(BaseModel):
    range: str
    from_ts: datetime
    to_ts: datetime
    day_count: int
    days: list[DailyCost]
    total_net_eur: float


def _floor_to_hour(ts: int) -> int:
    return (ts // 3600) * 3600


def _resolve_range(range_name: str, from_iso: Optional[str], to_iso: Optional[str]) -> tuple[int, int]:
    """Zelfde range-logica als /api/flows (today/7d/30d/ytd/custom)."""
    from datetime import timedelta
    now = datetime.now(timezone.utc)

    if range_name == "today":
        nl_now = now.astimezone(NL_TZ)
        nl_midnight = nl_now.replace(hour=0, minute=0, second=0, microsecond=0)
        from_dt = nl_midnight.astimezone(timezone.utc)
        to_dt = now
    elif range_name == "7d":
        from_dt = now - timedelta(days=7)
        to_dt = now
    elif range_name == "30d":
        from_dt = now - timedelta(days=30)
        to_dt = now
    elif range_name == "ytd":
        nl_now = now.astimezone(NL_TZ)
        nl_jan1 = nl_now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        from_dt = nl_jan1.astimezone(timezone.utc)
        to_dt = now
    elif range_name == "custom":
        if not (from_iso and to_iso):
            raise HTTPException(400, "Custom range requires both 'from' and 'to' (ISO 8601).")
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


@router.get("/costs", response_model=CostsResponse)
def get_costs(
    range: Literal["today", "7d", "30d", "ytd", "custom"] = Query("7d"),
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = Query(None),
):
    """Daily cost decomposition over de gekozen range."""
    from_ts, to_ts = _resolve_range(range, from_, to)

    with ha_db() as conn:
        hourly_rows = query_hourly_costs(conn, from_ts, to_ts)

    # Bucket hourly rows naar NL-lokale dagen
    daily_buckets: dict[str, dict] = defaultdict(lambda: {
        "import_kwh": 0.0,
        "export_kwh": 0.0,
        "variable_import_eur": 0.0,
        "variable_export_eur": 0.0,
        "weighted_import_value": 0.0,  # voor avg price calculation
        "weighted_export_value": 0.0,
    })

    for r in hourly_rows:
        spot = r["spot_price"]
        if spot is None:
            # Geen prijs = kunnen geen kosten berekenen voor dit uur. Skip.
            continue
        i_kwh = r["import_kwh"] or 0.0
        e_kwh = r["export_kwh"] or 0.0
        if i_kwh == 0 and e_kwh == 0:
            continue

        p_import = import_price(spot)
        p_export = export_price_within_saldo(spot)
        var_import = i_kwh * p_import
        var_export = -e_kwh * p_export   # negatief = credit

        # Bucket date in NL local time
        hour_dt_utc = datetime.fromtimestamp(r["hour_ts"], tz=timezone.utc)
        hour_dt_nl = hour_dt_utc.astimezone(NL_TZ)
        day_key = hour_dt_nl.strftime("%Y-%m-%d")

        b = daily_buckets[day_key]
        b["import_kwh"] += i_kwh
        b["export_kwh"] += e_kwh
        b["variable_import_eur"] += var_import
        b["variable_export_eur"] += var_export
        b["weighted_import_value"] += i_kwh * p_import
        b["weighted_export_value"] += e_kwh * p_export

    days = []
    for date_str in sorted(daily_buckets.keys()):
        b = daily_buckets[date_str]
        net = b["variable_import_eur"] + b["variable_export_eur"] + VASTE_KOSTEN_PER_DAG_NETTO
        avg_import = (
            b["weighted_import_value"] / b["import_kwh"] if b["import_kwh"] > 0 else None
        )
        avg_export = (
            b["weighted_export_value"] / b["export_kwh"] if b["export_kwh"] > 0 else None
        )
        days.append(DailyCost(
            date=date_str,
            import_kwh=round(b["import_kwh"], 3),
            export_kwh=round(b["export_kwh"], 3),
            variable_import_eur=round(b["variable_import_eur"], 4),
            variable_export_eur=round(b["variable_export_eur"], 4),
            fixed_eur=round(VASTE_KOSTEN_PER_DAG_NETTO, 4),
            net_eur=round(net, 4),
            avg_import_price_eur_per_kwh=round(avg_import, 5) if avg_import else None,
            avg_export_price_eur_per_kwh=round(avg_export, 5) if avg_export else None,
        ))

    return CostsResponse(
        range=range,
        from_ts=datetime.fromtimestamp(from_ts, tz=timezone.utc),
        to_ts=datetime.fromtimestamp(to_ts, tz=timezone.utc),
        day_count=len(days),
        days=days,
        total_net_eur=round(sum(d.net_eur for d in days), 2),
    )