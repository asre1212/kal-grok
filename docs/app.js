/* kal-grok dashboard — plain JS, no build step. Reads ./data/*.json written by the bot. */
const TZ = "America/New_York";
const $ = (s) => document.querySelector(s);
const money = (v, d = 2) => v == null ? "—" : (v < 0 ? "-$" : "$") + Math.abs(v).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
const signed = (v, d = 2) => v == null ? "—" : (v >= 0 ? "+" : "") + money(v, d);
const pct = (v, d = 2) => v == null ? "—" : (v >= 0 ? "+" : "") + v.toFixed(d) + "%";
const cls = (v) => v > 0 ? "up" : v < 0 ? "dn" : "mut";
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const fmtTime = (ts, opts = {}) => ts ? new Intl.DateTimeFormat("en-US", { timeZone: TZ, month: "short", day: "numeric", hour: "numeric", minute: "2-digit", ...opts }).format(new Date(ts * 1000)) + " ET" : "—";
const nice = (s) => String(s).replace(/_/g, " ");

async function getJSON(name) {
  const r = await fetch(`data/${name}?t=${Date.now()}`, { cache: "no-store" });
  if (!r.ok) throw new Error(name + " " + r.status);
  return r.json();
}
async function getText(name) {
  try { const r = await fetch(`data/${name}?t=${Date.now()}`, { cache: "no-store" }); return r.ok ? r.text() : null; } catch { return null; }
}

