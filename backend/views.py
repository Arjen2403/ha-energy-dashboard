"""SQL views voor het energy dashboard.

De views zijn Python-strings (geen aparte .sql files) — eenvoudiger te
versiebeheren voor een solo project en parametriseren via :name placeholders.
"""

# v_hourly_energy_flows
#
# Levert per uur (start_ts in UTC) de kWh-deltas voor alle dashboard-flows
# plus de gemiddelde spot-prijs. Werking:
#
# 1. spine — recursieve CTE die uur-buckets genereert van :from_ts tot :to_ts.
# 2. deltas — per LTS-entity de delta tussen opeenvolgende uren via LAG over
#    de cumulatieve `sum` kolom. We trekken één extra uur naar achter aan
#    zodat het eerste uur in onze range ook een valide LAG-buur heeft.
# 3. flows — voor elk uur somt de bijbehorende statistic_ids tot één kolom
#    per dashboard-flow (import, export, pv, heatpump, etc).
# 4. prices — uur-gemiddelde van de Nord Pool spot-prijs.
# 5. SELECT — LEFT JOIN spine met flows en prices zodat ook gap-uren een rij
#    krijgen (NULL waardes); pv_kwh wordt naar 0 gecoaleseerd omdat
#    Trannergy-gaps 's nachts altijd 0 productie betekenen.

V_HOURLY_ENERGY_FLOWS = """
WITH RECURSIVE
spine(hour) AS (
    SELECT :from_ts
    UNION ALL
    SELECT hour + 3600 FROM spine WHERE hour + 3600 <= :to_ts
),

deltas AS (
    SELECT
        sm.statistic_id,
        s.start_ts AS hour,
        s.sum - LAG(s.sum) OVER (
            PARTITION BY s.metadata_id ORDER BY s.start_ts
        ) AS delta
    FROM statistics s
    JOIN statistics_meta sm ON sm.id = s.metadata_id
    WHERE sm.statistic_id IN (
        'sensor.p1_meter_energy_import_tariff_1',
        'sensor.p1_meter_energy_import_tariff_2',
        'sensor.p1_meter_energy_export_tariff_1',
        'sensor.p1_meter_energy_export_tariff_2',
        'sensor.trannergy_energy_total',
        'sensor.boiler_total_energy_consumption',
        'sensor.boiler_total_energy_supplied',
        'sensor.socket_quooker_energy_import',
        'sensor.socket_afwasmachine_energy_import'
    )
      AND s.start_ts >= :from_ts - 3600
      AND s.start_ts <= :to_ts
),

flows AS (
    SELECT
        d.hour,
        SUM(CASE WHEN d.statistic_id IN (
            'sensor.p1_meter_energy_import_tariff_1',
            'sensor.p1_meter_energy_import_tariff_2'
        ) THEN d.delta END) AS import_kwh,
        SUM(CASE WHEN d.statistic_id IN (
            'sensor.p1_meter_energy_export_tariff_1',
            'sensor.p1_meter_energy_export_tariff_2'
        ) THEN d.delta END) AS export_kwh,
        SUM(CASE WHEN d.statistic_id = 'sensor.trannergy_energy_total'
            THEN d.delta END) AS pv_kwh,
        SUM(CASE WHEN d.statistic_id = 'sensor.boiler_total_energy_consumption'
            THEN d.delta END) AS heatpump_kwh,
        SUM(CASE WHEN d.statistic_id = 'sensor.boiler_total_energy_supplied'
            THEN d.delta END) AS heatpump_supplied_kwh,
        SUM(CASE WHEN d.statistic_id = 'sensor.socket_quooker_energy_import'
            THEN d.delta END) AS quooker_kwh,
        SUM(CASE WHEN d.statistic_id = 'sensor.socket_afwasmachine_energy_import'
            THEN d.delta END) AS afwasmachine_kwh
    FROM deltas d
    WHERE d.hour >= :from_ts
    GROUP BY d.hour
),

prices AS (
    SELECT s.start_ts AS hour, s.mean AS spot_price
    FROM statistics s
    JOIN statistics_meta sm ON sm.id = s.metadata_id
    WHERE sm.statistic_id = 'sensor.nord_pool_nl_current_price'
      AND s.start_ts >= :from_ts
      AND s.start_ts <= :to_ts
)

SELECT
    spine.hour AS hour_ts,
    f.import_kwh,
    f.export_kwh,
    COALESCE(f.pv_kwh, 0) AS pv_kwh,
    f.heatpump_kwh,
    f.heatpump_supplied_kwh,
    f.quooker_kwh,
    f.afwasmachine_kwh,
    p.spot_price
FROM spine
LEFT JOIN flows f ON f.hour = spine.hour
LEFT JOIN prices p ON p.hour = spine.hour
ORDER BY spine.hour
"""


