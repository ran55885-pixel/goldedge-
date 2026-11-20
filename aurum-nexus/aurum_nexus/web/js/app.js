/* AURUM NEXUS dashboard controller. Renders ONLY backend values; stale/missing data is shown as
   STALE / DISCONNECTED / NO DATA / n/a with the last real value and its timestamp. */
(() => {
  const $ = (id) => document.getElementById(id);
  let S = null;                 // last state snapshot
  let account = localStorage.getItem("an_acc") || "ALL";
  const events = [];
  let eqChart, eqSeries, dxyChart, dxySeries, dxyLines = [];
  const DESK_ORDER = ["SCANNER", "LEVELS", "SIGNAL", "RISK", "EXECUTION", "GUARDIAN", "ACCOUNTS", "ANALYST", "STRATEGIST"];
  const GHOST = (c) => `<svg class="ghost" viewBox="0 0 40 48"><defs><radialGradient id="gg${c.slice(1)}" cx="50%" cy="35%" r="70%">
    <stop offset="0" stop-color="#fff" stop-opacity=".95"/><stop offset=".5" stop-color="${c}" stop-opacity=".75"/><stop offset="1" stop-color="${c}" stop-opacity=".15"/></radialGradient></defs>
    <path d="M20 2C9 2 4 11 4 21v24l5-4 5 4 6-4 6 4 5-4 5 4V21C36 11 31 2 20 2z" fill="url(#gg${c.slice(1)})" stroke="${c}" stroke-width="1"/>
    <circle cx="14" cy="20" r="3.2" fill="#02050d"/><circle cx="26" cy="20" r="3.2" fill="#02050d"/><circle cx="15" cy="19" r="1" fill="#fff"/><circle cx="27" cy="19" r="1" fill="#fff"/></svg>`;

  // ------------------------------------------------------------------ boot
  async function boot() {
    Floor.init();
    initEquity(); initDxy(); buildDesks();
    document.querySelectorAll(".nav button").forEach((b) => b.addEventListener("click", () => {
      document.querySelectorAll(".nav button").forEach((x) => x.classList.toggle("active", x === b));
      document.querySelectorAll(".view").forEach((v) => v.classList.toggle("active", v.id === "view-" + b.dataset.view));
      if (S) renderViews();
    }));
    $("symSel").addEventListener("change", () => Floor.load($("symSel").value, $("tfSel").value, true));
    $("tfSel").addEventListener("change", () => Floor.load($("symSel").value, $("tfSel").value, true));
    $("accSel").addEventListener("change", () => { account = $("accSel").value; localStorage.setItem("an_acc", account); loadEquity(); if (S) render(); });
    $("killBtn").addEventListener("click", kill);
    $("pauseAllBtn").addEventListener("click", pauseAll);
    $("qrBtn").addEventListener("click", showQR);
    $("logoutBtn").addEventListener("click", async () => { await U.post("/api/logout"); location.href = "/login"; });
    $("modalClose").addEventListener("click", () => $("modal").classList.remove("open"));
    $("modal").addEventListener("click", (e) => { if (e.target.id === "modal") $("modal").classList.remove("open"); });
    try { const ev = await U.api("/api/events?n=200"); ev.forEach(addEvent); } catch (e) {}
    try { S = await U.api("/api/state"); if (S && S.symbols) firstRender(); } catch (e) {}
    connect();
    setInterval(() => { if (S && S.symbols) Floor.load($("symSel").value, $("tfSel").value); }, 4000);
    setInterval(loadEquity, 30000);
  }

  function firstRender() {
    if (!$("symSel").options.length) {
      $("symSel").innerHTML = S.symbols.map((s) => `<option>${s}</option>`).join("");
      Floor.load(S.symbols[0], $("tfSel").value, true);
    }
    render(); loadEquity();
  }

  function connect() {
    const ws = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws");
    ws.onmessage = (m) => {
      const msg = JSON.parse(m.data);
      if (msg.type === "state" && msg.state && msg.state.symbols) {
        const first = !S || !S.symbols;
        S = msg.state;
        if (first) firstRender(); else render();
      } else if (msg.type === "events") msg.events.forEach(addEvent);
    };
    ws.onclose = (e) => { if (e.code === 4401) location.href = "/login"; else setTimeout(connect, 2000); setDot("bad"); };
  }
  const setDot = (c) => { $("statusDot").className = "dot " + c; };

  // ------------------------------------------------------------------ render
  function render() {
    renderTop(); renderBalance(); renderEdge(); renderRanking(); renderLevels(); renderDxy(); renderTM();
    renderCtx(); renderDesks(); renderViews();
    const q = S.quotes[$("symSel").value];
    if (q) Floor.tick(q);
  }
  function renderViews() {
    if ($("view-accounts").classList.contains("active")) renderAccounts();
    if ($("view-strategy").classList.contains("active")) renderStrategy();
  }

  function renderTop() {
    $("ver").textContent = `v${S.version} · cycle ${S.cycle_ms}ms`;
    $("uptime").textContent = U.dur(S.uptime);
    $("accConn").textContent = `${S.accounts_connected}/${S.accounts_mt5} MT5`;
    $("openPos").textContent = S.open_trades.length + realOtherPositions();
    const risk = S.open_trades.reduce((a, t) => a + (t.risk_money || 0) * ((t.volume_open || 0) / (t.volume || 1)), 0);
    const bank = mainEquity();
    $("inPos").textContent = S.open_trades.length ? `${U.fmt(risk)} (${bank ? (100 * risk / bank).toFixed(2) : "n/a"}%)` : "0.00 (0.00%)";
    $("inPos").title = "risk at stop of open system positions / equity";
    $("clock").textContent = U.hhmmss(S.now);
    const src = S.source || {};
    const sb = $("srcBadge");
    const kind = src.kind || "none";
    sb.className = "badge " + (kind === "mock" ? "sim" : src.preview ? "preview" : "");
    sb.textContent = kind === "mock" ? "SIMULATED DATA" : `${(src.label || "no source").split("·")[0]}`;
    sb.title = src.label || "";
    const live = S.accounts.some((a) => a.mode === "live");
    $("modeBadge").className = "badge " + (live ? "live" : "paper");
    $("modeBadge").textContent = live ? "LIVE TRADING" : "PAPER";
    $("killBtn").classList.toggle("on", !!S.kill_switch);
    $("killBtn").textContent = S.kill_switch ? "KILL ACTIVE" : "KILL SWITCH";
    const allPaused = S.accounts.length && S.accounts.every((a) => a.paused);
    $("pauseAllBtn").textContent = allPaused ? "RESUME ALL" : "PAUSE ALL";
    const st = src.state;
    setDot(S.kill_switch ? "bad" : st === "LIVE" ? "ok" : st === "STALE" || st === "MARKET CLOSED" ? "warn" : "bad");
    const opts = ["ALL", ...S.accounts.map((a) => a.name)];
    if ($("accSel").options.length !== opts.length) {
      $("accSel").innerHTML = opts.map((o) => `<option ${o === account ? "selected" : ""}>${o}</option>`).join("");
      if (!opts.includes(account)) { account = "ALL"; }
    }
    const b = $("banner");
    if (kind === "mock") { b.className = "banner sim"; b.textContent = "SIMULATE MODE - every price on this screen is SIMULATED test data, not market data."; }
    else if (src.preview) { b.className = "banner preview"; b.textContent = `${src.label} - analysis preview on public data; live trading is refused on this source. Connect an MT5 terminal or OANDA for broker prices.`; }
    else b.className = "banner";
  }
  function realOtherPositions() {
    return S.accounts.reduce((n, a) => n + ((a.real && a.real.positions) ? a.real.positions.filter((p) => p.tag !== "BOT").length : 0), 0);
  }
  function mainEquity() {
    const acc = S.accounts.find((a) => a.name === account);
    if (acc) return (acc.real && acc.real.status === "OK" ? acc.real.equity : acc.paper && acc.paper.equity) || null;
    const cur = Object.keys(S.totals)[0];
    if (cur) return S.totals[cur].equity;
    const pc = Object.keys(S.paper_totals)[0];
    return pc ? S.paper_totals[pc].equity : null;
  }

  function renderBalance() {
    let bal = null, ccy = "", label = "", unreal = null, today = null, note = "", grey = false;
    const acc = S.accounts.find((a) => a.name === account);
    if (acc) {
      const r = acc.real;
      if (r && (r.status === "OK" || r.status === "STALE")) {
        bal = r.balance; ccy = r.currency; unreal = r.profit; today = r.realized_today;
        label = `${acc.name} · MT5 ${r.trade_mode}${r.status === "STALE" ? " · STALE" : ""}`; grey = r.status === "STALE";
        if (acc.paper) note = `PAPER (separate): ${U.money(acc.paper.equity, acc.paper.currency)} equity`;
      } else if (r) {
        label = `${acc.name} · ${r.status}`; grey = true;
        bal = r.balance; ccy = r.currency || "";
        note = r.balance != null ? `last real value at ${U.dt(r.last_ok)}` : `${r.status} · no real figures (${r.error || "terminal not connected"})`;
        if (acc.paper) note += ` · PAPER ${U.money(acc.paper.equity, acc.paper.currency)}`;
      } else if (acc.paper) {
        bal = acc.paper.balance; ccy = acc.paper.currency; unreal = acc.paper.profit; today = acc.paper.realized_today;
        label = `${acc.name} · PAPER`;
      }
    } else {
      const curs = Object.keys(S.totals);
      if (curs.length) {
        const t = S.totals[curs[0]]; bal = t.balance; ccy = curs[0]; unreal = t.profit; today = t.realized_today;
        label = `ALL MT5 (${t.accounts}) · ${curs[0]}`;
        if (curs.length > 1) note = curs.slice(1).map((c) => `${c}: ${U.money(S.totals[c].balance, c)}`).join(" · ");
      } else {
        const pc = Object.keys(S.paper_totals);
        if (pc.length) {
          const t = S.paper_totals[pc[0]]; bal = t.balance; ccy = pc[0]; unreal = t.profit;
          label = `ALL · PAPER (${t.accounts})`; note = "no MT5 account connected - paper figures";
          if (pc.length > 1) note += " · " + pc.slice(1).map((c) => `${c}: ${U.money(S.paper_totals[c].balance, c)}`).join(" · ");
        } else label = "NO DATA";
      }
    }
    $("balLabel").textContent = label;
    $("balance").classList.toggle("state-STALE", grey);
    U.countTo($("balance"), bal, (v) => (U.isNum(v) ? U.fmt(v) + " " + ccy : "NO DATA"));
    const st = S.stats || {};
    const tp = $("totalPnl"); U.countTo(tp, st.pnl, (v) => U.signed(v)); tp.className = "v " + U.cls(st.pnl);
    const ur = $("unreal"); U.countTo(ur, unreal, (v) => U.signed(v)); ur.className = "v " + U.cls(unreal);
    const td = $("todayReal"); td.textContent = U.signed(today); td.className = "v " + U.cls(today);
    $("winRate").textContent = U.isNum(st.win_rate) ? st.win_rate.toFixed(1) + "%" : "n/a";
    $("balNote").textContent = note;
  }

  function renderEdge() {
    const s = S.stats || {};
    const pf = s.profit_factor === null || s.profit_factor === undefined ? "n/a" : U.fmt(s.profit_factor, 2);
    $("edge").innerHTML = [["TRADES", s.trades ?? 0], ["EXPECTANCY", U.isNum(s.expectancy_r) ? U.r(s.expectancy_r) : "n/a"],
      ["PROFIT FACTOR", pf], ["AVG WIN", U.isNum(s.avg_win_r) ? U.r(s.avg_win_r) : "n/a"], ["AVG LOSS", U.isNum(s.avg_loss_r) ? U.r(s.avg_loss_r) : "n/a"],
      ["MAX DD", U.isNum(s.max_dd_r) ? s.max_dd_r + "R" : "n/a"], ["TOTAL", U.isNum(s.total_r) ? U.r(s.total_r) : "n/a"]]
      .map(([k, v]) => `<span class="k">${k}</span><span class="v">${v}</span>`).join("");
  }

  // ------------------------------------------------------------------ activity log
  function addEvent(e) {
    events.push(e); if (events.length > 400) events.shift();
    const box = $("log"); if (!box) return;
    const row = document.createElement("div"); row.className = "row";
    const tag = String(e.tag || "").replace(/\s/g, "");
    row.innerHTML = `<span class="t">${U.hhmmss(e.ts)}</span><span class="tag tag-${tag}">${U.esc(e.tag)}</span>${U.esc(e.message)}`;
    row.title = e.message;
    const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 30;
    box.appendChild(row);
    while (box.children.length > 300) box.removeChild(box.firstChild);
    if (atBottom) box.scrollTop = box.scrollHeight;
    $("logCount").textContent = `${events.length} events`;
  }

  // ------------------------------------------------------------------ ranking / levels
  function renderRanking() {
    const ms = U.isNum(S.min_score) ? S.min_score : 70;
    const slots = Object.entries(S.slots || {}).map(([k, v]) => `${k} ${v.open}/${v.max} · risk ${v.open_risk_pct}%`).join(" | ");
    $("slots").textContent = slots;
    const rows = (S.ranking || []).slice(0, 14);
    const open = new Set(S.open_trades.map((t) => t.symbol + t.direction));
    $("ranking").innerHTML = `<tr><th>SYM</th><th>SETUP</th><th>TF</th><th>SCORE</th><th>RR</th><th>STATUS</th></tr>` +
      (rows.length ? rows.map((r) => {
        const st = open.has(r.symbol + r.direction) && r.status === "TAKEN" ? "IN TRADE" : r.status;
        return `<tr title="${U.esc((r.notes || []).join(" · "))}"><td>${r.symbol} <span class="${r.direction === "long" ? "green" : "red"}">${r.direction === "long" ? "▲" : "▼"}</span></td>
        <td>${U.esc(r.setup)}</td><td>${r.timeframe}</td>
        <td><span class="scorebar"><i style="width:${Math.max(0, Math.min(100, r.score))}%"></i><span class="min" style="left:${ms}%"></span></span> ${r.score.toFixed(0)}</td>
        <td>${U.isNum(r.rr) ? r.rr.toFixed(2) : "—"}</td><td><span class="chip ${st.replace(" ", "")}">${st}</span></td></tr>
        <tr><td colspan="6" class="reason">${U.esc(r.reason || (r.confirmations || []).join(", "))}</td></tr>`;
      }).join("") : `<tr><td colspan="6" class="empty">no candidates yet - setups appear when a playbook triggers on a closed bar</td></tr>`);
  }

  function renderLevels() {
    const sym = $("symSel").value || S.symbols[0];
    $("lvSym").textContent = sym + " · price " + U.px((S.context[sym] || {}).price, sym);
    const lv = (S.levels || {})[sym] || [];
    $("levels").innerHTML = `<tr><th>LEVEL</th><th>GR</th><th>DIST</th><th>STATE</th><th>NOTES</th></tr>` + (lv.length ? lv.map((l) => {
      const cd = l.countdown ? ` ⏱ ${l.countdown.bars_left} bars` : "";
      return `<tr><td class="${l.role === "support" ? "green" : l.role === "resistance" ? "red" : ""}">${U.px(l.mid, sym)}</td>
      <td><span class="grade ${U.gradeCls(l.grade)}">${l.grade}</span></td><td>${U.isNum(l.distance_atr) ? l.distance_atr.toFixed(1) + "×" : ""}</td>
      <td class="st-${l.state}">${l.state}${cd}</td><td class="reason" title="${U.esc(l.sources.join(", "))}">${U.esc(l.expected)} · ${U.esc(l.sources.slice(0, 3).join(", "))}${(l.reactions || []).length ? " · " + U.esc(l.reactions.join(" + ")) : ""}</td></tr>`;
    }).join("") : `<tr><td colspan="5" class="empty">level map not built yet</td></tr>`);
  }

  // ------------------------------------------------------------------ DXY
  function initDxy() {
    dxyChart = LightweightCharts.createChart($("dxychart"), { autoSize: true, layout: { background: { type: "solid", color: "transparent" }, textColor: "#7f9cc0", fontFamily: "ShareTechMono" },
      grid: { vertLines: { visible: false }, horzLines: { color: "rgba(60,110,180,.08)" } }, timeScale: { timeVisible: true, borderColor: "rgba(110,190,255,.2)" }, rightPriceScale: { borderColor: "rgba(110,190,255,.2)" } });
    dxySeries = dxyChart.addCandlestickSeries({ upColor: "#00e5ff", downColor: "#ff3df2", borderVisible: false, wickUpColor: "#00e5ff", wickDownColor: "#ff3df2",
      priceFormat: { type: "price", precision: 3, minMove: 0.001 } });
  }
  function renderDxy() {
    const d = S.dxy || {};
    $("dxyLabel").textContent = d.available ? d.label : "n/a";
    if (!d.available) { $("dxyInfo").innerHTML = `<div class="empty">${U.esc(d.reason || "DXY n/a")}</div>`; return; }
    if (d.bars) dxySeries.setData(d.bars);
    dxyLines.forEach((l) => dxySeries.removePriceLine(l)); dxyLines = [];
    (d.resistance || []).forEach((p) => dxyLines.push(dxySeries.createPriceLine({ price: p, color: "#ff3b3b", lineStyle: 2, lineWidth: 1, title: "R" })));
    (d.support || []).forEach((p) => dxyLines.push(dxySeries.createPriceLine({ price: p, color: "#39ff14", lineStyle: 2, lineWidth: 1, title: "S" })));
    if (U.isNum(d.vwap)) dxyLines.push(dxySeries.createPriceLine({ price: d.vwap, color: "#00e5ff", lineWidth: 1, title: d.vwap_basis || "VWAP" }));
    const corr = Object.entries(d.correlations || {}).map(([s, c]) => `<div class="mono" style="font-size:11px;display:grid;grid-template-columns:62px 1fr 48px;gap:6px;align-items:center">
      <span>${s}</span><div class="gauge"><i style="left:${U.isNum(c) ? ((c + 1) / 2) * 100 : 50}%"></i></div><span class="${U.isNum(c) && Math.abs(c) < 0.3 ? "yellow" : ""}">${U.isNum(c) ? c.toFixed(2) : "n/a"}</span></div>`).join("");
    const flags = [["bias", d.bias], ["M15", d.trend_m15], ["H1", d.trend_h1], ["vs VWAP", d.below_vwap === true ? "below" : d.below_vwap === false ? "above" : "n/a"],
      ["rej R", d.rejecting_resistance ? "yes" : "no"], ["rej S", d.rejecting_support ? "yes" : "no"], ["BOS↑", d.breaking_up ? "yes" : "no"], ["BOS↓", d.breaking_down ? "yes" : "no"]];
    const div = Object.entries(d.divergence || {}).map(([s, t]) => `<span class="chip ${t === "bullish" ? "TAKEN" : "BLOCKED"}">${s} ${t} div</span>`).join(" ");
    $("dxyInfo").innerHTML = `<div class="chips">${flags.map(([k, v]) => `<span class="chip" style="border-color:var(--line)">${k}: <b class="${v === "bull" || v === "yes" ? "cyan" : v === "bear" ? "magenta" : ""}">${v}</b></span>`).join("")}</div>
      <div style="margin-top:6px">${corr || '<div class="empty">correlations n/a</div>'}</div>${div ? `<div style="margin-top:6px">${div}</div>` : ""}
      ${(d.decoupled || []).length ? `<div class="yellow mono" style="font-size:11px;margin-top:4px">DXY decoupled: ${d.decoupled.join(", ")} (weight reduced)</div>` : ""}
      ${d.shock ? `<div class="red mono" style="font-size:11px">DXY SHOCK ${d.shock.direction} ${d.shock.atr_mult}×ATR</div>` : ""}`;
  }

  // ------------------------------------------------------------------ trade manager
  function renderTM() {
    const tm = S.trade_manager || {};
    const fired = tm.fired || {};
    const rules = Object.entries(tm.rules || {}).sort((a, b) => a[1].priority - b[1].priority);
    const tradeRows = S.open_trades.map((t) => `<tr><td>${t.symbol} <span class="${t.direction === "long" ? "green" : "red"}">${t.direction === "long" ? "▲" : "▼"}</span></td>
      <td>${t.account}</td><td class="${U.cls(t.r_now)}">${U.r(t.r_now)}</td><td class="mono">${U.esc((t.eta || {}).text || "")}</td>
      <td>${t.tp1_done ? '<span class="chip TAKEN">TP1</span>' : ""}${t.tp2_done ? ' <span class="chip TAKEN">TP2</span>' : ""}</td></tr>`).join("");
    const early = events.filter((e) => e.tag === "EARLY EXIT").slice(-5).reverse().map((e) => `<div class="row orange">${U.hhmmss(e.ts)} ${U.esc(e.message)}</div>`).join("");
    $("tm").innerHTML = `<table class="tbl"><tr><th>OPEN</th><th>ACC</th><th>R</th><th>ETA</th><th></th></tr>${tradeRows || '<tr><td colspan="5" class="empty">no open positions</td></tr>'}</table>
      <div class="chips" style="margin:8px 0">${rules.map(([k, v]) => `<span class="chip" style="border-color:${v.enabled ? "var(--purple)" : "var(--mute)"};color:${v.enabled ? "var(--purple)" : "var(--mute)"}">${k} ${fired[k] || 0}</span>`).join("")}
      <span class="chip" style="border-color:var(--yellow);color:var(--yellow)">time T1 ${fired.time_t1 || 0}</span><span class="chip" style="border-color:var(--yellow);color:var(--yellow)">time T2 ${fired.time_t2 || 0}</span>
      <span class="chip" style="border-color:${(tm.time_targets || {}).enabled ? "var(--green)" : "var(--mute)"}">time targets ${(tm.time_targets || {}).enabled ? "ON" : "OFF"}</span></div>
      <div class="log" style="height:auto;max-height:110px">${early || '<div class="empty">no early exits yet</div>'}</div>`;
  }

  function renderCtx() {
    const sym = $("symSel").value || S.symbols[0];
    const c = S.context[sym] || {}, reg = (S.regime || {})[sym] || {}, intel = S.intel || {};
    const ry = intel.real_yield, cot = intel.cot, dom = (intel.dom || {})[sym], sent = (intel.sentiment || {})[sym];
    const chip = (k, v, col) => `<span class="chip" style="border-color:${col || "var(--line)"};color:${col || "var(--txt)"}">${k}: ${v}</span>`;
    const news = (S.news || {});
    const nx = (news.next || []).filter((e) => e.ts > S.now - 1800).slice(0, 3).map((e) => `${U.hhmmss(e.ts)} ${e.country} ${e.title}`).join(" · ");
    $("ctxChips").innerHTML = [
      chip("regime", `${reg.regime || "n/a"} (${U.isNum(reg.confidence) ? reg.confidence : "n/a"})`, "var(--purple)"),
      chip("session", c.session || "n/a", "var(--cyan)"),
      chip("HTF bias", (c.htf_bias || {}).bias || "n/a", "var(--gold)"),
      chip("ATR regime", c.atr_regime || "n/a"),
      chip("ADR used", U.isNum(c.adr_used) ? c.adr_used.toFixed(0) + "%" : "n/a", U.isNum(c.adr_used) && c.adr_used > 85 ? "var(--red)" : null),
      chip("volume", c.has_volume ? "tick-vol est." : "n/a (no volume)", c.has_volume ? "var(--green)" : "var(--mute)"),
      chip("DOM", dom ? `imb ${dom.imbalance}` : "n/a"),
      chip("COT gold", cot && cot !== "n/a" ? `net ${U.fmt(cot.net, 0)} · ${cot.percentile}pct` : "n/a", "var(--yellow)"),
      chip("real 10Y", ry && ry !== "n/a" ? `${ry.value}% ${ry.trend}` : "n/a", "var(--orange)"),
      chip("sentiment", sent ? `${sent.long_pct.toFixed(0)}% long` : "n/a"),
      chip("news", news.status || "n/a", news.status === "ok" ? "var(--green)" : "var(--yellow)"),
      nx ? `<div class="mono dim" style="font-size:11px;width:100%">next: ${U.esc(nx)}</div>` : "",
    ].join("");
  }

  // ------------------------------------------------------------------ desks
  function buildDesks() {
    const box = $("desks"), tabs = $("botTabs");
    box.innerHTML = DESK_ORDER.map((n) => `<div class="desk" data-bot="${n}"><div class="nm">${n}</div><div class="cnt"></div>
      <div class="iso"></div><div class="screen"><canvas width="184" height="108"></canvas></div><div class="ghost-slot"></div><div class="st">waiting for first cycle…</div></div>`).join("");
    for (let i = 0; i < 5; i++) { const c = document.createElement("div"); c.className = "coin"; c.style.left = (8 + i * 21) + "%"; c.style.top = (6 + (i % 2) * 70) + "%"; c.style.animationDelay = `${-i * 0.7}s, ${-i * 1.3}s`; document.getElementById("floorDesks").appendChild(c); }
    box.querySelectorAll(".desk").forEach((d) => d.addEventListener("click", () => openBot(d.dataset.bot)));
    tabs.innerHTML = DESK_ORDER.map((n) => `<button data-bot="${n}">${n}</button>`).join("");
    tabs.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => openBot(b.dataset.bot)));
  }
  function renderDesks() {
    (S.bots || []).forEach((b) => {
      const d = document.querySelector(`.desk[data-bot="${b.name}"]`); if (!d) return;
      d.style.setProperty("--c", b.color);
      const tab = document.querySelector(`#botTabs button[data-bot="${b.name}"]`); if (tab) tab.style.setProperty("--c", b.color);
      if (!d.querySelector(".ghost")) d.querySelector(".ghost-slot").outerHTML = GHOST(b.color);
      d.querySelector(".st").textContent = b.status || "idle";
      const cnt = Object.entries(b.counters || {}).slice(0, 2).map(([k, v]) => `${k} ${v}`).join(" · ");
      d.querySelector(".cnt").textContent = cnt;
      spark(d.querySelector("canvas"), b.activity || [], b.color);
    });
  }
  function spark(cv, vals, color) {
    const g = cv.getContext("2d"), W = cv.width, H = cv.height;
    g.clearRect(0, 0, W, H);
    g.strokeStyle = "rgba(110,190,255,.12)"; for (let y = 10; y < H; y += 18) { g.beginPath(); g.moveTo(0, y); g.lineTo(W, y); g.stroke(); }
    if (vals.length < 2) { g.fillStyle = color; g.font = "12px ShareTechMono"; g.fillText("…", W / 2 - 4, H / 2); return; }
    const mn = Math.min(...vals), mx = Math.max(...vals), rg = mx - mn || 1;
    g.strokeStyle = color; g.lineWidth = 2; g.shadowColor = color; g.shadowBlur = 6; g.beginPath();
    vals.forEach((v, i) => { const x = (i / (vals.length - 1)) * (W - 6) + 3, y = H - 6 - ((v - mn) / rg) * (H - 12); i ? g.lineTo(x, y) : g.moveTo(x, y); });
    g.stroke(); g.shadowBlur = 0;
  }
  async function openBot(name) {
    try {
      const b = await U.api("/api/bot/" + encodeURIComponent(name));
      const rows = (b.log || []).slice(-80).reverse().map((e) => `<div class="row"><span class="t">${U.hhmmss(e.ts)}</span><span class="tag tag-${String(e.tag).replace(/\s/g, "")}">${U.esc(e.tag)}</span>${U.esc(e.message)}</div>`).join("");
      $("modalBody").innerHTML = `<h3 style="color:${b.color}">${b.name} <span class="sub">${U.esc(b.job)}</span></h3>
        <div class="kv"><span class="k">STATUS</span><span class="v">${U.esc(b.status)}</span><span class="k">UPDATED</span><span class="v">${U.dt(b.status_ts)}</span></div>
        <h3 style="margin-top:12px">STATS</h3><div class="chips">${Object.entries(b.counters || {}).map(([k, v]) => `<span class="chip" style="border-color:${b.color}">${k}: ${v}</span>`).join("") || '<span class="empty">no events yet</span>'}</div>
        <h3 style="margin-top:12px">CONFIG</h3><pre class="cfg">${U.esc(JSON.stringify(b.config, null, 2))}</pre>
        <h3>LOG</h3><div class="log" style="height:300px">${rows || '<div class="empty">no log lines yet</div>'}</div>`;
      $("modal").classList.add("open");
    } catch (e) { U.toast("bot details unavailable"); }
  }

  // ------------------------------------------------------------------ equity
  function initEquity() {
    eqChart = LightweightCharts.createChart($("equity"), { autoSize: true, layout: { background: { type: "solid", color: "transparent" }, textColor: "#7f9cc0", fontFamily: "ShareTechMono" },
      grid: { vertLines: { visible: false }, horzLines: { color: "rgba(60,110,180,.08)" } }, timeScale: { timeVisible: true, borderColor: "rgba(110,190,255,.2)" }, rightPriceScale: { borderColor: "rgba(110,190,255,.2)" } });
    eqSeries = eqChart.addAreaSeries({ lineColor: "#39ff14", topColor: "rgba(57,255,20,.35)", bottomColor: "rgba(57,255,20,0)", lineWidth: 2, lastValueVisible: true });
  }
  async function loadEquity() {
    if (!S) return;
    let acc = account;
    if (acc === "ALL") {
      const real = S.accounts.find((a) => a.real && a.real.status === "OK");
      acc = real ? real.name : (S.accounts[0] ? S.accounts[0].name + "#paper" : null);
    } else {
      const a = S.accounts.find((x) => x.name === acc);
      if (a && !(a.real && a.real.balance != null)) acc = acc + "#paper";
    }
    if (!acc) return;
    try {
      const rows = await U.api("/api/equity?account=" + encodeURIComponent(acc));
      const pts = []; let last = 0;
      rows.forEach((r) => { const t = Math.floor(r.ts); if (t > last && U.isNum(r.equity)) { pts.push({ time: t, value: r.equity }); last = t; } });
      eqSeries.setData(pts); eqChart.timeScale().fitContent();
      $("eqEmpty").style.display = pts.length ? "none" : "block";
      $("eqLabel").textContent = `${acc.replace("#paper", " · PAPER")} · ${rows.length ? rows[0].currency : ""} · SQLite snapshots`;
    } catch (e) {}
  }

  // ------------------------------------------------------------------ accounts view
  function fig(k, v, cls) { return `<div class="fig"><div class="k">${k}</div><div class="v ${cls || ""}">${v}</div></div>`; }
  function posTable(ps, sym) {
    if (!ps || !ps.length) return '<div class="empty">no open positions</div>';
    return `<div class="scroll-x"><table class="tbl"><tr><th>SYM</th><th>SIDE</th><th>LOTS</th><th>OPEN</th><th>NOW</th><th>SL</th><th>TP</th><th>P/L</th><th>R</th><th>TAG</th></tr>` +
      ps.map((p) => `<tr><td>${p.symbol}</td><td class="${p.side === "buy" ? "green" : "red"}">${p.side}</td><td>${p.volume}</td><td>${U.px(p.price_open, p.symbol)}</td>
      <td>${U.px(p.price_current, p.symbol)}</td><td>${p.sl ? U.px(p.sl, p.symbol) : "—"}</td><td>${p.tp ? U.px(p.tp, p.symbol) : "—"}</td>
      <td class="${U.cls(p.profit)}">${U.signed(p.profit)}</td><td>${U.r(p.r)}</td><td><span class="chip" style="border-color:var(--line)">${p.tag}</span></td></tr>`).join("") + `</table></div>`;
  }
  function renderAccounts() {
    const t = S.totals || {}, pt = S.paper_totals || {};
    $("totals").innerHTML = (Object.keys(t).length ? Object.entries(t).map(([c, v]) => `<div class="mono">${c}: balance ${U.money(v.balance)} · equity ${U.money(v.equity)} · floating <span class="${U.cls(v.profit)}">${U.signed(v.profit)}</span> · today <span class="${U.cls(v.realized_today)}">${U.signed(v.realized_today)}</span> (${v.accounts} acc.)</div>`).join("")
      : '<div class="empty">no MT5 account connected - no real totals (NO DATA)</div>') +
      Object.entries(pt).map(([c, v]) => `<div class="mono cyan">PAPER ${c}: balance ${U.money(v.balance)} · equity ${U.money(v.equity)} · P/L since start ${U.signed(v.balance - v.start_balance)}</div>`).join("");
    $("accCards").innerHTML = S.accounts.map((a) => {
      const r = a.real, p = a.paper;
      const tmode = r && r.trade_mode ? `<span class="badge ${r.trade_mode === "REAL" ? "real" : "demo"}">${r.trade_mode}</span>` : "";
      let realHtml = "";
      if (r) {
        const st = r.status || "NO DATA", grey = st !== "OK";
        realHtml = `<div class="${grey ? "state-STALE" : ""}"><div class="mono dim" style="font-size:11px">${U.esc(r.holder || "")} · login ${r.login ?? "n/a"} · ${U.esc(r.server || "")} · ${U.esc(r.company || "")} · 1:${r.leverage ?? "n/a"} · ${r.currency || ""}</div>
          <div class="figs">${fig("BALANCE", U.money(r.balance))}${fig("EQUITY", U.money(r.equity))}${fig("FLOATING P/L", U.signed(r.profit), U.cls(r.profit))}
          ${fig("MARGIN", U.money(r.margin))}${fig("FREE MARGIN", U.money(r.margin_free))}${fig("MARGIN LEVEL", U.isNum(r.margin_level) ? r.margin_level.toFixed(0) + "%" : "n/a")}
          ${fig("TODAY REALIZED", U.signed(r.realized_today), U.cls(r.realized_today))}${fig("STATUS", st, st === "OK" ? "green" : "red")}${fig("LAST SYNC", r.last_ok ? U.hhmmss(r.last_ok) : "n/a")}</div>
          ${st !== "OK" ? `<div class="mono red" style="font-size:11px">${U.esc(st)}${r.error ? " · " + U.esc(r.error) : ""}${a.next_retry ? " · retry " + U.hhmmss(a.next_retry) : ""}</div>` : ""}
          ${posTable(r.positions)}</div>`;
      }
      const paperHtml = p ? `<div class="paperbox"><div class="mono cyan" style="font-size:11px">PAPER (simulated fills on ${r && r.status === "OK" ? "this terminal's" : "analysis-feed"} prices)</div>
        <div class="figs">${fig("PAPER BALANCE", U.money(p.balance, p.currency))}${fig("PAPER EQUITY", U.money(p.equity))}${fig("PAPER TODAY", U.signed(p.realized_today), U.cls(p.realized_today))}</div>${posTable(p.positions)}</div>` : "";
      return `<div class="panel acc"><h3>${U.esc(a.name)} <span class="badge ${a.mode === "live" ? "live" : "paper"}">${a.mode.toUpperCase()}</span>${tmode}${a.paused ? '<span class="badge" style="color:var(--yellow)">PAUSED</span>' : ""}
        <span class="sub">${U.esc(a.live_reason || "")} · risk ${a.risk_pct}%</span></h3>
        <div style="display:flex;gap:6px;margin-bottom:6px"><button class="btn small ${a.paused ? "ok" : ""}" data-acc="${U.esc(a.name)}" data-p="${a.paused ? 0 : 1}">${a.paused ? "RESUME" : "PAUSE"}</button></div>
        ${realHtml}${paperHtml}</div>`;
    }).join("");
    $("accCards").querySelectorAll("button[data-acc]").forEach((b) => b.addEventListener("click", async () => {
      await U.post("/api/control/pause", { account: b.dataset.acc, paused: b.dataset.p === "1" }); U.toast(`${b.dataset.acc}: ${b.dataset.p === "1" ? "paused" : "resumed"}`);
    }));
  }

  // ------------------------------------------------------------------ strategy / analyst view
  async function renderStrategy() {
    const st = S.strategy || {};
    $("strategy").innerHTML = Object.values(st).map((s) => {
      const props = (s.proposals || []).map((p) => `<div class="mono" style="font-size:11px;border:1px dashed var(--magenta);border-radius:5px;padding:5px;margin-top:4px">
        #${p.id} ${U.esc(p.source)}: ${U.esc(p.reason)}<br>${U.esc(JSON.stringify(p.changes))}
        <div style="margin-top:4px"><button class="btn small ok" data-apply="${p.id}">APPLY</button> <button class="btn small" data-reject="${p.id}">REJECT</button></div></div>`).join("");
      const oos = s.oos || {};
      return `<div style="border-bottom:1px solid rgba(110,190,255,.12);padding:8px 0"><div style="display:flex;gap:8px;align-items:center">
        <b class="gold" style="font-family:var(--head);letter-spacing:.1em">${s.symbol}</b><span class="badge">v${s.version}</span>${s.locked ? '<span class="badge" style="color:var(--yellow)">LOCKED</span>' : ""}
        <span class="badge" style="color:var(--purple)">regime ${s.regime || "n/a"}</span><span class="spacer"></span>
        <button class="btn small" data-lock="${s.symbol}" data-l="${s.locked ? 0 : 1}">${s.locked ? "UNLOCK" : "LOCK"}</button></div>
        <div class="mono dim" style="font-size:11px;margin:4px 0">${U.esc(s.notes)}</div>
        <div class="chips">${s.active_playbooks.map((p) => `<span class="chip" style="border-color:${s.regime_prefer.includes(p) ? "var(--green)" : s.regime_avoid.includes(p) ? "var(--red)" : "var(--line)"}">${p} ×${s.weights[p]}</span>`).join("")}</div>
        <div class="mono" style="font-size:11px;margin-top:4px">TF ${s.entry_timeframes.join("/")} · sessions ${s.sessions.join(", ")} · stop ${s.stops.style} +${s.stops.atr_buffer_mult}ATR · TP1 ${s.targets.tp1_rr}R (${s.targets.tp1_fraction * 100}%) · trail ${s.targets.trail} · T1×${s.time_targets.t1_mult} T2×${s.time_targets.t2_mult}</div>
        <div class="mono dim" style="font-size:11px">OOS: ${oos.trades ? `${oos.trades} trades · exp ${U.r(oos.expectancy_r)} · WR ${oos.win_rate}%` : "no walk-forward run yet (mt5sys optimize)"}</div>${props}</div>`;
    }).join("");
    $("strategy").querySelectorAll("[data-apply]").forEach((b) => b.addEventListener("click", async () => { const r = await U.post("/api/strategy/apply", { id: +b.dataset.apply }); U.toast("apply: " + r.status); }));
    $("strategy").querySelectorAll("[data-reject]").forEach((b) => b.addEventListener("click", async () => { const r = await U.post("/api/strategy/reject", { id: +b.dataset.reject }); U.toast("reject: " + r.status); }));
    $("strategy").querySelectorAll("[data-lock]").forEach((b) => b.addEventListener("click", async () => { await U.post("/api/strategy/lock", { symbol: b.dataset.lock, locked: b.dataset.l === "1" }); U.toast("profile updated"); }));
    const fr = (S.analyst || {}).frequency || {};
    $("freq").innerHTML = Object.entries(fr).map(([s, f]) => `<div class="mono" style="font-size:12px;display:grid;grid-template-columns:70px 1fr 120px;gap:8px;align-items:center;margin:3px 0">
      <span>${s}</span><span class="scorebar" style="width:100%"><i style="width:${Math.min(100, (f.avg_trades_per_day / f.target) * 100)}%"></i></span><span>${f.avg_trades_per_day}/day · target ${f.target}</span></div>`).join("") +
      ((S.analyst || {}).warnings || []).map((w) => `<div class="yellow mono" style="font-size:11px">⚠ ${U.esc(w)}</div>`).join("") +
      ((S.analyst || {}).suggestions || []).map((w) => `<div class="dim mono" style="font-size:11px">→ ${U.esc(w)}</div>`).join("") || '<div class="empty">no data yet</div>';
    try {
      const rep = await U.api("/api/analyst");
      const sym = $("symSel").value || S.symbols[0];
      $("heatSym").textContent = sym + " · shrunk expectancy by weekday × hour (UTC)";
      const hm = (rep.heatmaps || {})[sym];
      const days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
      $("heat").innerHTML = hm ? `<div class="heat"><div class="lab"></div>${Array.from({ length: 24 }, (_, h) => `<div class="lab">${h % 3 ? "" : h}</div>`).join("")}
        ${hm.map((row, d) => `<div class="lab">${days[d]}</div>` + row.map((c) => { const v = c ? c.est : null; const a = v == null ? 0.04 : Math.min(0.9, Math.abs(v) * 1.2 + 0.15);
          return `<div title="${c ? `${days[d]}: exp ${v} (n=${c.n})` : "no trades"}" style="background:${v == null ? "rgba(255,255,255,.04)" : v >= 0 ? `rgba(57,255,20,${a})` : `rgba(255,59,59,${a})`}"></div>`; }).join("")).join("")}</div>`
        : '<div class="empty">no closed trades yet for this symbol - the heatmap fills as the journal grows</div>';
      const tbl = (title, obj) => `<h3 style="margin-top:8px">${title}</h3><table class="tbl"><tr><th>KEY</th><th>N</th><th>WR</th><th>EXP</th><th>PF</th></tr>${Object.entries(obj || {}).map(([k, v]) => `<tr><td>${U.esc(k)}</td><td>${v.trades}</td><td>${v.win_rate ?? "n/a"}</td><td class="${U.cls(v.expectancy_r)}">${U.r(v.expectancy_r)}</td><td>${v.profit_factor ?? "n/a"}</td></tr>`).join("") || '<tr><td colspan="5" class="empty">no trades</td></tr>'}</table>`;
      const sa = rep.shadow_audit || {};
      $("analyst").innerHTML = tbl("BY SETUP", rep.by_setup) + tbl("BY SESSION", rep.by_session) + tbl("BY EXIT TYPE", rep.by_exit_type) +
        `<h3 style="margin-top:8px">EARLY-EXIT AUDIT vs SHADOW</h3>${Object.keys(sa).length ? Object.entries(sa).map(([k, v]) => `<div class="mono" style="font-size:12px">${k}: n=${v.n} · avg saved ${U.r(v.avg_saved_r)} · helped ${v.helped_pct}%</div>`).join("") : '<div class="empty">no closed shadows yet</div>'}`;
    } catch (e) {}
  }

  // ------------------------------------------------------------------ controls
  async function kill() {
    const on = !(S && S.kill_switch);
    if (on && !confirm("ACTIVATE GLOBAL KILL SWITCH?\nNo new trades on any account; pending orders are cancelled.\nOpen positions stay managed.")) return;
    const r = await U.post("/api/control/kill", { on }); U.toast(r.kill_switch ? "KILL SWITCH ACTIVE" : "kill switch released");
  }
  async function pauseAll() {
    const pause = $("pauseAllBtn").textContent.startsWith("PAUSE");
    await U.post("/api/control/pause", { account: "*", paused: pause }); U.toast(pause ? "all accounts paused" : "all accounts resumed");
  }
  async function showQR() {
    try {
      const r = await U.api("/api/lan");
      $("modalBody").innerHTML = `<h3>PHONE / LAN ACCESS</h3><div style="display:flex;gap:16px;flex-wrap:wrap;align-items:center"><img src="${r.qr}" alt="QR" style="width:220px;height:220px;border-radius:8px;background:#fff;padding:6px">
        <div class="mono"><div class="cyan" style="font-size:16px">${U.esc(r.url)}</div><div class="dim" style="margin-top:8px;font-size:12px">Same Wi-Fi only. Log in with your dashboard user.<br>Outside your home: use Tailscale or a Cloudflare Tunnel (HTTPS) - never port forwarding.</div></div></div>`;
      $("modal").classList.add("open");
    } catch (e) { U.toast("LAN info unavailable"); }
  }

  boot();
})();
