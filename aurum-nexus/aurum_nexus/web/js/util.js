/* AURUM NEXUS - shared helpers. Every number shown comes from the backend; missing data renders as n/a. */
const U = (() => {
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const isNum = (x) => typeof x === "number" && isFinite(x);
  const fmt = (x, d = 2) => (isNum(x) ? x.toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d }) : "n/a");
  const money = (x, ccy = "") => (isNum(x) ? `${fmt(x, 2)}${ccy ? " " + ccy : ""}` : "n/a");
  const signed = (x, d = 2) => (isNum(x) ? (x >= 0 ? "+" : "") + fmt(x, d) : "n/a");
  const r = (x) => (isNum(x) ? (x >= 0 ? "+" : "") + x.toFixed(2) + "R" : "—");
  const px = (x, sym) => {
    if (!isNum(x)) return "n/a";
    const d = sym && sym.startsWith("XAU") ? 2 : sym && sym.startsWith("XAG") ? 3 : sym && sym.endsWith("JPY") ? 3 : x > 50 ? 2 : 5;
    return x.toFixed(d);
  };
  const cls = (x) => (isNum(x) ? (x > 0 ? "green" : x < 0 ? "red" : "") : "mute");
  const hhmmss = (ts) => (ts ? new Date(ts * 1000).toISOString().substring(11, 19) : "--:--:--");
  const dt = (ts) => (ts ? new Date(ts * 1000).toISOString().replace("T", " ").substring(0, 19) : "n/a");
  const dur = (s) => {
    if (!isNum(s)) return "--";
    const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60), sec = Math.floor(s % 60);
    return (d ? d + "d " : "") + String(h).padStart(2, "0") + ":" + String(m).padStart(2, "0") + ":" + String(sec).padStart(2, "0");
  };
  const ago = (ts, now) => (ts ? Math.max(0, (now || Date.now() / 1000) - ts).toFixed(1) + "s ago" : "n/a");

  // animated count-up: keeps the last value per element
  const anim = new Map();
  function countTo(el, value, formatter) {
    if (!el) return;
    if (!isNum(value)) { el.textContent = formatter(value); anim.delete(el); return; }
    const from = anim.has(el) ? anim.get(el) : value;
    anim.set(el, value);
    if (from === value) { el.textContent = formatter(value); return; }
    const t0 = performance.now(), D = 650;
    const step = (t) => {
      const k = Math.min(1, (t - t0) / D), e = 1 - Math.pow(1 - k, 3);
      el.textContent = formatter(from + (value - from) * e);
      if (k < 1 && anim.get(el) === value) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }

  async function api(path) {
    const r = await fetch(path, { credentials: "same-origin" });
    if (r.status === 401) { location.href = "/login"; throw new Error("auth"); }
    if (!r.ok) throw new Error(await r.text());
    return r.json();
  }
  async function post(path, body) {
    const r = await fetch(path, { method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-Requested-With": "AurumNexus" }, body: JSON.stringify(body || {}) });
    if (r.status === 401) { location.href = "/login"; throw new Error("auth"); }
    return r.json();
  }
  function toast(msg, ms = 2600) {
    const t = document.getElementById("toast");
    t.textContent = msg; t.style.display = "block";
    clearTimeout(t._h); t._h = setTimeout(() => (t.style.display = "none"), ms);
  }
  const gradeCls = (g) => "g-" + String(g || "C").replace("+", "plus");
  return { esc, isNum, fmt, money, signed, r, px, cls, hhmmss, dt, dur, ago, countTo, api, post, toast, gradeCls };
})();