def query_hourly_flows(conn, from_ts: int, to_ts: int):
    """Voer v_hourly_energy_flows uit en retourneer een lijst dicts.

    from_ts en to_ts zijn unix epoch seconds (UTC) en MOETEN op een hele uur
    geflood zijn (`(ts // 3600) * 3600`). LTS-rijen hebben `start_ts` op
    het hele uur; non-aligned waarden geven NULL terug uit de spine-JOIN.
    """
    rows = conn.execute(
        V_HOURLY_ENERGY_FLOWS, {"from_ts": from_ts, "to_ts": to_ts}
    ).fetchall()
    return [dict(r) for r in rows]

# v_hourly_costs
#
# Per uur (UTC): kWh import, kWh export, en de spot-prijs. Cost-berekening
# zelf gebeurt in Python (pricing.py) — view levert alleen de bouwstenen.
# Reden: BTW/energiebelasting kunnen midyear wijzigen, en we willen één plek
# waar die formules staan.

V_HOURLY_COSTS = """
WITH RECURSIVE
spine(hour) AS (
    SELECT :from_ts
    UNION ALL
    SELECT hour + 3600 FROM spine WHERE hour + 3600 <= :to_ts
),

deltas AS (
    SELECT
        sm.statistic_id,
        s.start_ts AS hour,
        s.sum - LAG(s.sum) OVER (
            PARTITION BY s.metadata_id ORDER BY s.start_ts
        ) AS delta
    FROM statistics s
    JOIN statistics_meta sm ON sm.id = s.metadata_id
    WHERE sm.statistic_id IN (
        'sensor.p1_meter_energy_import_tariff_1',
        'sensor.p1_meter_energy_import_tariff_2',
        'sensor.p1_meter_energy_export_tariff_1',
        'sensor.p1_meter_energy_export_tariff_2'
    )
      AND s.start_ts >= :from_ts - 3600
      AND s.start_ts <= :to_ts
),

flows AS (
    SELECT
        d.hour,
        SUM(CASE WHEN d.statistic_id LIKE '%import%' THEN d.delta END) AS import_kwh,
        SUM(CASE WHEN d.statistic_id LIKE '%export%' THEN d.delta END) AS export_kwh
    FROM deltas d
    WHERE d.hour >= :from_ts
    GROUP BY d.hour
),

prices AS (
    SELECT s.start_ts AS hour, s.mean AS spot_price
    FROM statistics s
    JOIN statistics_meta sm ON sm.id = s.metadata_id
    WHERE sm.statistic_id = 'sensor.nord_pool_nl_current_price'
      AND s.start_ts >= :from_ts
      AND s.start_ts <= :to_ts
)

SELECT
    spine.hour AS hour_ts,
    f.import_kwh,
    f.export_kwh,
    p.spot_price
FROM spine
LEFT JOIN flows f ON f.hour = spine.hour
LEFT JOIN prices p ON p.hour = spine.hour
ORDER BY spine.hour
"""

def query_hourly_costs(conn, from_ts: int, to_ts: int):
    """Voer v_hourly_costs uit en retourneer een lijst dicts.

    from_ts en to_ts zijn unix epoch seconds (UTC), uur-aligned.
    """
    rows = conn.execute(
        V_HOURLY_COSTS, {"from_ts": from_ts, "to_ts": to_ts}
    ).fetchall()
    return [dict(r) for r in rows]

# v_hourly_heatpump
#
# Per uur (UTC): kWh consumption + supplied per categorie (totaal/heating/DHW),
# uptime in minuten, en gemiddelde flow/return/outside temperaturen + compressor power.
# COP-berekening doen we niet hier — pas op dag-aggregatie level (in Python),
# want de Nefit/EMS-ESP rapport in hele kWh-resolutie wat ruisige uur-COP geeft.

