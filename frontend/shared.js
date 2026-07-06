/* shared.js — gedeelde kern voor alle dashboard-tabs (window.ES).
 * Insights (grid/now/flows/solar/costs) staan in shared-insights.js. */
(function () {
  "use strict";

  // Semantiek: overal dezelfde betekenis.
  const palette = {
    import: "#3b82f6", export: "#22c55e", pv: "#facc15", cost: "#dc2626",
    good: "#16a34a", warn: "#f59e0b", bad: "#dc2626", neutral: "#6b7280", house: "#6b7280",
  };
  // Fasen: bewust NIET blauw/groen, zodat kleuren nooit dubbelzinnig zijn.
  const phase = { L1: "#7c3aed", L2: "#f97316", L3: "#0891b2" };

  // Groepen -> fase -> apparaat (meterkast). Regel: 1-4=L1, 5-8=L2, 9-12=L3.
  // heavy = grote enkelfase-last. (Wasdroger verwijderd; wasmachine nu op L1/groep 4.)
  const groups = [
    { g: 1, phase: "L1", label: "Woon-/eetkamer + verlichting", devices: ["WCD woon-/eetkamer", "verlichting", "overdekt terras"], heavy: false },
    { g: 2, phase: "L1", label: "Keuken + bijkeuken", devices: ["WCD keuken", "bijkeuken", "verlichting"], heavy: false },
    { g: 3, phase: "L1", label: "Magnetron + Quooker", devices: ["Magnetron", "Quooker"], heavy: false },
    { g: 4, phase: "L1", label: "Wasmachine", devices: ["Wasmachine"], heavy: true },
    { g: 5, phase: "L2", label: "Vaatwasser", devices: ["Vaatwasser"], heavy: true },
    { g: 6, phase: "L2", label: "Slaapkamer 2/3 + vliering (ventilatie)", devices: ["Slaapkamer 2/3", "Vliering", "Ventilatie-unit (MVHR)"], heavy: false },
    { g: 7, phase: "L2", label: "Master slaapkamer / badkamer / kleedruimte", devices: ["Slaapkamer 1", "Badkamer 1", "Kleedruimte"], heavy: false },
    { g: 8, phase: "L2", label: "Studeerkamer + overloop", devices: ["Studeerkamer", "Overloop", "Trapopgang"], heavy: false },
    { g: 9, phase: "L3", label: "Tuin / meterkast / carport / schuur", devices: ["Tuin", "Meterkast", "Carport", "Schuur"], heavy: false },
    { g: 10, phase: "L3", label: "Oven", devices: ["Oven"], heavy: true },
    { g: 11, phase: "L3", label: "WP-buitenunit + techniek + Niko", devices: ["Warmtepomp buitenunit", "Technische ruimte", "Dakramen", "Niko main"], heavy: true },
  ];
  const multiPhase = [
    { label: "PV-omvormer", phases: ["L1", "L2", "L3"], note: "verdeelt productie gelijk over 3 fasen" },
    { label: "Kookplaat (inductie)", phases: ["L1", "L2"], note: "2-fase aansluiting" },
    { label: "Warmtepomp", phases: ["L1", "L2", "L3"], note: "3-fase; besturing op groep 11 (L3)" },
  ];
  function heavyLoadsOn(L) { return groups.filter((r) => r.phase === L && r.heavy).map((r) => r.label); }

  const fmt = {
    w(v) { if (v == null) return "–"; const a = Math.abs(v); return a >= 1000 ? (v / 1000).toFixed(2) + " kW" : Math.round(v) + " W"; },
    kwh(v, d = 1) { return v == null ? "–" : v.toFixed(d) + " kWh"; },
    eur(v, d = 2) { if (v == null) return "–"; return (v < 0 ? "−€ " : "€ ") + Math.abs(v).toFixed(d); },
    price(v) { return v == null ? "–" : "€ " + v.toFixed(3) + "/kWh"; },
    pct(v, d = 1) { return v == null ? "–" : v.toFixed(d) + " %"; },
    temp(v) { return v == null ? "–" : v.toFixed(1) + " °C"; },
  };

  function median(arr) {
    const xs = arr.filter((x) => x != null && !Number.isNaN(x)).sort((a, b) => a - b);
    if (!xs.length) return null;
    const m = Math.floor(xs.length / 2);
    return xs.length % 2 ? xs[m] : (xs[m - 1] + xs[m]) / 2;
  }
  // higherIsWorse: true als hoger "slechter" is (kosten, verbruik).
  function compare(value, baseline, higherIsWorse = true) {
    if (value == null || baseline == null || baseline === 0) return null;
    const pct = ((value - baseline) / Math.abs(baseline)) * 100;
    const dir = pct >= 0 ? "up" : "down";
    const worse = higherIsWorse ? pct > 0 : pct < 0;
    const tone = Math.abs(pct) < 3 ? "flat" : worse ? "bad" : "good";
    return { pct, dir, tone };
  }

  const TONE = {
    good: { bar: "border-green-500", bg: "bg-green-50", txt: "text-green-900", tag: "text-green-700" },
    warn: { bar: "border-amber-500", bg: "bg-amber-50", txt: "text-amber-900", tag: "text-amber-700" },
    bad: { bar: "border-red-500", bg: "bg-red-50", txt: "text-red-900", tag: "text-red-700" },
    info: { bar: "border-blue-500", bg: "bg-blue-50", txt: "text-blue-900", tag: "text-blue-700" },
  };
  function esc(s) { return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }

  function verdictBox(opts) {
    if (!opts) return "";
    const t = TONE[opts.tone] || TONE.info;
    const lines = (opts.lines || []).map((l) => `<li>${esc(l)}</li>`).join("");
    return `<div class="border-l-4 ${t.bar} ${t.bg} rounded p-4 mb-6">
      <div class="text-xs uppercase tracking-wide font-semibold ${t.tag}">Wat dit betekent</div>
      <div class="text-lg font-bold ${t.txt} mt-0.5">${esc(opts.title)}</div>
      ${lines ? `<ul class="list-disc list-inside text-sm ${t.txt} mt-2 space-y-0.5">${lines}</ul>` : ""}
    </div>`;
  }
  function deltaBadge(cmp, suffix = "") {
    if (!cmp) return "";
    const arrow = cmp.tone === "flat" ? "→" : cmp.dir === "up" ? "▲" : "▼";
    const cls = cmp.tone === "good" ? "text-green-600" : cmp.tone === "bad" ? "text-red-600" : "text-gray-400";
    return `<span class="text-xs font-medium ${cls}">${arrow} ${Math.abs(cmp.pct).toFixed(0)}%${suffix ? " " + esc(suffix) : ""}</span>`;
  }
  function recList(items) {
    if (!items || !items.length) return `<div class="text-sm text-gray-400">Geen aanbevelingen — alles ziet er in orde uit.</div>`;
    const rows = items.map((it) => {
      const t = TONE[it.tone] || TONE.info;
      return `<li class="flex items-start gap-2 p-3 border-l-4 ${t.bar} ${t.bg} rounded"><span class="text-sm ${t.txt}">${esc(it.text)}</span></li>`;
    }).join("");
    return `<ul class="space-y-2">${rows}</ul>`;
  }

  window.ES = {
    palette, phase, groups, multiPhase, heavyLoadsOn,
    fmt, median, compare, verdictBox, deltaBadge, recList,
  };
})();