function chart(el, curve, start) {
  if (!curve || curve.length < 1) { el.innerHTML = '<div class="empty">Equity curve appears after the first runs</div>'; return; }
  const pts = curve.length === 1 ? [{ ...curve[0], ts: curve[0].ts - 3600 }, curve[0]] : curve;
  const W = 600, H = 170, P = 6;
  const xs = pts.map((p) => p.ts), ys = pts.map((p) => p.equity).concat([start]);
  const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
  const pad = (y1 - y0) * 0.1 || 50;
  const X = (t) => P + (W - 2 * P) * (x1 === x0 ? 1 : (t - x0) / (x1 - x0));
  const Y = (v) => H - P - (H - 2 * P) * ((v - (y0 - pad)) / ((y1 + pad) - (y0 - pad)));
  const d = pts.map((p, i) => (i ? "L" : "M") + X(p.ts).toFixed(1) + "," + Y(p.equity).toFixed(1)).join("");
  const last = pts[pts.length - 1].equity, col = last >= start ? "#22c55e" : "#ef4444";
  el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
    <defs><linearGradient id="g" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="${col}" stop-opacity=".35"/><stop offset="1" stop-color="${col}" stop-opacity="0"/></linearGradient></defs>
    <line x1="0" x2="${W}" y1="${Y(start)}" y2="${Y(start)}" stroke="#8a98ad" stroke-dasharray="4 4" stroke-width="1"/>
    <text x="${W - 4}" y="${Y(start) - 4}" fill="#8a98ad" font-size="11" text-anchor="end">$100k start</text>
    <path d="${d}L${X(x1)},${H}L${X(x0)},${H}Z" fill="url(#g)"/>
    <path d="${d}" fill="none" stroke="${col}" stroke-width="2.2" vector-effect="non-scaling-stroke"/>
    <text x="4" y="12" fill="#8a98ad" font-size="11">${money(y1 + pad, 0)}</text>
    <text x="4" y="${H - 4}" fill="#8a98ad" font-size="11">${money(y0 - pad, 0)}</text></svg>`;
}

function kpis(s) {
  const k = [
    ["Total P&L", signed(s.total_pnl), cls(s.total_pnl)], ["Return", pct(s.return_pct), cls(s.return_pct)],
    ["Realized", signed(s.realized), cls(s.realized)], ["Unrealized", signed(s.unrealized), cls(s.unrealized)],
    ["Cash", money(s.cash), ""], ["In positions", money(s.positions_value), ""],
    ["Fees paid", money(s.fees), "mut"], ["Drawdown", pct(-s.drawdown_pct), s.drawdown_pct > 0 ? "dn" : "mut"],
    ["Liquidation value", money(s.liquidation_equity), ""], ["Open positions", s.open_positions, ""],
    ["Cash interest", money(s.interest), "mut"], ["Runs", s.runs, ""],
  ];
  $("#kpis").innerHTML = k.map(([l, v, c]) => `<div class="kpi"><span>${l}</span><b class="${c}">${v}</b></div>`).join("");
}

let POS = [], TRD = [];
function positions(list, filter = "all") {
  const el = $("#tab-positions");
  const strats = [...new Set(list.map((p) => p.strategy))].sort();
  const rows = list.filter((p) => filter === "all" || p.strategy === filter);
  const head = `<div class="filter"><select id="pf">${["all", ...strats].map((s) => `<option ${s === filter ? "selected" : ""} value="${s}">${s === "all" ? "All strategies" : nice(s)}</option>`).join("")}</select>
    <span class="meta">${rows.length} positions · cost ${money(rows.reduce((a, p) => a + p.cost, 0))}</span></div>`;
  el.innerHTML = head + (rows.length ? rows.map((p) => `<div class="item">
    <div class="row"><div class="title"><a href="${esc(p.url)}" target="_blank" rel="noopener">${esc(p.title || p.ticker)}</a></div>
    <b class="${cls(p.pnl)}">${signed(p.pnl)}</b></div>
    <div class="meta"><span class="pill ${p.side}">${p.side.toUpperCase()}</span><span class="pill s">${nice(p.strategy)}</span>
    ${p.qty.toLocaleString()} @ ${(p.avg_cost * 100).toFixed(1)}¢ → mark ${p.mark == null ? "—" : (p.mark * 100).toFixed(1) + "¢"}
    · value ${money(p.value)}${p.p_model != null ? ` · model ${(p.p_model * 100).toFixed(0)}%` : ""}</div>
    <div class="meta">${esc(p.ticker)} · closes ${fmtTime(p.close, { year: "numeric" })}</div></div>`).join("") : '<div class="empty">No open positions</div>');
  $("#pf").onchange = (e) => positions(POS, e.target.value);
}

function trades(list) {
  const el = $("#tab-trades");
  el.innerHTML = list.length ? list.slice(0, 150).map((t) => `<div class="item">
    <div class="row"><div class="title">${esc(t.title || t.ticker)}</div>${t.pnl != null ? `<b class="${cls(t.pnl)}">${signed(t.pnl)}</b>` : ""}</div>
    <div class="meta"><span class="pill">${t.action}</span><span class="pill ${t.side}">${String(t.side).toUpperCase()}</span><span class="pill s">${nice(t.strategy)}</span>
    ${t.qty} @ ${(t.price * 100).toFixed(1)}¢ · fee ${money(t.fee)}${t.maker ? " · maker" : ""}</div>
    <div class="meta">${fmtTime(t.ts)}${t.note ? " · " + esc(t.note) : ""}</div></div>`).join("") : '<div class="empty">No trades yet</div>';
}

function strategies(s) {
  const el = $("#tab-strategies");
  const rows = [...s.strategies].sort((a, b) => b.weight - a.weight);
  el.innerHTML = rows.map((r) => `<div class="item">
    <div class="row"><div class="title">${nice(r.name)} ${r.status !== "active" ? `<span class="pill">${r.status}</span>` : ""}</div><b class="${cls(r.total)}">${signed(r.total)}</b></div>
    <div class="meta">${esc(r.description)}</div>
    <div class="bar"><i style="width:${Math.min(100, r.weight * 100 / 0.3)}%"></i></div>
    <div class="meta">Allocation ${(r.weight * 100).toFixed(1)}% (${money(r.sleeve, 0)}) · exposure ${money(r.exposure, 0)} · ${r.open_positions} open</div>
    <div class="meta">Realized ${signed(r.realized)} · unrealized ${signed(r.unrealized)} · fees ${money(r.fees)} · ${r.trades} fills · hit ${r.hit_rate == null ? "—" : (r.hit_rate * 100).toFixed(0) + "%"}</div>
    <div class="meta">Params: ${esc(Object.entries(r.params || {}).map(([k, v]) => `${k}=${v}`).join(", "))}${r.note ? " · " + esc(r.note) : ""}</div></div>`).join("")
    + `<div class="item"><div class="meta">Cash reserve ${(s.reserve_weight * 100).toFixed(0)}% · calibration samples ${s.calibration.samples ?? 0}
      · short-horizon β ${s.calibration.beta_short?.toFixed?.(3) ?? "—"} · idle-cash APY assumption ${(s.cash_apy_assumption * 100).toFixed(2)}%</div></div>`;
}

function md(text) {
  const lines = text.split("\n"); let out = "", inTable = false, inList = false;
  const inline = (s) => esc(s).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/_(.+?)_/g, "<i>$1</i>").replace(/`(.+?)`/g, "<code>$1</code>");
  for (const l of lines) {
    if (/^\|/.test(l)) {
      if (/^\|[-:| ]+\|$/.test(l)) continue;
      const cells = l.split("|").slice(1, -1).map((c) => c.trim());
      if (!inTable) { out += "<table><tr>" + cells.map((c) => `<th>${inline(c)}</th>`).join("") + "</tr>"; inTable = true; }
      else out += "<tr>" + cells.map((c) => `<td>${inline(c)}</td>`).join("") + "</tr>";
      continue;
    } else if (inTable) { out += "</table>"; inTable = false; }
    if (/^- /.test(l)) { if (!inList) { out += "<ul>"; inList = true; } out += `<li>${inline(l.slice(2))}</li>`; continue; }
    else if (inList) { out += "</ul>"; inList = false; }
    if (/^## /.test(l)) out += `<h2>${inline(l.slice(3))}</h2>`;
    else if (/^# /.test(l)) out += `<h1>${inline(l.slice(2))}</h1>`;
    else if (l.trim()) out += `<p>${inline(l)}</p>`;
  }
  return out + (inTable ? "</table>" : "") + (inList ? "</ul>" : "");
}

async function report() {
  const el = $("#tab-report");
  const text = await getText("reports/latest.md");
  let idx = []; try { idx = await getJSON("reports/index.json"); } catch {}
  el.innerHTML = `<div class="item md">${text ? md(text) : '<div class="empty">The first weekly review runs on Sunday. Strategies are re-scored and capital is reallocated every week.</div>'}</div>`
    + (idx.length ? `<div class="item"><div class="title">History</div>${idx.slice().reverse().map((i) => `<div class="row meta"><span>${i.date}</span><span class="${cls(i.week_pnl)}">${signed(i.week_pnl)} (${pct(i.return_pct)})</span></div>`).join("")}</div>` : "");
}

function lastRun(s) {
  const c = s.last_cycle || {};
  $("#tab-run").innerHTML = `<div class="item"><div class="meta">${fmtTime(c.ts)} · ${(c.events_scanned || 0).toLocaleString()} events · ${(c.markets_scanned || 0).toLocaleString()} markets scanned ·
    ${(c.universe || 0).toLocaleString()} tradable · ${c.new_trades || 0} new trades · ${c.settled || 0} settlements · ${c.orderbooks || 0} orderbooks · ${c.seconds || 0}s
    ${c.halted ? " · <b class='dn'>circuit breaker: directional trading halted</b>" : ""}</div></div>
    <div class="item"><div class="log">${esc(Object.entries(c.notes || {}).map(([k, v]) => `${k}: ${v}`).join("\n"))}\n\n${esc((c.log || []).join("\n")) || "No decisions logged."}</div></div>`;
}

async function load() {
  const btn = $("#refresh"); btn.classList.add("spin");
  try {
    const [s, eq] = await Promise.all([getJSON("summary.json"), getJSON("equity.json")]);
    $("#equity").textContent = money(s.equity);
    $("#pnlline").innerHTML = `<span class="${cls(s.total_pnl)}">${signed(s.total_pnl)} (${pct(s.return_pct)})</span> <span class="mut">vs $100,000 start</span>`;
    $("#updated").textContent = fmtTime(s.last_run, { year: "numeric" });
    chart($("#chart"), eq.curve, eq.start);
    kpis(s); strategies(s); lastRun(s);
    [POS, TRD] = await Promise.all([getJSON("positions.json"), getJSON("trades.json")]);
    positions(POS, $("#pf")?.value || "all"); trades(TRD);
    report();
  } catch (e) {
    $("#updated").textContent = "offline / no data yet (" + e.message + ")";
  } finally { btn.classList.remove("spin"); }
}

document.querySelectorAll("#tabs button").forEach((b) => b.onclick = () => {
  document.querySelectorAll("#tabs button, .tab").forEach((x) => x.classList.remove("on"));
  b.classList.add("on"); $("#tab-" + b.dataset.tab).classList.add("on");
});
$("#refresh").onclick = load;

// pull-to-refresh (works in iOS standalone mode where the browser's own gesture is absent)
let y0 = null, pulled = 0;
window.addEventListener("touchstart", (e) => { y0 = window.scrollY <= 0 ? e.touches[0].clientY : null; }, { passive: true });
window.addEventListener("touchmove", (e) => {
  if (y0 == null) return; pulled = e.touches[0].clientY - y0;
  if (pulled > 0) { $("#ptr").style.top = Math.min(pulled / 2, 50) - 40 + "px"; $("#ptr").textContent = pulled > 110 ? "↻ Release to refresh" : "↓ Pull to refresh"; }
}, { passive: true });
window.addEventListener("touchend", () => { if (y0 != null && pulled > 110) load(); $("#ptr").style.top = "-40px"; y0 = null; pulled = 0; });
document.addEventListener("visibilitychange", () => { if (!document.hidden) load(); });

if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js");
load();
