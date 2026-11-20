/* THE TRADING FLOOR - candles + toggleable analysis layers. No averaging lines of price are drawn:
   VWAP (volume-anchored) and its σ bands are the only lines derived from price. */
const Floor = (() => {
  const LAYERS = [
    ["zones", "S/D zones", true], ["sessions", "Sessions", true], ["keylevels", "PDH/PDL/PWH/PWL", true],
    ["asia", "Asia range", true], ["liquidity", "Liquidity", true], ["fvg", "FVG/OB", false],
    ["structure", "BOS/CHoCH", true], ["vp", "Volume Profile", true], ["vwap", "VWAP ±σ", true],
    ["levels", "Daily levels", true], ["volume", "Volume/RVOL", true],
  ];
  const on = Object.fromEntries(LAYERS.map((l) => [l[0], l[2]]));
  const GRADE_COL = { "A+": "#f5c542", A: "#39ff14", B: "#00e5ff", C: "#4c6688" };
  const SESS_COL = { asia: "179,136,255", london: "0,229,255", newyork: "255,159,28" };
  let chart, candles, vol, cvdChart, cvdSeries, overlay, g, data = null, times = [], priceLines = [], vw = {};
  let symbol = null, tf = "M5", precision = 2, lastLoad = 0, loading = false;

  function precisionFor(sym) { return sym.startsWith("XAU") ? 2 : sym.startsWith("XAG") ? 3 : sym.endsWith("JPY") ? 3 : 5; }

  function init() {
    const el = document.getElementById("chart");
    const common = {
      layout: { background: { type: "solid", color: "transparent" }, textColor: "#7f9cc0", fontFamily: "ShareTechMono" },
      grid: { vertLines: { color: "rgba(60,110,180,0.08)" }, horzLines: { color: "rgba(60,110,180,0.08)" } },
      timeScale: { timeVisible: true, secondsVisible: false, borderColor: "rgba(110,190,255,0.25)", rightOffset: 8 },
      rightPriceScale: { borderColor: "rgba(110,190,255,0.25)" },
      crosshair: { mode: 0 }, autoSize: true,
    };
    chart = LightweightCharts.createChart(el, common);
    candles = chart.addCandlestickSeries({ upColor: "#39ff14", downColor: "#ff3b3b", borderUpColor: "#39ff14",
      borderDownColor: "#ff3b3b", wickUpColor: "#8dffb0", wickDownColor: "#ff8a8a" });
    vol = chart.addHistogramSeries({ priceScaleId: "vol", priceFormat: { type: "volume" }, lastValueVisible: false, priceLineVisible: false });
    chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    const mk = (color, style, width) => chart.addLineSeries({ color, lineStyle: style, lineWidth: width, lastValueVisible: false,
      priceLineVisible: false, crosshairMarkerVisible: false });
    vw = { d: mk("#00e5ff", 0, 2), du1: mk("rgba(0,229,255,.55)", 1, 1), dl1: mk("rgba(0,229,255,.55)", 1, 1),
      du2: mk("rgba(0,229,255,.45)", 2, 1), dl2: mk("rgba(0,229,255,.45)", 2, 1), w: mk("#b388ff", 0, 2) };
    cvdChart = LightweightCharts.createChart(document.getElementById("cvd"), { ...common, timeScale: { ...common.timeScale, visible: false } });
    cvdSeries = cvdChart.addLineSeries({ color: "#ffd166", lineWidth: 2, priceLineVisible: false });
    chart.timeScale().subscribeVisibleLogicalRangeChange((r) => { if (r) cvdChart.timeScale().setVisibleLogicalRange(r); draw(); });
    overlay = document.getElementById("overlay");
    g = overlay.getContext("2d");
    new ResizeObserver(() => draw()).observe(document.querySelector(".chartwrap"));
    const box = document.getElementById("layers");
    box.innerHTML = LAYERS.map(([k, n]) => `<label><input type="checkbox" data-k="${k}" ${on[k] ? "checked" : ""}><span>${n}</span></label>`).join("");
    box.querySelectorAll("input").forEach((i) => i.addEventListener("change", () => { on[i.dataset.k] = i.checked; apply(); }));
  }

  async function load(sym, timeframe, force) {
    if (loading) return;
    if (!force && sym === symbol && timeframe === tf && Date.now() - lastLoad < 12000) return;
    loading = true;
    const changed = sym !== symbol || timeframe !== tf;
    symbol = sym; tf = timeframe;
    try {
      data = await U.api(`/api/chart?symbol=${encodeURIComponent(sym)}&tf=${timeframe}&limit=400`);
      lastLoad = Date.now();
      precision = precisionFor(sym);
      candles.applyOptions({ priceFormat: { type: "price", precision, minMove: Math.pow(10, -precision) } });
      times = data.candles.map((c) => c.time);
      candles.setData(data.candles);
      apply();
      if (changed) chart.timeScale().scrollToRealTime();
      header();
    } catch (e) { console.warn(e); } finally { loading = false; }
  }

  function header() {
    if (!data) return;
    const q = data.quote || {};
    const st = q.state || "NO DATA";
    const col = st === "LIVE" ? "green" : st === "STALE" || st === "MARKET CLOSED" ? "yellow" : "red";
    document.getElementById("srcText").innerHTML = `<span class="badge ${q.preview ? "preview" : ""}">${U.esc(q.label || "no source")}</span>
      <span class="${col}">${U.esc(st)}</span> <span>${U.esc(q.text || "")}</span>`;
    const d = q.oanda_mt5_diff;
    document.getElementById("diffText").innerHTML = U.isNum(d) ? `OANDA vs MT5 diff <b class="${Math.abs(d) > 0 ? "yellow" : ""}">${d.toFixed(precision)}</b>` : "";
    const c = data.context || {};
    document.getElementById("ctxText").textContent = c.session ? `session ${c.session} · regime ${(c.regime || {}).regime || "n/a"} · ADR used ${U.isNum(c.adr_used) ? c.adr_used.toFixed(0) + "%" : "n/a"}` : "";
    document.getElementById("volTag").textContent = data.has_volume ? "volume (tick-volume estimate) · colour = RVOL" : "volume tools disabled - source has no volume";
  }

  function apply() {
    if (!data) return;
    const L = data.layers || {};
    // volume coloured by RVOL
    vol.setData(on.volume ? (L.volume || []).map((v) => ({ time: v.time, value: v.value,
      color: v.rvol >= 2 ? "rgba(255,61,242,.85)" : v.rvol >= 1.5 ? "rgba(255,159,28,.8)" : v.rvol >= 1.2 ? "rgba(0,229,255,.7)" : "rgba(77,171,255,.28)" })) : []);
    // CVD pane aligned bar-for-bar (whitespace where no value)
    const cmap = new Map((L.cvd || []).map((p) => [p.time, p.value]));
    cvdSeries.setData(times.map((t) => (cmap.has(t) ? { time: t, value: cmap.get(t) } : { time: t })));
    cvdSeries.setMarkers((L.divergences || []).filter((d) => cmap.has(d.time)).map((d) => ({ time: d.time,
      position: d.type === "bearish" ? "aboveBar" : "belowBar", color: d.type === "bearish" ? "#ff3b3b" : "#39ff14",
      shape: d.type === "bearish" ? "arrowDown" : "arrowUp", text: "div" })));
    // VWAP + bands
    const V = (L.vwap || {});
    const set = (s, arr) => s.setData(on.vwap && arr ? arr : []);
    set(vw.d, (V.daily || {}).vwap); set(vw.du1, (V.daily || {}).u1); set(vw.dl1, (V.daily || {}).l1);
    set(vw.du2, (V.daily || {}).u2); set(vw.dl2, (V.daily || {}).l2); set(vw.w, (V.weekly || {}).vwap);
    // key levels as price lines
    priceLines.forEach((p) => candles.removePriceLine(p)); priceLines = [];
    if (on.keylevels) (L.key_levels || []).forEach((k) => priceLines.push(candles.createPriceLine({ price: k.price,
      color: k.label.startsWith("PW") ? "#b388ff" : k.label === "D-OPEN" ? "#9fb5d6" : "#ffd166", lineWidth: 1, lineStyle: 2,
      axisLabelVisible: true, title: k.label })));
    // structure markers
    candles.setMarkers(on.structure ? (L.structure || []).filter((s) => times.includes(s.time)).map((s) => ({ time: s.time,
      position: s.dir === "bull" ? "belowBar" : "aboveBar", color: s.dir === "bull" ? "#39ff14" : "#ff3b3b",
      shape: s.dir === "bull" ? "arrowUp" : "arrowDown", text: s.kind })) : []);
    draw();
  }

  // ---------- overlay helpers
  function xOf(t) {
    if (!times.length) return null;
    const ts = chart.timeScale();
    if (t <= times[0]) return ts.logicalToCoordinate(0) ?? 0;
    let lo = 0, hi = times.length - 1;
    if (t >= times[hi]) { const step = times[hi] - times[hi - 1] || 60; return ts.logicalToCoordinate(hi + (t - times[hi]) / step); }
    while (hi - lo > 1) { const m = (lo + hi) >> 1; if (times[m] <= t) lo = m; else hi = m; }
    const frac = (t - times[lo]) / ((times[hi] - times[lo]) || 1);
    return ts.logicalToCoordinate(lo + frac);
  }
  const yOf = (p) => candles.priceToCoordinate(p);

  function rect(x1, x2, p1, p2, fill, stroke, label) {
    const y1 = yOf(p1), y2 = yOf(p2);
    if (y1 == null || y2 == null || x1 == null) return;
    const top = Math.min(y1, y2), h = Math.max(2, Math.abs(y1 - y2));
    g.fillStyle = fill; g.fillRect(x1, top, x2 - x1, h);
    if (stroke) { g.strokeStyle = stroke; g.lineWidth = 1; g.strokeRect(x1 + .5, top + .5, x2 - x1 - 1, h - 1); }
    if (label) { g.fillStyle = stroke || "#cfe"; g.font = "10px ShareTechMono"; g.fillText(label, Math.max(4, x1 + 3), top + 10); }
  }
  function hline(x1, x2, p, color, dash, label, alignRight) {
    const y = yOf(p); if (y == null) return;
    g.strokeStyle = color; g.setLineDash(dash || []); g.lineWidth = 1;
    g.beginPath(); g.moveTo(x1, y + .5); g.lineTo(x2, y + .5); g.stroke(); g.setLineDash([]);
    if (label) { g.fillStyle = color; g.font = "10px ShareTechMono"; const w = g.measureText(label).width; g.fillText(label, alignRight ? x2 - w - 4 : x1 + 3, y - 3); }
  }

  function draw() {
    if (!overlay || !data) return;
    const W = overlay.clientWidth, H = overlay.clientHeight, dpr = window.devicePixelRatio || 1;
    if (overlay.width !== W * dpr || overlay.height !== H * dpr) { overlay.width = W * dpr; overlay.height = H * dpr; }
    g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, W, H);
    const L = data.layers || {}, right = W - 62;
    if (on.sessions) (L.sessions || []).forEach((s) => {
      const x1 = xOf(s.start), x2 = xOf(s.end); if (x1 == null || x2 == null || x2 < 0 || x1 > right) return;
      const c = SESS_COL[s.name] || "200,200,200";
      rect(Math.max(0, x1), Math.min(right, x2), s.high, s.low, `rgba(${c},.05)`, `rgba(${c},.35)`, s.name.toUpperCase());
    });
    if (on.asia && L.asia) {
      const x1 = Math.max(0, xOf(L.asia.start) ?? 0);
      hline(x1, right, L.asia.high, "rgba(179,136,255,.9)", [6, 4], "ASIA H", true);
      hline(x1, right, L.asia.low, "rgba(179,136,255,.9)", [6, 4], "ASIA L", true);
    }
    if (on.levels) (L.daily_levels || []).slice().sort((a, b) => a.score - b.score).forEach((lv) => {
      if (lv.grade === "C") return;
      const col = GRADE_COL[lv.grade] || "#888";
      const y1 = yOf(lv.high), y2 = yOf(lv.low); if (y1 == null || y2 == null) return;
      g.fillStyle = col + "1f"; g.fillRect(0, Math.min(y1, y2), right, Math.max(2, Math.abs(y1 - y2)));
      g.fillStyle = col; g.font = "10px ShareTechMono";
      const txt = `${lv.grade} ${lv.state}${lv.countdown ? " ⏱" + lv.countdown.bars_left : ""}`;
      g.fillText(txt, right - g.measureText(txt).width - 6, Math.min(y1, y2) - 2);
    });
    if (on.zones) (L.zones || []).forEach((z) => {
      const x1 = Math.max(0, xOf(z.from) ?? 0), dem = z.kind === "demand";
      rect(x1, right, z.high, z.low, dem ? "rgba(57,255,20,.10)" : "rgba(255,59,59,.10)", dem ? "rgba(57,255,20,.55)" : "rgba(255,59,59,.55)",
        `${z.tf} ${z.kind}${z.touches ? " ×" + z.touches : ""}`);
    });
    if (on.fvg) (L.fvg_ob || []).forEach((z) => {
      const x1 = Math.max(0, xOf(z.from) ?? 0), bull = z.kind.endsWith("bull");
      const c = z.kind.startsWith("fvg") ? (bull ? "77,171,255" : "255,61,242") : (bull ? "0,255,163" : "255,159,28");
      rect(x1, right, z.high, z.low, `rgba(${c},.10)`, `rgba(${c},.6)`, `${z.tf} ${z.kind.replace("_", " ").toUpperCase()}`);
    });
    if (on.liquidity) (L.liquidity || []).forEach((q) => {
      const x1 = Math.max(0, xOf(q.from) ?? 0), swept = q.meta && q.meta.swept;
      const c = q.kind === "eqh" ? "255,61,242" : "0,229,255";
      hline(x1, right, q.low, `rgba(${c},${swept ? .35 : .95})`, [2, 3], `${q.kind.toUpperCase()}${swept ? " swept" : ""} ${q.tf}`);
    });
    if (on.structure) (L.structure || []).forEach((s) => {
      const x1 = xOf(s.from), x2 = xOf(s.time); if (x1 == null || x2 == null) return;
      hline(x1, x2, s.price, s.dir === "bull" ? "rgba(57,255,20,.8)" : "rgba(255,59,59,.8)", [4, 3], s.kind);
    });
    if (on.vp) {
      const vp = (L.volume_profile || {}).composite || (L.volume_profile || {}).daily;
      if (vp && vp.bins && vp.bins.length) {
        const maxV = Math.max(...vp.volumes), wMax = W * 0.16, step = vp.bins.length > 1 ? Math.abs(vp.bins[1] - vp.bins[0]) : 0;
        vp.bins.forEach((b, i) => {
          const y1 = yOf(b + step / 2), y2 = yOf(b - step / 2); if (y1 == null || y2 == null) return;
          const inVA = b >= vp.val && b <= vp.vah, w = (vp.volumes[i] / maxV) * wMax;
          g.fillStyle = inVA ? "rgba(0,229,255,.32)" : "rgba(127,156,192,.18)";
          g.fillRect(right - w, Math.min(y1, y2), w, Math.max(1, Math.abs(y2 - y1) - 1));
        });
        hline(right - wMax, right, vp.poc, "#ffd166", [], "POC", false);
        hline(right - wMax, right, vp.vah, "rgba(0,229,255,.8)", [3, 3], "VAH", false);
        hline(right - wMax, right, vp.val, "rgba(0,229,255,.8)", [3, 3], "VAL", false);
      }
    }
  }

  function tick(quote) {
    if (!data || !data.candles.length || !quote || !U.isNum(quote.bid)) return;
    const mid = (quote.bid + quote.ask) / 2, sec = { M1: 60, M5: 300, M15: 900, H1: 3600, H4: 14400, D1: 86400 }[tf];
    const last = data.candles[data.candles.length - 1], now = Math.floor((quote.last_tick || Date.now() / 1000));
    const bucket = Math.floor(now / sec) * sec;
    if (tf === "D1" || bucket <= last.time) {
      last.close = mid; last.high = Math.max(last.high, mid); last.low = Math.min(last.low, mid);
      candles.update(last);
    } else if (bucket > last.time) {
      const c = { time: bucket, open: last.close, high: Math.max(last.close, mid), low: Math.min(last.close, mid), close: mid };
      data.candles.push(c); times.push(bucket); candles.update(c);
    }
    data.quote = quote; header();
  }

  return { init, load, tick, get symbol() { return symbol; }, get tf() { return tf; } };
})();
