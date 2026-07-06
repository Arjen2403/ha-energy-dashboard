/* grid.js — Grid/Fases tab. Gebruikt window.ES uit shared.js. */
function gridApp() {
    return {
        phases: [{label:'L1',power_w:null,voltage_v:null},
                 {label:'L2',power_w:null,voltage_v:null},
                 {label:'L3',power_w:null,voltage_v:null}],
        imbalance_pct: null,
        total_w: null,
        error: null,
        lastUpdated: null,
        trendLoaded: false,
        trendData: [],
        insight: null,
        measured: {},

        phaseColor(L) { return ES.phase[L] || '#6b7280'; },
        fmtW(v) { return ES.fmt.w(v); },
        groupsOf(L) { return ES.groups.filter(r => r.phase === L); },
        baselineOf(L) { return this.insight ? this.insight.base[L] : null; },
        measuredOn(L) {
            const m = (this.insight && this.insight.measured) ? this.insight.measured[L] : null;
            return (m || []).filter(d => d.w != null && Math.abs(d.w) > 50);
        },

        get verdictHtml() {
            if (!this.insight) return '';
            return ES.verdictBox({ tone: this.insight.tone, title: this.insight.title, lines: this.insight.lines });
        },
        get recsHtml() { return this.insight ? ES.recList(this.insight.recs) : ''; },

        computeInsight() {
            if (!this.phases.some(p => p.power_w !== null)) { this.insight = null; return; }
            this.insight = ES.grid.insight(this.phases, this.imbalance_pct, this.trendData || [], this.measured);
        },

        async load() {
            await Promise.all([
                this.loadLive(),
                this.loadOverview(),
                this.trendLoaded ? Promise.resolve() : this.loadTrend(),
            ]);
        },

        async loadOverview() {
            try {
                const res = await fetch('/api/overview');
                if (!res.ok) return;
                const data = await res.json();
                const k = data.kpi || {};
                this.measured = { quooker_w: k.quooker_w, afwasmachine_w: k.afwasmachine_w, hp_w: k.heatpump_w };
                this.computeInsight();
            } catch (e) { /* stille fout — gemeten data is optioneel */ }
        },

        async loadLive() {
            try {
                const res = await fetch('/api/grid/live');
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                const data = await res.json();
                this.phases = data.phases.length ? data.phases :
                    [{label:'L1',power_w:null,voltage_v:null},
                     {label:'L2',power_w:null,voltage_v:null},
                     {label:'L3',power_w:null,voltage_v:null}];
                this.imbalance_pct = data.imbalance_pct;
                this.total_w = data.total_w;
                this.lastUpdated = new Date().toLocaleTimeString('nl-NL');
                this.error = null;
                this.computeInsight();
                this.$nextTick(() => this.drawBar());
            } catch(e) {
                this.error = `Fout bij laden: ${e.message}`;
            }
        },

        async loadTrend() {
            try {
                const res = await fetch('/api/grid/trend');
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                const data = await res.json();
                this.trendLoaded = true;
                this.trendData = data.trend || [];
                this.computeInsight();
                this.$nextTick(() => this.drawTrend(this.trendData));
            } catch(e) {
                console.warn('Trend load failed:', e.message);
            }
        },

        drawBar() {
            const labels = this.phases.map(p => 'Fase ' + p.label);
            const values = this.phases.map(p => p.power_w ?? 0);
            const colors = this.phases.map(p => this.phaseColor(p.label));
            Plotly.newPlot('phase-bar-chart', [{
                type: 'bar', x: labels, y: values,
                marker: { color: colors },
                text: values.map(v => Math.round(v) + ' W'),
                textposition: 'outside',
                hovertemplate: '%{x}: %{y} W<extra></extra>',
            }], {
                yaxis: { title: 'Vermogen (W)', zeroline: true, zerolinecolor: '#9ca3af' },
                margin: { t: 10, l: 50, r: 20, b: 40 },
                showlegend: false,
            }, { responsive: true });
        },

        drawTrend(trend) {
            if (!trend || trend.length === 0) return;
            const ts = trend.map(r => r.ts);
            Plotly.newPlot('phase-trend-chart', [
                { type:'scatter', mode:'lines', name:'L1', x:ts, y:trend.map(r=>r.l1_w), line:{color:ES.phase.L1, width:2} },
                { type:'scatter', mode:'lines', name:'L2', x:ts, y:trend.map(r=>r.l2_w), line:{color:ES.phase.L2, width:2} },
                { type:'scatter', mode:'lines', name:'L3', x:ts, y:trend.map(r=>r.l3_w), line:{color:ES.phase.L3, width:2} },
            ], {
                yaxis: { title:'W', zeroline:true, zerolinecolor:'#9ca3af' },
                xaxis: { type:'date' },
                margin: { t:10, l:50, r:20, b:40 },
                legend: { orientation:'h', y:-0.2 },
            }, { responsive:true });
        },
    };
}

document.addEventListener('alpine:init', () => {
    setInterval(() => {
        const app = document.querySelector('[x-data]')?._x_dataStack?.[0];
        if (app) { app.loadLive(); app.loadOverview(); }
    }, 30000);
});
