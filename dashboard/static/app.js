const $ = (id) => document.getElementById(id);
const NS = "http://www.w3.org/2000/svg";

const fmtR = (x, d = 2) => (x === null || x === undefined ? "—" : `${x > 0 ? "+" : x < 0 ? "−" : ""}${Math.abs(x).toFixed(d)} R`);
const START_BALANCE = 10000;
const RISK_USD = 100;
const usd = (x, d = 0) => `$${Math.abs(x).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d })}`;
const fmtUsd = (r, d = 0) => (r === null || r === undefined ? "—" : `${r > 0 ? "+" : r < 0 ? "−" : ""}${usd(r * RISK_USD, d)}`);
const fmtPct = (x, d = 1) => (x === null || x === undefined ? "—" : `${(x * 100).toFixed(d)}%`);
const fmtPrice = (x) => (x === null || x === undefined ? "—" : `$${x.toLocaleString(undefined, { maximumFractionDigits: 0 })}`);
const fmtTime = (iso) => (iso ? new Date(iso).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "—");
const ago = (iso) => {
  if (!iso) return "never";
  const m = (Date.now() - new Date(iso).getTime()) / 60000;
  if (m < 1) return "just now";
  if (m < 60) return `${Math.round(m)} min ago`;
  if (m < 48 * 60) return `${(m / 60).toFixed(1)} h ago`;
  return `${Math.round(m / 1440)} d ago`;
};
const minutesSince = (iso) => (iso ? (Date.now() - new Date(iso).getTime()) / 60000 : Infinity);

function setText(id, text, cls) {
  const el = $(id);
  el.textContent = text;
  el.classList.remove("pos", "neg");
  if (cls) el.classList.add(cls);
}
const sign = (x) => (x > 0 ? "pos" : x < 0 ? "neg" : null);

function el(tag, attrs = {}, text) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  if (text !== undefined) node.textContent = text;
  return node;
}
function svg(tag, attrs = {}) {
  const node = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  return node;
}