V_HOURLY_HEATPUMP = """
WITH RECURSIVE
spine(hour) AS (
    SELECT :from_ts
    UNION ALL
    SELECT hour + 3600 FROM spine WHERE hour + 3600 <= :to_ts
),

deltas AS (
    SELECT
        sm.statistic_id,
        s.start_ts AS hour,
        s.sum - LAG(s.sum) OVER (
            PARTITION BY s.metadata_id ORDER BY s.start_ts
        ) AS delta
    FROM statistics s
    JOIN statistics_meta sm ON sm.id = s.metadata_id
    WHERE sm.statistic_id IN (
        'sensor.boiler_total_energy_consumption',
        'sensor.boiler_total_energy_supplied',
        'sensor.boiler_energy_consumption_compressor_heating',
        'sensor.boiler_dhw_energy_consumption_compressor',
        'sensor.boiler_total_energy_supplied_heating',
        'sensor.boiler_dhw_total_energy_warm_supplied',
        'sensor.boiler_heatpump_total_uptime'
    )
      AND s.start_ts >= :from_ts - 3600
      AND s.start_ts <= :to_ts
),

flows AS (
    SELECT
        d.hour,
        SUM(CASE WHEN d.statistic_id = 'sensor.boiler_total_energy_consumption'
            THEN d.delta END) AS consumption_total_kwh,
        SUM(CASE WHEN d.statistic_id = 'sensor.boiler_total_energy_supplied'
            THEN d.delta END) AS supplied_total_kwh,
        SUM(CASE WHEN d.statistic_id = 'sensor.boiler_energy_consumption_compressor_heating'
            THEN d.delta END) AS consumption_heating_kwh,
        SUM(CASE WHEN d.statistic_id = 'sensor.boiler_dhw_energy_consumption_compressor'
            THEN d.delta END) AS consumption_dhw_kwh,
        SUM(CASE WHEN d.statistic_id = 'sensor.boiler_total_energy_supplied_heating'
            THEN d.delta END) AS supplied_heating_kwh,
        SUM(CASE WHEN d.statistic_id = 'sensor.boiler_dhw_total_energy_warm_supplied'
            THEN d.delta END) AS supplied_dhw_kwh,
        SUM(CASE WHEN d.statistic_id = 'sensor.boiler_heatpump_total_uptime'
            THEN d.delta END) AS uptime_min
    FROM deltas d
    WHERE d.hour >= :from_ts
    GROUP BY d.hour
),

means AS (
    SELECT
        s.start_ts AS hour,
        AVG(CASE WHEN sm.statistic_id = 'sensor.boiler_outside_temperature'
            THEN s.mean END) AS outside_temp_c,
        AVG(CASE WHEN sm.statistic_id = 'sensor.boiler_return_temperature'
            THEN s.mean END) AS flow_temp_c,
        AVG(CASE WHEN sm.statistic_id = 'sensor.boiler_current_flow_temperature'
            THEN s.mean END) AS return_temp_c,
        AVG(CASE WHEN sm.statistic_id = 'sensor.boiler_compressor_power_output'
            THEN s.mean END) AS compressor_kw
    FROM statistics s
    JOIN statistics_meta sm ON sm.id = s.metadata_id
    WHERE sm.statistic_id IN (
        'sensor.boiler_outside_temperature',
        'sensor.boiler_current_flow_temperature',
        'sensor.boiler_return_temperature',
        'sensor.boiler_compressor_power_output'
    )
      AND s.start_ts >= :from_ts
      AND s.start_ts <= :to_ts
    GROUP BY s.start_ts
)

SELECT
    spine.hour AS hour_ts,
    f.consumption_total_kwh,
    f.supplied_total_kwh,
    f.consumption_heating_kwh,
    f.consumption_dhw_kwh,
    f.supplied_heating_kwh,
    f.supplied_dhw_kwh,
    f.uptime_min,
    m.outside_temp_c,
    m.flow_temp_c,
    m.return_temp_c,
    m.compressor_kw
FROM spine
LEFT JOIN flows f ON f.hour = spine.hour
LEFT JOIN means m ON m.hour = spine.hour
ORDER BY spine.hour
"""

def query_hourly_heatpump(conn, from_ts: int, to_ts: int):
    """Voer v_hourly_heatpump uit en retourneer een lijst dicts.

    from_ts en to_ts zijn unix epoch seconds (UTC), uur-aligned.
    """
    rows = conn.execute(
        V_HOURLY_HEATPUMP, {"from_ts": from_ts, "to_ts": to_ts}
    ).fetchall()
    return [dict(r) for r in rows]

# v_hourly_solar
#
# Per uur (UTC): PV-productie kWh + gemiddeld vermogen + inverter-temp +
# AC output voltage. Plus import/export voor self-consumption-berekening.
# Bouwt voort op het patroon van v_hourly_energy_flows maar specifiek voor
# de Solar pagina.

