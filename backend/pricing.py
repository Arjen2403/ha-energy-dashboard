"""Vandebron tarief-formules. Pure functies, geen DB-toegang.

Bron: Bijlage A versie 6 jan 2026 (zie project memory).
Tarieven worden 1 jan en 1 juli aangepast — dan deze constanten updaten.
"""

# --- Constanten (incl/excl BTW zoals genoemd in Bijlage A) ---

INKOOPVERGOEDING_EXCL_BTW = 0.02125          # EUR/kWh, both import en export within saldering
ENERGIEBELASTING_TIER1_INCL_BTW = 0.11085    # EUR/kWh, tier 1 (0-10.000 kWh)
VERKOOPVERGOEDING_EXCL_BTW = 0.01125         # EUR/kWh, alleen voor overschot na saldering
BTW = 1.21

# Vaste kosten per dag (incl. BTW)
VASTE_LEVERINGSKOSTEN_PER_DAG = 0.22998
NETBEHEERKOSTEN_PER_DAG = 1.30365            # Enexis Noord
VERMINDERING_ENERGIEBELASTING_PER_DAG = -1.72199  # negatief = credit

# Netto vaste kosten = -0.18836 EUR/dag (Vandebron betaalt netto aan Arjen)
VASTE_KOSTEN_PER_DAG_NETTO = (
    VASTE_LEVERINGSKOSTEN_PER_DAG
    + NETBEHEERKOSTEN_PER_DAG
    + VERMINDERING_ENERGIEBELASTING_PER_DAG
)


def import_price(spot_eur_per_kwh: float) -> float:
    """Import-prijs per kWh op basis van EPEX spot. Tier 1 EB."""
    return (spot_eur_per_kwh + INKOOPVERGOEDING_EXCL_BTW) * BTW + ENERGIEBELASTING_TIER1_INCL_BTW


def export_price_within_saldo(spot_eur_per_kwh: float) -> float:
    """Export-prijs binnen jaarsaldering — gelijk aan import (1:1 saldering)."""
    return import_price(spot_eur_per_kwh)


def export_price_overschot(spot_eur_per_kwh: float) -> float:
    """Export-prijs voor overschot na jaarsaldering. Geldt vanaf 1-1-2027 verplicht."""
    return (spot_eur_per_kwh - VERKOOPVERGOEDING_EXCL_BTW) * BTW


def hourly_variable_cost(import_kwh: float, export_kwh: float, spot: float) -> float:
    """Netto variabele kosten voor één uur, binnen saldering.

    Negatief = credit (export > import dat uur tegen hetzelfde prijspeil).
    """
    p_import = import_price(spot)
    p_export = export_price_within_saldo(spot)  # = p_import in saldering
    return import_kwh * p_import - export_kwh * p_export