function renderHeader(s) {
  const minutes = minutesSince(s.last_run.run_at);
  const pill = $("status-pill");
  const ok = minutes < 75 && Object.values(s.last_run.steps || {}).every((v) => v === "ok");
  pill.className = `pill ${ok ? "ok" : "warn"}`;
  $("status-text").textContent = ok ? "Live" : "Delayed";
  $("updated").textContent = `Updated ${new Date(s.generated_at).toLocaleTimeString()}`;
  $("next-run").textContent = `Next run ${new Date(s.next_run).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
  const m = s.model;
  $("footer").textContent = `Model ${m.version ?? "—"} · threshold ${m.threshold ? m.threshold.toFixed(4) : "—"} · forward test since ${fmtTime(s.forward_test.start)} · paper trading only, no orders are placed`;
}

function renderHero(t) {
  setText("hero-net", fmtUsd(t.net_R), sign(t.net_R));
  const pct = `${t.return_pct >= 0 ? "+" : "−"}${Math.abs(t.return_pct).toFixed(1)}%`;
  $("hero-sub").textContent = `${pct} on a ${usd(START_BALANCE)} practice account, now worth ${usd(START_BALANCE + t.net_R * RISK_USD)} · ${t.closed} finished trade${t.closed === 1 ? "" : "s"}, after fees`;
  setText("hero-maker", fmtUsd(t.net_R_maker), sign(t.net_R_maker));
  setText("hero-mean", fmtUsd(t.mean_R), sign(t.mean_R));
  $("hero-bw").textContent = t.closed ? `${fmtUsd(t.best_R)} / ${fmtUsd(t.worst_R)}` : "—";
  $("hero-r").textContent = t.closed ? `In risk units: ${fmtR(t.net_R)} (1 R = the ${usd(RISK_USD)} put at risk per trade)` : "";
}

function renderKpis(t, f) {
  $("kpi-winrate").textContent = fmtPct(t.win_rate);
  $("kpi-wl").textContent = t.closed ? `${t.wins} of ${t.closed} trades made money` : "no finished trades yet";
  setText("kpi-won", fmtUsd(t.won_R), t.won_R > 0 ? "pos" : null);
  $("kpi-won-sub").textContent = `from ${t.wins} winning trade${t.wins === 1 ? "" : "s"}`;
  setText("kpi-lost", fmtUsd(t.lost_R), t.lost_R < 0 ? "neg" : null);
  $("kpi-lost-sub").textContent = `from ${t.losses} losing trade${t.losses === 1 ? "" : "s"}`;
  $("kpi-pf").textContent = t.profit_factor === null ? "—" : `$${t.profit_factor.toFixed(2)}`;
  setText("kpi-dd", t.max_drawdown_R ? fmtUsd(-t.max_drawdown_R) : "$0", t.max_drawdown_R ? "neg" : null);
  $("kpi-closed").textContent = `${t.closed}`;
  $("kpi-closed-sub").textContent = `of the ${f.trades_target} needed before judging`;
}

let openPos = null;
let livePrice = null;

function renderLive() {
  const tick = $("live-price");
  tick.textContent = livePrice === null ? "BTC —" : `BTC ${fmtPrice(livePrice)}`;
  const p = openPos;
  if (!p || livePrice === null || !$("pos-now")) return;
  const unit = Math.abs(p.entry_price - p.sl);
  const r = unit ? ((p.side === "long" ? 1 : -1) * (livePrice - p.entry_price)) / unit : null;
  setText("pos-now", fmtPrice(livePrice));
  setText("pos-pnl", fmtUsd(r), sign(r));
  const mark = $("pos-mark");
  if (mark) mark.style.left = `${Math.min(100, Math.max(0, ((livePrice - p.sl) / (p.tp - p.sl)) * 100))}%`;
}

function connectPrice(delay = 1000) {
  let ws;
  try { ws = new WebSocket("wss://fstream.binance.com/market/ws/btcusdt@miniTicker"); } catch (_) { return; }
  ws.onopen = () => { delay = 1000; $("live-price").classList.add("on"); };
  ws.onmessage = (ev) => {
    const c = parseFloat(JSON.parse(ev.data).c);
    if (Number.isFinite(c)) { livePrice = c; renderLive(); }
  };
  ws.onclose = () => {
    $("live-price").classList.remove("on");
    setTimeout(() => connectPrice(Math.min(delay * 2, 30000)), delay);
  };
}

function renderPosition(p) {
  openPos = p;
  const body = $("position-body");
  body.replaceChildren();
  const badge = $("pos-side");
  if (!p) {
    badge.textContent = "No trade";
    body.append(el("p", { class: "empty" }, "No trade open right now. The system checks again every hour."));
    return;
  }
  badge.textContent = p.side === "long" ? "▲ Betting price goes up" : "▼ Betting price goes down";
  const grid = el("div", { class: "pos-grid" });
  const cell = (label, value, cls, id) => {
    const d = el("div");
    d.append(el("span", { class: "label" }, label), el("span", { class: `value ${cls || ""}`, ...(id ? { id } : {}) }, value));
    return d;
  };
  grid.append(
    cell("Bitcoin price at entry", fmtPrice(p.entry_price)),
    cell("Bitcoin price now", fmtPrice(p.last_price), "", "pos-now"),
    cell("Profit / loss so far (before fees)", fmtUsd(p.unrealized_gross_R), sign(p.unrealized_gross_R), "pos-pnl"),
    cell("Closes by", fmtTime(p.horizon_end)),
  );
  const lo = p.sl, hi = p.tp;
  const pos = (x) => `${Math.min(100, Math.max(0, ((x - lo) / (hi - lo)) * 100))}%`;
  const range = el("div", { class: "range", role: "img", "aria-label": `Price between stop-loss ${fmtPrice(p.sl)} and target ${fmtPrice(p.tp)}` });
  const entry = el("span", { class: "mark entry", style: `left:${pos(p.entry_price)}` });
  const entryTag = el("span", { class: "tag", style: `left:${pos(p.entry_price)}` }, "entry");
  range.append(entry, entryTag);
  if (p.last_price) range.append(el("span", { class: "mark now", id: "pos-mark", style: `left:${pos(p.last_price)}`, title: "now" }));
  const ends = el("div", { class: "range-ends" });
  ends.append(el("span", {}, `Stop-loss ${fmtPrice(p.sl)} (−${usd(RISK_USD)})`), el("span", {}, `Target ${fmtPrice(p.tp)}`));
  body.append(grid, range, ends);
  renderLive();
}

function renderProgress(f) {
  const dayPct = Math.min(100, (f.days_elapsed / f.days_total) * 100);
  $("prog-days").style.width = `${dayPct}%`;
  $("prog-days-text").textContent = `${Math.floor(f.days_elapsed)} of ${Math.round(f.days_total)} days`;
  $("prog-trades").style.width = `${Math.min(100, (f.trades_closed / f.trades_target) * 100)}%`;
  $("prog-trades-text").textContent = `${f.trades_closed} of ${f.trades_target} trades`;
}

function niceTicks(lo, hi) {
  const span = hi - lo || 1;
  const raw = span / 4;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((k) => k * mag).find((k) => k >= raw);
  const out = [];
  for (let v = Math.floor(lo / step) * step; v <= Math.ceil(hi / step) * step + 1e-9; v += step) out.push(+v.toFixed(6));
  return out;
}

function renderEquity(s) {
  const box = $("equity");
  const tip = $("tooltip");
  tip.style.opacity = 0;
  box.replaceChildren();
  const pts = [{ t: s.forward_test.start, equity_R: 0, net_R: null }, ...s.trades.equity];
  if (pts.length < 2) {
    const p = s.open_position;
    box.append(el("div", { class: "empty-chart" }, p ? `No closed trades yet. The open ${p.side} settles by ${fmtTime(p.horizon_end)}.` : "No closed trades yet."));
    $("equity-note").textContent = "";
    return;
  }
  $("equity-note").textContent = `started at ${usd(START_BALANCE)}`;
  const W = box.clientWidth, H = box.clientHeight, m = { l: 62, r: 12, t: 12, b: 26 };
  const xs = pts.map((p) => new Date(p.t).getTime());
  const ys = pts.map((p) => START_BALANCE + p.equity_R * RISK_USD);
  const x0 = Math.min(...xs), x1 = Math.max(...xs, x0 + 3600e3);
  const ticks = niceTicks(Math.min(START_BALANCE, ...ys), Math.max(START_BALANCE, ...ys));
  const y0 = ticks[0], y1 = ticks.at(-1);
  const X = (x) => m.l + ((x - x0) / (x1 - x0)) * (W - m.l - m.r);
  const Y = (y) => m.t + (1 - (y - y0) / (y1 - y0)) * (H - m.t - m.b);
  const root = svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Practice account balance over the forward test" });
  const axis = svg("g", { class: "axis" });
  for (const v of ticks) {
    axis.append(svg("line", { class: "gridline", x1: m.l, x2: W - m.r, y1: Y(v), y2: Y(v) }));
    const t = svg("text", { x: m.l - 8, y: Y(v) + 4, "text-anchor": "end" });
    t.textContent = usd(v);
    axis.append(t);
  }
  for (const frac of [0, 0.5, 1]) {
    const x = x0 + (x1 - x0) * frac;
    const t = svg("text", { x: X(x), y: H - 6, "text-anchor": frac === 0 ? "start" : frac === 1 ? "end" : "middle" });
    t.textContent = new Date(x).toLocaleDateString(undefined, { month: "short", day: "numeric" });
    axis.append(t);
  }
  root.append(axis, svg("line", { class: "zero", x1: m.l, x2: W - m.r, y1: Y(START_BALANCE), y2: Y(START_BALANCE) }));
  const d = pts.map((p, i) => `${i ? "L" : "M"}${X(xs[i]).toFixed(1)},${Y(ys[i]).toFixed(1)}`).join("");
  root.append(svg("path", { class: "area", d: `${d}L${X(xs.at(-1))},${Y(START_BALANCE)}L${X(xs[0])},${Y(START_BALANCE)}Z` }));
  root.append(svg("path", { class: "line", d }));
  const last = pts.length - 1;
  root.append(svg("circle", { class: "point", cx: X(xs[last]), cy: Y(ys[last]), r: 4 }));
  const cross = svg("line", { class: "crosshair", y1: m.t, y2: H - m.b, visibility: "hidden" });
  const dot = svg("circle", { class: "point", r: 4, visibility: "hidden" });
  root.append(cross, dot);
  const hit = svg("rect", { x: m.l, y: 0, width: W - m.l - m.r, height: H, fill: "transparent" });
  root.append(hit);
  const move = (ev) => {
    const r = root.getBoundingClientRect();
    const px = ((ev.clientX - r.left) / r.width) * W;
    let i = 0;
    for (let k = 1; k < xs.length; k++) if (Math.abs(X(xs[k]) - px) < Math.abs(X(xs[i]) - px)) i = k;
    cross.setAttribute("x1", X(xs[i])); cross.setAttribute("x2", X(xs[i])); cross.setAttribute("visibility", "visible");
    dot.setAttribute("cx", X(xs[i])); dot.setAttribute("cy", Y(ys[i])); dot.setAttribute("visibility", "visible");
    tip.replaceChildren(el("strong", {}, usd(ys[i])), el("span", {}, i === 0 ? "Start" : `${fmtTime(pts[i].t)} · this trade ${fmtUsd(pts[i].net_R)}`));
    tip.style.opacity = 1;
    const px0 = box.offsetLeft + (X(xs[i]) / W) * box.clientWidth;
    const py0 = box.offsetTop + (Y(ys[i]) / H) * box.clientHeight;
    const flip = px0 + 14 + tip.offsetWidth > box.offsetLeft + box.clientWidth;
    tip.style.left = `${flip ? px0 - 14 - tip.offsetWidth : px0 + 14}px`;
    tip.style.top = `${Math.max(box.offsetTop, py0 - tip.offsetHeight - 10)}px`;
  };
  hit.addEventListener("pointermove", move);
  hit.addEventListener("pointerleave", () => { tip.style.opacity = 0; cross.setAttribute("visibility", "hidden"); dot.setAttribute("visibility", "hidden"); });
  box.append(root);
}

function renderCells(dec) {
  const box = $("cells");
  box.replaceChildren();
  const o = dec.outcomes;
  $("decision-summary").textContent = `last ${dec.recent.length} hours`;
  const stats = $("decision-stats");
  stats.replaceChildren();
  const late = o.late + o.stale + dec.hours_missed;
  for (const [label, n] of [["Hours checked", dec.hours_logged], ["Decided to trade", o.trade], ["No good bet", o.no_side],
    ["Not confident enough", o.meta_below], ["Paused for big news", o.blackout], ["Missed (Mac asleep or late)", late]]) {
    const d = el("div");
    d.append(el("span", { class: "label" }, label), el("span", { class: "value" }, `${n}`));
    stats.append(d);
  }
  for (const h of [...dec.recent].reverse()) {
    const warn = h.status === "missed" || /^(late|stale)/.test(h.reason || "");
    const trade = h.status === "long" || h.status === "short";
    const c = el("span", { class: `cell ${trade ? "trade" : warn ? "warn" : ""}`, tabindex: "0" }, trade ? (h.status === "long" ? "U" : "D") : warn ? "!" : "");
    const label = trade ? (h.status === "long" ? "bet price goes up" : "bet price goes down") : h.status === "missed" ? "missed" : "no trade";
    c.title = `${fmtTime(h.t)} · ${label}${h.reason ? ` · ${h.reason}` : ""}${h.meta_p !== null && h.meta_p !== undefined ? ` · confidence ${fmtPct(h.meta_p, 0)}` : ""}`;
    box.append(c);
  }
}

function renderHealth(s) {
  const list = $("health");
  list.replaceChildren();
  const row = (ok, label, detail) => {
    const li = el("li", { class: ok ? "ok" : "warn" });
    li.append(el("span", { class: "dot" }), el("span", {}, label), el("span", { class: "detail" }, `${ok ? "✓" : "!"} ${detail}`));
    list.append(li);
  };
  const steps = s.last_run.steps || {};
  const failed = Object.entries(steps).filter(([, v]) => v !== "ok").map(([k]) => k);
  row(minutesSince(s.last_run.run_at) < 75, "Hourly run", ago(s.last_run.run_at));
  row(!failed.length, "Pipeline steps", failed.length ? `failed: ${failed.join(", ")}` : `${Object.keys(steps).length} ok`);
  const stale = s.last_run.staleness_min || {};
  const worst = Object.entries(stale).filter(([k]) => k.endsWith("_1m")).reduce((a, [, v]) => Math.max(a, v), 0);
  row(worst <= 5, "Market data", `${worst.toFixed(0)} min behind`);
  const rss = s.collectors.rss;
  row(minutesSince(rss.last_run) < 30 && rss.feeds_ok === rss.feeds, "News (RSS)", `${rss.feeds_ok}/${rss.feeds} feeds · ${ago(rss.last_run)}`);
  const liq = s.collectors.liquidations;
  row(minutesSince(liq.last_seen) < 3, "Liquidations", `${liq.events_today} today · ${ago(liq.last_seen)}`);
  const parity = s.parity;
  row(!parity || parity.features_not_all_equal.length === 0, "Parity", parity ? `${parity.decisions_agree} decisions agree` : "first check after the daily ingest");
  row(s.alerts_24h.length === 0, "Alerts (24 h)", `${s.alerts_24h.length}`);
}

function renderTrades(trades) {
  const body = $("trades");
  body.replaceChildren();
  if (!trades.length) {
    const tr = el("tr");
    tr.append(el("td", { colspan: "7", class: "empty" }, "No closed trades yet."));
    body.append(tr);
    return;
  }
  for (const t of trades) {
    const tr = el("tr");
    const win = t.net_R > 0;
    const res = el("span", { class: `result ${win ? "win" : "loss"}` }, win ? "▲ Win" : "▼ Loss");
    const r = el("td", { class: `num ${sign(t.net_R) || ""}`, title: fmtR(t.net_R, 3) }, fmtUsd(t.net_R));
    const resTd = el("td");
    resTd.append(res);
    tr.append(
      el("td", {}, fmtTime(t.decision_time)),
      el("td", {}, t.side === "long" ? "▲ Up" : "▼ Down"),
      el("td", { class: "num" }, fmtPrice(t.entry_price)),
      el("td", { class: "num" }, fmtPrice(t.exit_price)),
      el("td", {}, { tp: "Hit target", sl: "Hit stop-loss", expiry: "Time ran out" }[t.exit_reason] || t.exit_reason),
      r,
      resTd,
    );
    body.append(tr);
  }
}

function renderTestB(a, b) {
  $("b-status").textContent = b.running ? "Running" : "Starts at the next hourly run";
  const body = $("ab-rows");
  body.replaceChildren();
  const ta = a, tb = b.trades;
  const rows = [
    ["Finished trades", `${ta.closed}`, `${tb.closed}`],
    ["Profit / loss after fees", fmtUsd(ta.net_R), fmtUsd(tb.net_R), ta.net_R, tb.net_R],
    ["Win rate", fmtPct(ta.win_rate), fmtPct(tb.win_rate)],
    ["Average per trade", fmtUsd(ta.mean_R), fmtUsd(tb.mean_R), ta.mean_R, tb.mean_R],
  ];
  for (const [label, va, vb, sa, sb] of rows) {
    const tr = el("tr");
    tr.append(el("td", {}, label), el("td", { class: `num ${sign(sa) || ""}` }, va), el("td", { class: `num ${sign(sb) || ""}` }, vb));
    body.append(tr);
  }
  const filled = tb.closed;
  const placed = b.orders;
  const pend = b.pending ? ` · 1 order waiting to fill or close (${b.pending.side === 1 ? "▲ up" : "▼ down"})` : "";
  $("b-orders").textContent = placed
    ? `Test B orders placed: ${placed} · filled and finished: ${filled} · not filled: ${b.unfilled}${filled + b.unfilled ? ` · fill rate ${fmtPct(filled / (filled + b.unfilled), 0)}` : ""}${pend}`
    : "Test B has placed no orders yet. It acts on the same signals as Test A from its start time onward.";
}

let latest = null;
async function refresh() {
  try {
    const res = await fetch("/api/summary", { cache: "no-store" });
    const s = await res.json();
    if (s.error) throw new Error(s.error);
    latest = s;
    renderHeader(s);
    renderHero(s.trades);
    renderKpis(s.trades, s.forward_test);
    renderPosition(s.open_position);
    renderProgress(s.forward_test);
    renderEquity(s);
    renderCells(s.decisions);
    renderHealth(s);
    renderTrades(s.recent_trades);
    renderTestB(s.trades, s.test_b);
  } catch (e) {
    $("status-pill").className = "pill warn";
    $("status-text").textContent = "Dashboard error";
    $("updated").textContent = String(e.message || e);
  }
}

function applyTheme(t) {
  if (t) document.documentElement.setAttribute("data-theme", t);
  else document.documentElement.removeAttribute("data-theme");
}
try { applyTheme(localStorage.getItem("mbpe-theme")); } catch (_) {}
$("theme-toggle").addEventListener("click", () => {
  const dark = getComputedStyle(document.documentElement).colorScheme.includes("dark");
  const next = dark ? "light" : "dark";
  applyTheme(next);
  try { localStorage.setItem("mbpe-theme", next); } catch (_) {}
  if (latest) renderEquity(latest);
});
window.addEventListener("resize", () => latest && renderEquity(latest));

refresh();
setInterval(refresh, 15000);
connectPrice();