V_HOURLY_SOLAR = """
WITH RECURSIVE
spine(hour) AS (
    SELECT :from_ts
    UNION ALL
    SELECT hour + 3600 FROM spine WHERE hour + 3600 <= :to_ts
),

deltas AS (
    SELECT
        sm.statistic_id,
        s.start_ts AS hour,
        s.sum - LAG(s.sum) OVER (
            PARTITION BY s.metadata_id ORDER BY s.start_ts
        ) AS delta
    FROM statistics s
    JOIN statistics_meta sm ON sm.id = s.metadata_id
    WHERE sm.statistic_id IN (
        'sensor.trannergy_energy_total',
        'sensor.p1_meter_energy_export_tariff_1',
        'sensor.p1_meter_energy_export_tariff_2'
    )
      AND s.start_ts >= :from_ts - 3600
      AND s.start_ts <= :to_ts
),

flows AS (
    SELECT
        d.hour,
        SUM(CASE WHEN d.statistic_id = 'sensor.trannergy_energy_total'
            THEN d.delta END) AS pv_kwh_raw,
        SUM(CASE WHEN d.statistic_id LIKE '%export%' THEN d.delta END) AS export_kwh
    FROM deltas d
    WHERE d.hour >= :from_ts
    GROUP BY d.hour
),

means AS (
    SELECT
        s.start_ts AS hour,
        AVG(CASE WHEN sm.statistic_id = 'sensor.trannergy_actual_power'
            THEN s.mean END) AS pv_w,
        AVG(CASE WHEN sm.statistic_id = 'sensor.trannergy_temperature'
            THEN s.mean END) AS inverter_temp_c,
        AVG(CASE WHEN sm.statistic_id = 'sensor.trannergy_ac_output_voltage_1'
            THEN s.mean END) AS inverter_voltage_v
    FROM statistics s
    JOIN statistics_meta sm ON sm.id = s.metadata_id
    WHERE sm.statistic_id IN (
        'sensor.trannergy_actual_power',
        'sensor.trannergy_temperature',
        'sensor.trannergy_ac_output_voltage_1'
    )
      AND s.start_ts >= :from_ts
      AND s.start_ts <= :to_ts
    GROUP BY s.start_ts
)

SELECT
    spine.hour AS hour_ts,
    COALESCE(f.pv_kwh_raw, 0) AS pv_kwh,
    f.export_kwh,
    m.pv_w,
    m.inverter_temp_c,
    m.inverter_voltage_v
FROM spine
LEFT JOIN flows f ON f.hour = spine.hour
LEFT JOIN means m ON m.hour = spine.hour
ORDER BY spine.hour
"""


def query_hourly_solar(conn, from_ts: int, to_ts: int):
    """Voer v_hourly_solar uit en retourneer een lijst dicts.

    from_ts en to_ts zijn unix epoch seconds (UTC), uur-aligned.
    """
    rows = conn.execute(
        V_HOURLY_SOLAR, {"from_ts": from_ts, "to_ts": to_ts}
    ).fetchall()
    return [dict(r) for r in rows]

# v_binary_state_history
#
# Ruwe state-change historie van één on/off (of enum-achtige) entity binnen
# een tijdvak, plus de laatst bekende state van vóór :from_ts. Dat extra
# "lookback"-record is nodig zodat het eerste segment binnen de range een
# geldig startpunt heeft — zelfde principe als de :from_ts - 3600 lookback
# in v_hourly_heatpump, maar hier op state-change niveau i.p.v. uur-niveau
# omdat binaire sensoren (bv. binary_sensor.boiler_heating_active,
# binary_sensor.boiler_dhw_charging) niet in Long-Term Statistics landen —
# alleen numerieke sensoren met state_class krijgen daar een rij. Voor
# aan/uit-tijdlijnen moeten we dus rechtstreeks uit `states` lezen en zelf
# aaneengesloten segmenten opbouwen (zie routers/hp_status.py).

V_BINARY_STATE_HISTORY = """
SELECT state, ts FROM (
    SELECT s.state, s.last_updated_ts AS ts
    FROM states s
    JOIN states_meta sm ON sm.metadata_id = s.metadata_id
    WHERE sm.entity_id = :entity_id
      AND s.last_updated_ts < :from_ts
    ORDER BY s.last_updated_ts DESC
    LIMIT 1
)
UNION ALL
SELECT state, ts FROM (
    SELECT s.state, s.last_updated_ts AS ts
    FROM states s
    JOIN states_meta sm ON sm.metadata_id = s.metadata_id
    WHERE sm.entity_id = :entity_id
      AND s.last_updated_ts >= :from_ts
      AND s.last_updated_ts <= :to_ts
    ORDER BY s.last_updated_ts
)
ORDER BY ts
"""


def query_binary_state_history(conn, entity_id: str, from_ts: int, to_ts: int):
    """Voer v_binary_state_history uit en retourneer een lijst dicts.

    Retourneert alle state-change rijen in [from_ts, to_ts], plus (als eerste
    rij, indien aanwezig) de laatst bekende state van vóór from_ts. Rijen
    zijn {"state": str, "ts": int (unix epoch seconds, UTC)}.
    """
    rows = conn.execute(
        V_BINARY_STATE_HISTORY,
        {"entity_id": entity_id, "from_ts": from_ts, "to_ts": to_ts},
    ).fetchall()
    return [dict(r) for r in rows]