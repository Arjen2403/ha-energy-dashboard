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