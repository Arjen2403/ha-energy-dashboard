"""Tests voor backend/pricing.py — Vandebron tarief-formules.

Deze tests valideren de prijs-berekeningen tegen bekende waardes uit Bijlage A
en tegen edge cases (negatieve spot, zero spot, hoog spot).
"""
import pytest

from backend.pricing import (
    BTW,
    ENERGIEBELASTING_TIER1_INCL_BTW,
    INKOOPVERGOEDING_EXCL_BTW,
    VASTE_KOSTEN_PER_DAG_NETTO,
    VERKOOPVERGOEDING_EXCL_BTW,
    export_price_overschot,
    export_price_within_saldo,
    hourly_variable_cost,
    import_price,
)


# --- Constanten check ---


def test_constants_match_bijlage_a():
    """Hard-checks dat de Bijlage A waardes ongewijzigd zijn."""
    assert INKOOPVERGOEDING_EXCL_BTW == 0.02125
    assert ENERGIEBELASTING_TIER1_INCL_BTW == 0.11085
    assert VERKOOPVERGOEDING_EXCL_BTW == 0.01125
    assert BTW == 1.21
    assert VASTE_KOSTEN_PER_DAG_NETTO == pytest.approx(-0.18836, abs=1e-5)


# --- import_price() ---


def test_import_price_at_zero_spot():
    """Bij spot=0: alleen inkoop-vergoeding × BTW + EB."""
    expected = (0 + 0.02125) * 1.21 + 0.11085
    assert import_price(0.0) == pytest.approx(expected)
    assert import_price(0.0) == pytest.approx(0.13656, abs=1e-4)


def test_import_price_at_typical_dag_spot():
    """Bij spot=€0.10 (typisch dag-niveau): ~€0.258/kWh consumer-prijs."""
    p = import_price(0.10)
    assert p == pytest.approx(0.25756, abs=1e-4)
    assert p > 0  # consumer betaalt


def test_import_price_negative_spot_can_go_negative():
    """Negatieve spot kan import-prijs negatief maken (post-2027 issue)."""
    # Bij -€0.40/kWh spot:
    # (-0.40 + 0.02125) × 1.21 + 0.11085 = -0.45828 + 0.11085 = -0.34743
    p = import_price(-0.40)
    assert p < 0
    assert p == pytest.approx(-0.34743, abs=1e-4)


def test_import_price_breakeven_spot():
    """Spot waarbij import_price = 0 is een belangrijk omslagpunt."""
    # 0 = (spot + 0.02125) × 1.21 + 0.11085
    # spot = -0.11085/1.21 - 0.02125 = -0.11283
    # Boven dit punt: consumer betaalt. Onder: consumer krijgt geld.
    assert import_price(-0.11283) == pytest.approx(0.0, abs=1e-4)


# --- export_price_within_saldo() ---


def test_export_within_saldo_equals_import_price():
    """Saldering 1:1 — export-prijs = import-prijs voor elke spot waarde."""
    for spot in [-0.50, -0.10, 0.0, 0.10, 0.30, 1.0]:
        assert export_price_within_saldo(spot) == import_price(spot)


# --- export_price_overschot() ---


def test_export_overschot_lower_than_import():
    """Verkoop-vergoeding maakt overschot-prijs lager dan import-prijs.

    De BTW wordt over (spot - vv) berekend, GEEN energiebelasting eraf.
    Verschil per kWh: import - overschot = (inkoop + vv) × BTW + EB
    """
    for spot in [0.0, 0.10, 0.30]:
        diff = import_price(spot) - export_price_overschot(spot)
        expected_diff = (INKOOPVERGOEDING_EXCL_BTW + VERKOOPVERGOEDING_EXCL_BTW) * BTW + ENERGIEBELASTING_TIER1_INCL_BTW
        assert diff == pytest.approx(expected_diff)


def test_export_overschot_at_zero_spot_is_negative():
    """Bij spot=0: verkoopvergoeding zorgt voor kleine negatieve overschot-prijs."""
    # (0 - 0.01125) × 1.21 = -0.01361
    p = export_price_overschot(0.0)
    assert p < 0
    assert p == pytest.approx(-0.01361, abs=1e-4)


def test_export_overschot_negative_spot_doubly_punished():
    """Bij negatieve spot is overschot-export extra duur (post-2027 + curtailment-aanleiding)."""
    p = export_price_overschot(-0.40)
    # (-0.40 - 0.01125) × 1.21 = -0.498 — exporteur betaalt 49.8 cent per kWh
    assert p == pytest.approx(-0.498, abs=1e-3)
    assert p < import_price(-0.40)  # nog meer pijn dan import


# --- hourly_variable_cost() ---


def test_hourly_variable_cost_pure_import():
    """Alleen import: positieve cost = import_kwh × import_price."""
    cost = hourly_variable_cost(import_kwh=2.0, export_kwh=0.0, spot=0.10)
    assert cost == pytest.approx(2.0 * import_price(0.10))
    assert cost > 0


def test_hourly_variable_cost_pure_export_within_saldo_credit():
    """Alleen export bij positieve spot: negatieve cost (credit) = -export × p_export."""
    cost = hourly_variable_cost(import_kwh=0.0, export_kwh=2.0, spot=0.10)
    assert cost == pytest.approx(-2.0 * import_price(0.10))
    assert cost < 0  # consumer krijgt credit


def test_hourly_variable_cost_balanced_in_out():
    """Even veel import als export: kosten saldering 1:1 = 0."""
    cost = hourly_variable_cost(import_kwh=2.0, export_kwh=2.0, spot=0.10)
    assert cost == pytest.approx(0.0)


def test_hourly_variable_cost_negative_spot_export_costs_money():
    """Negatieve spot + export = consumer betaalt voor het exporteren (saldering bug-feature)."""
    # Spot -0.40, export 5 kWh, geen import
    # cost = 0 - 5 × p_export_within_saldo(-0.40) = -5 × p_import(-0.40) = -5 × -0.347 = +1.74
    cost = hourly_variable_cost(import_kwh=0.0, export_kwh=5.0, spot=-0.40)
    assert cost > 0  # KOST geld bij negatieve spot tijdens export
    assert cost == pytest.approx(5.0 * 0.34743, abs=1e-3)


def test_hourly_variable_cost_zero_when_no_flow():
    """Geen import én geen export = geen variabele kosten."""
    cost = hourly_variable_cost(import_kwh=0.0, export_kwh=0.0, spot=0.20)
    assert cost == 0.0