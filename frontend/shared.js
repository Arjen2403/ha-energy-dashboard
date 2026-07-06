/* =====================================================================
 * shared.js — gedeelde bouwstenen voor alle dashboard-tabs
 * ---------------------------------------------------------------------
 * Bevat:
 *   ES.palette      semantische kleuren (import/export/pv/kosten)
 *   ES.phase        aparte fase-kleuren (L1/L2/L3), losstaand van semantiek
 *   ES.groups       statische groepen->fase->apparaat map (uit de meterkast)
 *   ES.fmt.*        formatters (W, kW, EUR, prijs, %, delta)
 *   ES.verdictBox() HTML-string voor een "wat betekent dit"-kader (impr. #1)
 *   ES.deltaBadge() HTML-string voor vergelijking t.o.v. baseline (impr. #2)
 *   ES.recList()    HTML-string voor een aanbevelingen-feed (impr. #5)
 *   ES.grid.insight() fase-analyse + apparaat-attributie via de groepen-map
 *
 * Geen afhankelijkheden. Laad met <script src="/shared.js"></script> vóór
 * het pagina-script. Alles hangt onder window.ES.
 * ===================================================================== */
(function () {
  "use strict";

  /* ---- Palet: semantiek (impr. #4) --------------------------------- */
  // Deze kleuren betekenen overal hetzelfde. Fase-kleuren staan los zodat
  // "groen" nooit dubbelzinnig is (export vs. L3).
  const palette = {
    import:  "#3b82f6", // blauw   — stroom van het net
    export:  "#22c55e", // groen   — teruglevering
    pv:      "#facc15", // geel    — zonproductie
    cost:    "#dc2626", // rood    — kosten / ongewenst
    good:    "#16a34a",
    warn:    "#f59e0b",
    bad:     "#dc2626",
    neutral: "#6b7280",
    house:   "#6b7280",
  };

  /* ---- Palet: fasen (bewust NIET blauw/groen) ---------------------- */
  const phase = { L1: "#7c3aed" /*violet*/, L2: "#f97316" /*oranje*/, L3: "#0891b2" /*cyaan*/ };

  /* ---- Groepen -> fase -> apparaat (meterkast, PDF 2026-07-06) ------ */
  // Regel: groep 1-4 = L1, 5-8 = L2, 9-12 = L3. `heavy` = grote enkelfase-last.
  const groups = [
    { g: 1,  phase: "L1", label: "Woon-/eetkamer + verlichting",              devices: ["WCD woon-/eetkamer", "verlichting", "overdekt terras"], heavy: false },
    { g: 2,  phase: "L1", label: "Keuken + bijkeuken",                         devices: ["WCD keuken", "bijkeuken", "verlichting"],               heavy: false },
    { g: 3,  phase: "L1", label: "Magnetron + Quooker",                        devices: ["Magnetron", "Quooker"],                                  heavy: false },
    { g: 4,  phase: "L1", label: "Wasdroger",                                  devices: ["Wasdroger"],                                             heavy: true  },
    { g: 5,  phase: "L2", label: "Vaatwasser",                                 devices: ["Vaatwasser"],                                            heavy: true  },
    { g: 6,  phase: "L2", label: "Slaapkamer 2/3 + vliering (ventilatie)",     devices: ["Slaapkamer 2/3", "Vliering", "Ventilatie-unit (MVHR)"],  heavy: false },
    { g: 7,  phase: "L2", label: "Master slaapkamer / badkamer / kleedruimte", devices: ["Slaapkamer 1", "Badkamer 1", "Kleedruimte"],             heavy: false },
    { g: 8,  phase: "L2", label: "Studeerkamer + overloop",                    devices: ["Studeerkamer", "Overloop", "Trapopgang"],                heavy: false },
    { g: 9,  phase: "L3", label: "Tuin / meterkast / carport / schuur",        devices: ["Tuin", "Meterkast", "Carport", "Schuur"],                heavy: false },
    { g: 10, phase: "L3", label: "Oven",                                       devices: ["Oven"],                                                  heavy: true  },
    { g: 11, phase: "L3", label: "WP-buitenunit + techniek + Niko",            devices: ["Warmtepomp buitenunit", "Technische ruimte", "Dakramen", "Niko main"], heavy: true },
    { g: 12, phase: "L3", label: "Wasmachine",                                 devices: ["Wasmachine"],                                            heavy: true  },
  ];

  // Meerfasige directe voedingen buiten de 12 groepen om.
  const multiPhase = [
    { label: "PV-omvormer",   phases: ["L1", "L2", "L3"], note: "verdeelt productie gelijk over 3 fasen" },
    { label: "Kookplaat (inductie)", phases: ["L1", "L2"], note: "2-fase aansluiting" },
    { label: "Warmtepomp",    phases: ["L1", "L2", "L3"], note: "3-fase; besturing op groep 11 (L3)" },
  ];

  function heavyLoadsOn(phaseLabel) {
    return groups.filter((r) => r.phase === phaseLabel && r.heavy).map((r) => r.label);
  }

  /* ---- Formatters -------------------------------------------------- */
  const fmt = {
    w(v) {
      if (v == null) return "–";
      const a = Math.abs(v);
      return a >= 1000 ? (v / 1000).toFixed(2) + " kW" : Math.round(v) + " W";
    },
    kwh(v, d = 1) { return v == null ? "–" : v.toFixed(d) + " kWh"; },
    eur(v, d = 2) {
      if (v == null) return "–";
      return (v < 0 ? "−€ " : "€ ") + Math.abs(v).toFixed(d);
    },
    price(v) { return v == null ? "–" : "€ " + v.toFixed(3) + "/kWh"; }, // 3 dec. leesbaar (impr. #4)
    pct(v, d = 1) { return v == null ? "–" : v.toFixed(d) + " %"; },
    temp(v) { return v == null ? "–" : v.toFixed(1) + " °C"; },
  };

  /* ---- Baseline / delta -------------------------------------------- */
  function median(arr) {
    const xs = arr.filter((x) => x != null && !Number.isNaN(x)).sort((a, b) => a - b);
    if (!xs.length) return null;
    const m = Math.floor(xs.length / 2);
    return xs.length % 2 ? xs[m] : (xs[m - 1] + xs[m]) / 2;
  }

  // Vergelijk een waarde met een baseline; geeft {pct, dir, tone}.
  // higherIsWorse: true als hoger "slechter" is (bv. kosten, verbruik).
  function compare(value, baseline, higherIsWorse = true) {
    if (value == null || baseline == null || baseline === 0) return null;
    const pct = ((value - baseline) / Math.abs(baseline)) * 100;
    const dir = pct >= 0 ? "up" : "down";
    const worse = higherIsWorse ? pct > 0 : pct < 0;
    const tone = Math.abs(pct) < 3 ? "flat" : worse ? "bad" : "good";
    return { pct, dir, tone };
  }

  /* ---- HTML-bouwstenen (Tailwind-klassen) -------------------------- */
  const TONE = {
    good: { bar: "border-green-500", bg: "bg-green-50",  txt: "text-green-900",  tag: "text-green-700" },
    warn: { bar: "border-amber-500", bg: "bg-amber-50",  txt: "text-amber-900",  tag: "text-amber-700" },
    bad:  { bar: "border-red-500",   bg: "bg-red-50",    txt: "text-red-900",    tag: "text-red-700"   },
    info: { bar: "border-blue-500",  bg: "bg-blue-50",   txt: "text-blue-900",   tag: "text-blue-700"  },
  };

  function esc(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  // Verdict-kader (impr. #1): antwoordt op "wat betekent dit / wat nu?"
  // opts: { tone, title, lines:[..], metric:{value,label} }
  function verdictBox(opts) {
    const t = TONE[opts.tone] || TONE.info;
    const lines = (opts.lines || []).map((l) => `<li>${esc(l)}</li>`).join("");
    const metric = opts.metric
      ? `<div class="text-3xl font-black ${t.txt} mt-1">${esc(opts.metric.value)}</div>
         <div class="text-xs uppercase tracking-wide ${t.tag}">${esc(opts.metric.label)}</div>`
      : "";
    return `
      <div class="border-l-4 ${t.bar} ${t.bg} rounded p-4 mb-6">
        <div class="text-xs uppercase tracking-wide font-semibold ${t.tag}">Wat dit betekent</div>
        <div class="text-lg font-bold ${t.txt} mt-0.5">${esc(opts.title)}</div>
        ${metric}
        ${lines ? `<ul class="list-disc list-inside text-sm ${t.txt} mt-2 space-y-0.5">${lines}</ul>` : ""}
      </div>`;
  }

  // Delta-badge (impr. #2): "▲ 18% vs vorige week"
  function deltaBadge(cmp, suffix = "") {
    if (!cmp) return "";
    const arrow = cmp.tone === "flat" ? "→" : cmp.dir === "up" ? "▲" : "▼";
    const cls = cmp.tone === "good" ? "text-green-600" : cmp.tone === "bad" ? "text-red-600" : "text-gray-400";
    return `<span class="text-xs font-medium ${cls}">${arrow} ${Math.abs(cmp.pct).toFixed(0)}%${suffix ? " " + esc(suffix) : ""}</span>`;
  }

  // Aanbevelingen-feed (impr. #5). items: [{text, eur?, tone?}]
  function recList(items) {
    if (!items || !items.length) {
      return `<div class="text-sm text-gray-400">Geen aanbevelingen — alles ziet er in orde uit.</div>`;
    }
    const rows = items.map((it) => {
      const t = TONE[it.tone] || TONE.info;
      const badge = it.eur != null
        ? `<span class="ml-auto shrink-0 text-sm font-semibold ${t.tag}">${esc(fmt.eur(it.eur))}${it.eurSuffix || ""}</span>`
        : "";
      return `<li class="flex items-start gap-2 p-3 border-l-4 ${t.bar} ${t.bg} rounded">
        <span class="text-sm ${t.txt}">${esc(it.text)}</span>${badge}</li>`;
    }).join("");
    return `<ul class="space-y-2">${rows}</ul>`;
  }

  /* ---- Grid/fase-inzicht via de groepen-map ------------------------ */
  // phases: [{label, power_w, voltage_v}]  (netto per fase; + = import)
  // imbalancePct: number|null
  // trend: [{ts, l1_w, l2_w, l3_w}]  (6h, voor baseline)
  function gridInsight(phases, imbalancePct, trend) {
    const byLabel = {};
    (phases || []).forEach((p) => { byLabel[p.label] = p.power_w; });

    // Baseline per fase = mediaan over de trend-periode.
    const base = {
      L1: median((trend || []).map((r) => r.l1_w)),
      L2: median((trend || []).map((r) => r.l2_w)),
      L3: median((trend || []).map((r) => r.l3_w)),
    };

    // Zwaarst belaste fase nu (grootste absolute vermogen).
    let heaviest = null, heaviestAbs = -1;
    ["L1", "L2", "L3"].forEach((L) => {
      const v = byLabel[L];
      if (v != null && Math.abs(v) > heaviestAbs) { heaviestAbs = Math.abs(v); heaviest = L; }
    });

    // Spike-detectie: fase ligt duidelijk boven eigen basislast -> noem
    // de waarschijnlijke zware apparaten op die fase.
    const SPIKE_W = 800;
    const attribution = [];
    ["L1", "L2", "L3"].forEach((L) => {
      const now = byLabel[L], b = base[L];
      if (now != null && b != null && now - b > SPIKE_W) {
        const loads = heavyLoadsOn(L);
        if (loads.length) {
          attribution.push(`${L} +${fmt.w(now - b)} boven basislast — waarschijnlijk: ${loads.join(" of ")}.`);
        }
      }
    });

    // Tone + titel op basis van onbalans.
    let tone = "info", title, lines = [];
    if (imbalancePct == null) {
      title = "Fasegegevens onvolledig";
      lines.push("Niet alle drie de fasen leverden een meetwaarde.");
    } else if (imbalancePct < 10) {
      tone = "good";
      title = `Fasen goed gebalanceerd (${imbalancePct.toFixed(0)}% onbalans)`;
    } else if (imbalancePct < 25) {
      tone = "warn";
      title = `Matige onbalans (${imbalancePct.toFixed(0)}%) — ${heaviest || "?"} draagt het meeste`;
    } else {
      tone = "bad";
      title = `Hoge onbalans (${imbalancePct.toFixed(0)}%) — ${heaviest || "?"} zwaar belast`;
    }
    attribution.forEach((a) => lines.push(a));
    // Structurele context uit de groepen-map.
    lines.push("Structureel draagt L3 het zwaarst: oven (10) + wasmachine (12) + WP-besturing (11).");

    // Aanbevelingen — neutraal over kosten (nog te verifiëren afrekening).
    const recs = [];
    if (imbalancePct != null && imbalancePct >= 25) {
      const loads = heavyLoadsOn(heaviest || "L3");
      recs.push({ tone: "warn", text: `Draai zware apparaten op ${heaviest} (${loads.join(", ")}) niet gelijktijdig — spreiden verlaagt de onbalans.` });
    }
    recs.push({ tone: "info", text: "Overweeg de wasmachine (groep 12) naar L1 of L2 te verhuizen; dat haalt een grote last van het overbelaste L3." });
    recs.push({ tone: "info", text: "Draai enkelfase-apparaten bij PV-overschot: lokaal zelfverbruik verlaagt zowel import als cross-fase export." });
    recs.push({ tone: "info", text: "Of onbalans geld kost hangt af van je meterafrekening (per fase vs. som van fasen) — nog te verifiëren." });

    return { tone, title, lines, recs, base, heaviest };
  }

  /* ---- Export ------------------------------------------------------ */
  window.ES = {
    palette, phase, groups, multiPhase, heavyLoadsOn,
    fmt, median, compare,
    verdictBox, deltaBadge, recList,
    grid: { insight: gridInsight },
  };
})();
