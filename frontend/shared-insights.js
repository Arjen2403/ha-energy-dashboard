/* shared-insights.js — per-tab verdict-logica. Laad NA shared.js.
 * Breidt window.ES uit met overview/flows/solar/costs insights (impr. #1). */
(function () {
  "use strict";
  const ES = window.ES;
  if (!ES) return;
  const fmt = ES.fmt;

  // Grid/fase-inzicht via de groepen-map.
  // measured (optioneel) = { quooker_w (L1), afwasmachine_w (L2), hp_w (3-fase) }
  function gridInsight(phases, imbalancePct, trend, measured) {
    const byLabel = {};
    (phases || []).forEach((p) => { byLabel[p.label] = p.power_w; });
    const base = {
      L1: ES.median((trend || []).map((r) => r.l1_w)),
      L2: ES.median((trend || []).map((r) => r.l2_w)),
      L3: ES.median((trend || []).map((r) => r.l3_w)),
    };
    let heaviest = null, heaviestAbs = -1;
    ["L1", "L2", "L3"].forEach((L) => {
      const v = byLabel[L];
      if (v != null && Math.abs(v) > heaviestAbs) { heaviestAbs = Math.abs(v); heaviest = L; }
    });
    // Gemeten verbruikers per fase: Quooker=L1, Afwasmachine=L2 (HomeWizard
    // sockets), WP 3-fase → ~1/3 per fase. Deze zijn hard, geen schatting.
    const meas = measured || {};
    const perPhase = { L1: [], L2: [], L3: [] };
    if (meas.quooker_w != null) perPhase.L1.push({ name: "Quooker", w: meas.quooker_w });
    if (meas.afwasmachine_w != null) perPhase.L2.push({ name: "Afwasmachine", w: meas.afwasmachine_w });
    if (meas.hp_w != null) { const s = meas.hp_w / 3; ["L1", "L2", "L3"].forEach((L) => perPhase[L].push({ name: "WP (~⅓)", w: s })); }
    const socketOf = { L1: { name: "Quooker", w: meas.quooker_w }, L2: { name: "Afwasmachine", w: meas.afwasmachine_w } };

    const SPIKE_W = 800, attribution = [];
    ["L1", "L2", "L3"].forEach((L) => {
      const now = byLabel[L], b = base[L];
      if (now == null || b == null || now - b <= SPIKE_W) return;
      const delta = now - b;
      const s = socketOf[L];
      if (s && s.w != null && s.w > 300 && Math.abs(s.w - delta) < Math.max(500, delta * 0.4)) {
        attribution.push(`${L}: gemeten — ${s.name} trekt nu ${fmt.w(s.w)} en verklaart de piek.`);
      } else {
        const loads = ES.heavyLoadsOn(L);
        if (loads.length) attribution.push(`${L} +${fmt.w(delta)} boven basislast — waarschijnlijk: ${loads.join(" of ")}.`);
      }
    });
    let tone = "info", title, lines = [];
    if (imbalancePct == null) { title = "Fasegegevens onvolledig"; lines.push("Niet alle drie de fasen leverden een meetwaarde."); }
    else if (imbalancePct < 10) { tone = "good"; title = `Fasen goed gebalanceerd (${imbalancePct.toFixed(0)}% onbalans)`; }
    else if (imbalancePct < 25) { tone = "warn"; title = `Matige onbalans (${imbalancePct.toFixed(0)}%) — ${heaviest || "?"} draagt het meeste`; }
    else { tone = "bad"; title = `Hoge onbalans (${imbalancePct.toFixed(0)}%) — ${heaviest || "?"} zwaar belast`; }
    attribution.forEach((a) => lines.push(a));
    lines.push("Structureel draagt L3 nog het zwaarst: oven (10) + WP-besturing (11). Wasmachine staat nu op L1 (groep 4).");
    const recs = [];
    if (imbalancePct != null && imbalancePct >= 25) {
      const loads = ES.heavyLoadsOn(heaviest || "L3");
      recs.push({ tone: "warn", text: `Draai zware apparaten op ${heaviest} (${loads.join(", ")}) niet gelijktijdig — spreiden verlaagt de onbalans.` });
    }
    recs.push({ tone: "info", text: "Oven (10) en warmtepomp (11) zijn de twee zware lasten op L3 — vermijd de oven tijdens een WP-verwarmingspiek." });
    recs.push({ tone: "info", text: "Draai enkelfase-apparaten bij PV-overschot: lokaal zelfverbruik verlaagt zowel import als cross-fase export." });
    recs.push({ tone: "info", text: "Of onbalans geld kost hangt af van je meterafrekening (per fase vs. som) — nog te verifiëren." });
    return { tone, title, lines, recs, base, heaviest, measured: perPhase };
  }

  // Now: live momentopname.
  function overviewInsight(k) {
    if (!k) return null;
    const pv = k.pv_w || 0, house = k.house_w || 0;
    const grid = k.grid_w == null ? 0 : k.grid_w; // >0 = import
    const price = k.import_price_eur_per_kwh;
    let tone = "info", title, lines = [];
    if (grid < -20) {
      tone = "good";
      title = `Je levert nu ${fmt.w(-grid)} terug aan het net`;
      lines.push(`Zon (${fmt.w(pv)}) dekt je verbruik (${fmt.w(house)}) volledig.`);
    } else if (pv > 50 && pv >= house * 0.95) {
      tone = "good";
      title = "Zon dekt nu (bijna) je hele verbruik";
      lines.push(`PV ${fmt.w(pv)} vs verbruik ${fmt.w(house)}.`);
    } else {
      title = `Je haalt nu ${fmt.w(grid)} van het net`;
      if (pv > 50) lines.push(`Zon levert ${fmt.w(pv)}; de rest komt van het net.`);
    }
    if (price != null) {
      if (price >= 0.35) { if (tone === "good") tone = "warn"; lines.push(`Stroomprijs nu hoog (${fmt.price(price)}) — stel zware apparaten uit.`); }
      else if (price <= 0.08) lines.push(`Stroom nu goedkoop (${fmt.price(price)}) — goed moment voor zware apparaten.`);
      else lines.push(`Importprijs nu ${fmt.price(price)}.`);
    }
    return { tone, title, lines };
  }

  // Flows: energiebalans over een periode (kWh).
  function flowsInsight(t) {
    if (!t) return null;
    const consumption = t.import_ + t.pv - t.export_;
    const selfUse = t.pv > 0 ? (t.pv - t.export_) / t.pv : null;
    const selfSuff = consumption > 0 ? (t.pv - t.export_) / consumption : null;
    const netGrid = t.import_ - t.export_;
    const tone = selfSuff != null && selfSuff >= 0.5 ? "good" : "info";
    const title = selfSuff != null ? `Zon dekte ${(selfSuff * 100).toFixed(0)}% van je verbruik` : "Energiebalans over de periode";
    const lines = [];
    lines.push(`Verbruik ${fmt.kwh(consumption)}, PV ${fmt.kwh(t.pv)}${selfUse != null ? ` (${(selfUse * 100).toFixed(0)}% direct zelf gebruikt)` : ""}.`);
    lines.push(netGrid >= 0 ? `Netto ${fmt.kwh(netGrid)} van het net gehaald.` : `Netto ${fmt.kwh(-netGrid)} teruggeleverd.`);
    if (t.hp && consumption > 0) lines.push(`Warmtepomp ${fmt.kwh(t.hp)} — ${(t.hp / consumption * 100).toFixed(0)}% van je verbruik.`);
    if (t.quooker || t.afwasmachine) lines.push(`Quooker ${fmt.kwh(t.quooker || 0)}, afwasmachine ${fmt.kwh(t.afwasmachine || 0)} (HomeWizard sockets).`);
    return { tone, title, lines };
  }

  // Solar: zelfconsumptie.
  function solarInsight(t) {
    if (!t || !t.pv) return null;
    const ratio = t.ratio != null ? t.ratio : (t.selfConsumed / t.pv);
    const tone = ratio >= 0.4 ? "good" : "warn";
    const title = `${(ratio * 100).toFixed(0)}% van je zon direct zelf verbruikt`;
    const lines = [`Productie ${fmt.kwh(t.pv)}, waarvan ${fmt.kwh(t.exported)} teruggeleverd.`];
    if (ratio < 0.4) lines.push("Na 2027 daalt de exportwaarde — meer eigen verbruik (WP/boiler overdag, batterij) gaat dan lonen.");
    return { tone, title, lines };
  }

  // Costs: netto kosten + grootste post.
  function costsInsight(t, dayCount) {
    if (!t) return null;
    const tone = t.net > 0 ? "bad" : "good";
    const title = `Netto ${fmt.eur(t.net)}${dayCount ? ` over ${dayCount} dagen` : ""}`;
    const lines = [];
    if (dayCount) lines.push(`Gemiddeld ${fmt.eur(t.net / dayCount)}/dag.`);
    lines.push(`Variabele import ${fmt.eur(t.varImport)}, export-credit ${fmt.eur(t.varExport)}, vaste kosten ${fmt.eur(t.fixed)}.`);
    return { tone, title, lines };
  }

  ES.grid = { insight: gridInsight };
  ES.overview = { insight: overviewInsight };
  ES.flows = { insight: flowsInsight };
  ES.solar = { insight: solarInsight };
  ES.costs = { insight: costsInsight };
})();
