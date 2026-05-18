"""/api/strategy — what-if cost simulator voor post-2027 keuzes.

Vergelijkt 4 scenario's: A=huidig saldering, B=na 2027 raw, C=+curtailment, D=+batterij.
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
from ..pricing import (
    VASTE_KOSTEN_PER_DAG_NETTO,
    export_price_overschot,
    export_price_within_saldo,
    import_price,
)
from ..views import query_hourly_flows

router = APIRouter()

_strategy_cache: TTLCache = TTLCache(maxsize=16, ttl=300)
_strategy_stale: dict = {}

NL_TZ = ZoneInfo("Europe/Amsterdam")

DEFAULT_BATTERY_KWH = 20.0
BATTERY_ROUND_TRIP_EFF = 0.90
BATTERY_EFF_LEG = BATTERY_ROUND_TRIP_EFF ** 0.5


class ScenarioSummary(BaseModel):
    id: str
    label: str
    total_variable_eur: float
    total_fixed_eur: float
    total_eur: float
    total_import_kwh: float
    total_export_kwh: float
    pv_curtailed_kwh: Optional[float] = None
    battery_max_soc_kwh: Optional[float] = None
    savings_vs_no_saldering_eur: Optional[float] = None


class DailyComparison(BaseModel):
    date: str
    current_eur: float
    no_saldering_eur: float
    curtailment_eur: float
    battery_eur: float


class StrategyResponse(BaseModel):
    range: str
    from_ts: datetime
    to_ts: datetime
    battery_kwh: float
    day_count: int
    scenarios: list[ScenarioSummary]
    daily: list[DailyComparison]
    # Extra velden voor ROI-berekening in de frontend
    battery_savings_per_day_eur: float = 0.0
    battery_overflow_kwh: float = 0.0


def _floor_to_hour(ts: int) -> int:
    return (ts // 3600) * 3600


def _resolve_range(range_name, from_iso, to_iso):
    now = datetime.now(timezone.utc)
    if range_name == "today":
        nl_now = now.astimezone(NL_TZ)
        from_dt = nl_now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
        to_dt = now
    elif range_name == "7d":
        from_dt = now - timedelta(days=7)
        to_dt = now
    elif range_name == "30d":
        from_dt = now - timedelta(days=30)
        to_dt = now
    elif range_name == "ytd":
        nl_now = now.astimezone(NL_TZ)
        from_dt = nl_now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
        to_dt = now
    elif range_name == "custom":
        if not (from_iso and to_iso):
            raise HTTPException(400, "Custom requires from+to")
        from_dt = datetime.fromisoformat(from_iso)
        to_dt = datetime.fromisoformat(to_iso)
        if from_dt.tzinfo is None:
            from_dt = from_dt.replace(tzinfo=timezone.utc)
        if to_dt.tzinfo is None:
            to_dt = to_dt.replace(tzinfo=timezone.utc)
    else:
        raise HTTPException(400, f"Unknown range '{range_name}'.")
    if to_dt <= from_dt:
        raise HTTPException(400, "'to' must be after 'from'")
    return _floor_to_hour(int(from_dt.timestamp())), _floor_to_hour(int(to_dt.timestamp()))


def _day_key(hour_ts: int) -> str:
    return datetime.fromtimestamp(hour_ts, tz=timezone.utc).astimezone(NL_TZ).strftime("%Y-%m-%d")


@router.get("/strategy", response_model=StrategyResponse)
def get_strategy(
    range_: Literal["today", "7d", "30d", "ytd", "custom"] = Query("7d", alias="range"),
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = Query(None),
    battery_kwh: float = Query(DEFAULT_BATTERY_KWH, ge=0, le=200),
):
    """Vergelijk 4 cost-scenario's op historische data."""
    cache_key = (range_, battery_kwh, from_, to)
    cached = _strategy_cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        from_ts, to_ts = _resolve_range(range_, from_, to)

        with ha_db() as conn:
            hourly = query_hourly_flows(conn, from_ts, to_ts)

        daily_current = defaultdict(float)
        daily_no_sald = defaultdict(float)
        daily_curtail = defaultdict(float)
        daily_battery = defaultdict(float)
        daily_dates = set()

        total_imp = 0.0
        total_exp = 0.0
        pv_curtailed = 0.0

        soc = 0.0
        max_soc = 0.0
        battery_imp = 0.0
        battery_exp = 0.0

        for r in hourly:
            spot = r.get("spot_price")
            if spot is None:
                continue
            pv = r.get("pv_kwh") or 0.0
            imp = r.get("import_kwh") or 0.0
            exp = r.get("export_kwh") or 0.0
            if pv == 0 and imp == 0 and exp == 0:
                continue
            house = pv + imp - exp
            d = _day_key(r["hour_ts"])
            daily_dates.add(d)

            p_imp = import_price(spot)
            p_exp_sald = export_price_within_saldo(spot)
            p_exp_oversch = export_price_overschot(spot)

            total_imp += imp
            total_exp += exp

            cost_current = imp * p_imp - exp * p_exp_sald
            daily_current[d] += cost_current

            cost_no_sald = imp * p_imp - exp * p_exp_oversch
            daily_no_sald[d] += cost_no_sald

            cost_with_pv = cost_no_sald
            cost_without_pv = house * p_imp
            if cost_without_pv < cost_with_pv:
                daily_curtail[d] += cost_without_pv
                pv_curtailed += pv
            else:
                daily_curtail[d] += cost_with_pv

            net = house - pv
            if net < 0:
                surplus = -net
                room = battery_kwh - soc
                charge = min(surplus, room)
                soc += charge * BATTERY_EFF_LEG
                b_exp = surplus - charge
                b_imp = 0.0
            else:
                deficit = net
                available = soc * BATTERY_EFF_LEG
                discharge = min(deficit, available)
                soc -= discharge / BATTERY_EFF_LEG
                b_imp = deficit - discharge
                b_exp = 0.0
            max_soc = max(max_soc, soc)
            battery_imp += b_imp
            battery_exp += b_exp
            cost_battery = b_imp * p_imp - b_exp * p_exp_oversch
            daily_battery[d] += cost_battery

        n_days = len(daily_dates)
        fixed_total = n_days * VASTE_KOSTEN_PER_DAG_NETTO

        def make_summary(id_, label, daily_var, **extra):
            var = sum(daily_var.values())
            return ScenarioSummary(
                id=id_,
                label=label,
                total_variable_eur=round(var, 2),
                total_fixed_eur=round(fixed_total, 2),
                total_eur=round(var + fixed_total, 2),
                total_import_kwh=round(extra.get("imp", total_imp), 2),
                total_export_kwh=round(extra.get("exp", total_exp), 2),
                pv_curtailed_kwh=extra.get("curtailed"),
                battery_max_soc_kwh=extra.get("max_soc"),
                savings_vs_no_saldering_eur=None,
            )

        no_sald_total = sum(daily_no_sald.values()) + fixed_total

        scenarios = [
            make_summary("current", "Huidig (saldering 1:1)", daily_current),
            make_summary("no_saldering", "Na 2027 (geen saldering)", daily_no_sald),
            make_summary("curtailment", "Na 2027 + PV-curtailment", daily_curtail,
                         curtailed=round(pv_curtailed, 2)),
            make_summary("battery", f"Na 2027 + {battery_kwh:.0f} kWh batterij", daily_battery,
                         imp=round(battery_imp, 2), exp=round(battery_exp, 2),
                         max_soc=round(max_soc, 2)),
        ]
        for s in scenarios:
            s.savings_vs_no_saldering_eur = round(no_sald_total - s.total_eur, 2)

        daily_list = []
        for d in sorted(daily_dates):
            daily_list.append(DailyComparison(
                date=d,
                current_eur=round(daily_current[d] + VASTE_KOSTEN_PER_DAG_NETTO, 4),
                no_saldering_eur=round(daily_no_sald[d] + VASTE_KOSTEN_PER_DAG_NETTO, 4),
                curtailment_eur=round(daily_curtail[d] + VASTE_KOSTEN_PER_DAG_NETTO, 4),
                battery_eur=round(daily_battery[d] + VASTE_KOSTEN_PER_DAG_NETTO, 4),
            ))

        no_sald_var = sum(daily_no_sald.values())
        battery_var = sum(daily_battery.values())
        battery_savings_per_day = (no_sald_var - battery_var) / max(n_days, 1)

        response = StrategyResponse(
            range=range_,
            from_ts=datetime.fromtimestamp(from_ts, tz=timezone.utc),
            to_ts=datetime.fromtimestamp(to_ts, tz=timezone.utc),
            battery_kwh=battery_kwh,
            day_count=n_days,
            scenarios=scenarios,
            daily=daily_list,
            battery_savings_per_day_eur=round(battery_savings_per_day, 4),
            battery_overflow_kwh=round(battery_exp, 2),
        )
        _strategy_cache[cache_key] = response
        _strategy_stale[cache_key] = response
        return response
    except Exception:
        stale = _strategy_stale.get(cache_key)
        if stale is not None:
            return stale
        raise
