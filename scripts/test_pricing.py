"""Snelle verificatie van pricing.py tegen bekende waardes uit Bijlage A."""
from backend.pricing import (
    import_price,
    export_price_within_saldo,
    export_price_overschot,
    hourly_variable_cost,
    VASTE_KOSTEN_PER_DAG_NETTO,
)

# Sanity check: bij spot = 0 EUR/kWh
spot = 0.0
print(f"Bij spot=€0/kWh:")
print(f"  import_price        = €{import_price(spot):.5f}/kWh  (verwacht ~€0.13656)")
print(f"  export_within_saldo = €{export_price_within_saldo(spot):.5f}/kWh  (idem)")
print(f"  export_overschot    = €{export_price_overschot(spot):.5f}/kWh  (verwacht -€0.01361)")

# Bij spot = 0.10 EUR/kWh (typisch dag-niveau)
spot = 0.10
print(f"\nBij spot=€0.10/kWh:")
print(f"  import_price        = €{import_price(spot):.5f}/kWh")
print(f"  export_within_saldo = €{export_price_within_saldo(spot):.5f}/kWh")
print(f"  export_overschot    = €{export_price_overschot(spot):.5f}/kWh")

# Hourly variable cost: 5 kWh import, 0 kWh export bij spot 0.10
print(f"\nUur 5 kWh import / 0 export @ spot 0.10:")
print(f"  variable cost = €{hourly_variable_cost(5, 0, 0.10):.4f}  (verwacht ~€0.79)")

# Hourly: 0 import, 5 kWh export (saldering)
print(f"\nUur 0 import / 5 kWh export @ spot 0.10:")
print(f"  variable cost = €{hourly_variable_cost(0, 5, 0.10):.4f}  (verwacht ~-€0.79 credit)")

# Vaste kosten per dag (netto)
print(f"\nVaste kosten/dag netto: €{VASTE_KOSTEN_PER_DAG_NETTO:.5f}  (verwacht -€0.18836)")