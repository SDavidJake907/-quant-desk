/* stock-scanner dashboard. Every value goes in via textContent,
   so nothing from the audit log or the broker can inject markup. */
"use strict";

const REFRESH_MS = 60_000;
const STALE_HOURS = 96;           // a daily job silent this long is flagged
const SVG_NS = "http://www.w3.org/2000/svg";
const BOOKS = {
  you: { label: "You", color: "var(--you)", key: "key-you" },
  gemma: { label: "Gemma", color: "var(--gemma)", key: "key-gemma" },
};
const EVENT_LABELS = {
  order_submitted: "order", trade_outcome: "closed", picks_exit: "sold",
  picks_refused: "refused", picks_entry_cancelled: "entry cancelled",
  gemma_decision: "decision", gemma_exit: "sold", momentum_order: "order",
  momentum_exit: "sold", candidate_refused: "refused",
  position_unprotected: "unprotected", protection_restored: "stop restored",
  weekend_flatten: "flattened",
};

// ---- small DOM helpers ---------------------------------------------------

const $ = (id) => document.getElementById(id);

function h(tag, props = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "class") el.className = v;
    else if (k === "text") el.textContent = v;
    else el.setAttribute(k, v);
  }
  for (const c of children) if (c != null) el.append(c);
  return el;
}

function svg(tag, attrs = {}) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  return el;
}

const money = (n, digits = 0) => n == null ? "—" :
  new Intl.NumberFormat("en-US", { style: "currency", currency: "USD",
    minimumFractionDigits: digits, maximumFractionDigits: digits }).format(n);
const signedMoney = (n) => n == null ? "—" : (n > 0 ? "+" : n < 0 ? "−" : "") + money(Math.abs(n));
const pct = (n) => n == null ? "—" : `${n > 0 ? "+" : n < 0 ? "−" : ""}${Math.abs(n).toFixed(2)}%`;
const tone = (n) => n > 0 ? "gain" : n < 0 ? "loss" : "";

function ago(iso) {
  if (!iso) return "never";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 90) return "just now";
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400 * 2) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}

function shortTime(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return d.toLocaleString("en-US", { month: "short", day: "numeric",
    hour: "numeric", minute: "2-digit" });
}

async function getJSON(path) {
  const r = await fetch(path, { cache: "no-store" });
  if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
  return r.json();
}

// ---- scoreboard -----------------------------------------------------------

function renderScoreboard(sb) {
  const m = sb.latest;
  if (!m) {
    $("score-title").textContent = "No standings yet";
    $("score-verdict").textContent = "scripts/competition.py has not written logs/competition/standings.md";
    return;
  }
  const month = new Date(`${m.month}-01T12:00:00`).toLocaleString("en-US",
    { month: "long", year: "numeric" });
  $("score-title").textContent = `${month}${m.in_progress ? " · in progress" : ""}`;
  $("score-verdict").textContent = m.verdict;

  const ret = Object.fromEntries(m.rows.map((r) => [r.key, r.return_pct]));
  for (const side of ["you", "gemma"]) {
    const el = $(`ret-${side}`);
    el.textContent = pct(ret[side]);
    el.className = `side-return ${tone(ret[side])}`;
    const ghost = ret[`ghost_${side}`];
    $(`ghost-${side}`).textContent = ghost == null ? "" :
      `ghost ${pct(ghost)} · ${ret[side] > ghost ? "beat its ghost" : "did not beat its ghost"}`;
  }
  const lead = ret.you > ret.gemma ? "you" : ret.gemma > ret.you ? "gemma" : null;
  $("side-you").classList.toggle("leader", lead === "you");
  $("side-gemma").classList.toggle("leader", lead === "gemma");

  const p = sb.probability;
  if (p) {
    $("odds-you").style.flex = String(p.you);
    $("odds-gemma").style.flex = String(p.gemma);
    $("odds-you-label").textContent = `You ${Math.round(p.you * 100)}%`;
    $("odds-gemma-label").textContent = `Gemma ${Math.round(p.gemma * 100)}%`;
    $("odds-bar").setAttribute("aria-label",
      `You ${Math.round(p.you * 100)} percent, Gemma ${Math.round(p.gemma * 100)} percent`);
    $("odds-note").textContent = "daily returns resampled";
  } else {
    $("odds").hidden = !sb.probability_note;
    $("odds-note").textContent = sb.probability_note;
  }

  const bench = $("benchmarks");
  bench.replaceChildren();
  for (const [key, label] of [["spx", "S&P 500"], ["momentum", "Momentum rule"]]) {
    if (ret[key] == null) continue;
    bench.append(h("div", {}, h("dt", { text: label }),
      h("dd", { class: tone(ret[key]), text: pct(ret[key]) })));
  }
  $("quarters").textContent = sb.quarters.join(" · ");
  $("meta-updated").textContent = `standings ${ago(sb.updated)}`;

  renderChart(sb.pl_series);
  renderSelection(sb.selection);
}

// ---- P/L chart ------------------------------------------------------------

function renderChart(series) {
  const wrap = $("chart");
  wrap.replaceChildren();
  const lines = [["you", series.picks || []], ["gemma", series.gemma || []]];
  const days = [...new Set(lines.flatMap(([, s]) => s.map((p) => p.day)))].sort();
  renderChartTable(days, lines);
  if (days.length < 2) {
    wrap.append(h("div", { class: "empty", text: "Not enough daily snapshots to draw yet." }));
    return;
  }

  const W = wrap.clientWidth || 800, H = wrap.clientHeight || 220;
  const pad = { l: 56, r: 72, t: 8, b: 22 };
  const values = lines.flatMap(([, s]) => s.map((p) => p.pl)).concat(0);
  const ticks = niceTicks(Math.min(...values), Math.max(...values));
  const lo = ticks[0], hi = ticks[ticks.length - 1];
  const x = (i) => pad.l + (i / (days.length - 1)) * (W - pad.l - pad.r);
  const y = (v) => pad.t + (1 - (v - lo) / (hi - lo)) * (H - pad.t - pad.b);

  const root = svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "img",
    "aria-label": "Total profit and loss by day for you and Gemma" });
  const grid = svg("g", { class: "grid" });
  for (const v of ticks) {
    grid.append(svg("line", { x1: pad.l, x2: W - pad.r, y1: y(v), y2: y(v) }));
    const t = svg("text", { x: pad.l - 8, y: y(v) + 4, "text-anchor": "end", class: "tick" });
    t.textContent = signedMoney(v);
    grid.append(t);
  }
  root.append(grid);
  root.append(svg("line", { class: "zero", x1: pad.l, x2: W - pad.r, y1: y(0), y2: y(0) }));
  for (const i of [0, days.length - 1]) {
    const t = svg("text", { x: x(i), y: H - 4, class: "tick",
      "text-anchor": i === 0 ? "start" : "end" });
    t.textContent = days[i].slice(5);
    root.append(t);
  }

  for (const [book, s] of lines) {
    if (!s.length) continue;
    const pts = s.map((p) => `${x(days.indexOf(p.day))},${y(p.pl)}`).join(" ");
    root.append(svg("polyline", { points: pts, fill: "none", stroke: BOOKS[book].color,
      "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }));
    const last = s[s.length - 1];
    const label = svg("text", { x: x(days.indexOf(last.day)) + 8, y: y(last.pl) + 4,
      class: "end-label" });
    label.textContent = `${BOOKS[book].label} ${signedMoney(last.pl)}`;
    root.append(label);
  }

  const cross = svg("line", { class: "crosshair", y1: pad.t, y2: H - pad.b, visibility: "hidden" });
  const dots = Object.fromEntries(lines.map(([book]) => [book,
    svg("circle", { r: 4, fill: BOOKS[book].color, stroke: "var(--surface)",
      "stroke-width": 2, visibility: "hidden" })]));
  root.append(cross, ...Object.values(dots));
  const hit = svg("rect", { x: pad.l, y: 0, width: W - pad.l - pad.r, height: H,
    fill: "transparent" });
  root.append(hit);
  wrap.append(root);

  const tip = $("tooltip");
  hit.addEventListener("mousemove", (ev) => {
    const box = root.getBoundingClientRect();
    const px = ((ev.clientX - box.left) / box.width) * W;
    const i = Math.max(0, Math.min(days.length - 1,
      Math.round(((px - pad.l) / (W - pad.l - pad.r)) * (days.length - 1))));
    cross.setAttribute("x1", x(i)); cross.setAttribute("x2", x(i));
    cross.setAttribute("visibility", "visible");
    tip.replaceChildren(h("div", { class: "day", text: days[i] }));
    for (const [book, s] of lines) {
      const p = s.find((q) => q.day === days[i]);
      const dot = dots[book];
      if (!p) { dot.setAttribute("visibility", "hidden"); continue; }
      dot.setAttribute("cx", x(i)); dot.setAttribute("cy", y(p.pl));
      dot.setAttribute("visibility", "visible");
      tip.append(h("div", { class: "row" },
        h("span", {}, h("span", { class: `key ${BOOKS[book].key}` }), BOOKS[book].label),
        h("span", { class: "num", text: signedMoney(p.pl) })));
    }
    tip.hidden = false;
    const left = Math.min(ev.clientX + 14, window.innerWidth - tip.offsetWidth - 8);
    tip.style.left = `${left}px`;
    tip.style.top = `${ev.clientY + 14}px`;
  });
  hit.addEventListener("mouseleave", () => {
    tip.hidden = true;
    cross.setAttribute("visibility", "hidden");
    Object.values(dots).forEach((d) => d.setAttribute("visibility", "hidden"));
  });
}

/** Round axis ticks (1, 2, 2.5, 5 x 10^n) that cover [lo, hi]. */
function niceTicks(lo, hi, target = 4) {
  const span = hi - lo || 1;
  const raw = span / target;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw);
  const out = [];
  for (let v = Math.floor(lo / step) * step; v <= hi + step * 0.999; v += step) {
    out.push(Math.round(v * 100) / 100);
  }
  return out;
}

function renderChartTable(days, lines) {
  const body = h("tbody");
  for (const day of [...days].reverse()) {
    const cells = lines.map(([, s]) => {
      const p = s.find((q) => q.day === day);
      return h("td", { class: `r num ${p ? tone(p.pl) : ""}`, text: p ? signedMoney(p.pl) : "—" });
    });
    body.append(h("tr", {}, h("td", { class: "num", text: day }), ...cells));
  }
  $("chart-table").replaceChildren(h("table", {},
    h("thead", {}, h("tr", {}, h("th", { text: "Day" }),
      h("th", { class: "r", text: "You" }), h("th", { class: "r", text: "Gemma" }))), body));
}

// ---- selection ------------------------------------------------------------

function renderSelection(groups) {
  const wrap = $("selection");
  wrap.replaceChildren();
  if (!groups.length) {
    wrap.append(h("div", { class: "empty", text: "No grades yet." }));
    return;
  }
  for (const g of groups) {
    wrap.append(h("div", { class: "sel-group" }, h("h3", { text: g.horizon }),
      ...g.lines.map((line) => h("p", { text: line }))));
  }
}

// ---- books ----------------------------------------------------------------

function positionsTable(rows) {
  const head = h("tr", {}, ...["Symbol", "Qty", "Avg", "Last", "P/L", "P/L %", "Stop"]
    .map((t, i) => h("th", { class: i ? "r" : "", text: t })));
  const body = h("tbody");
  for (const r of rows) {
    body.append(h("tr", {},
      h("td", { class: "sym", text: r.symbol }),
      h("td", { class: "r num", text: String(r.qty) }),
      h("td", { class: "r num", text: money(r.avg_entry, 2) }),
      h("td", { class: "r num", text: money(r.price, 2) }),
      h("td", { class: `r num ${tone(r.pl)}`, text: signedMoney(r.pl) }),
      h("td", { class: `r num ${tone(r.pl_pct)}`, text: pct(r.pl_pct) }),
      h("td", { class: `r num${r.stop ? "" : " no-stop"}`, text: r.stop ? money(r.stop, 2) : "none" })));
  }
  return h("div", { class: "table-scroll" }, h("table", {}, h("thead", {}, head), body));
}

function renderBook(id, rows, emptyText, listName, list) {
  const wrap = $(id);
  wrap.replaceChildren();
  wrap.append(rows.length ? positionsTable(rows) : h("div", { class: "empty", text: emptyText }));
  if (list && list.length) {
    wrap.append(h("div", { class: "chips" }, ...list.map((s) => h("span", { class: "chip", text: s }))));
  }
}

function renderBooks(data) {
  if (!data.ok) {
    for (const id of ["book-you", "book-gemma"]) {
      $(id).replaceChildren(h("div", { class: "empty", text: data.error }));
    }
    $("account").replaceChildren(h("div", { class: "empty", text: data.error }));
    $("meta-market").textContent = "broker unreachable";
    return;
  }
  const { books, lists, account } = data;
  $("list-you").textContent = `picks.txt · ${lists.picks.length} names`;
  $("list-gemma").textContent = `menu: core + gemma.txt · ${lists.core.length + lists.gemma.length} names`;
  renderBook("book-you", books.picks,
    lists.picks.length ? "No open positions." : "No open positions — picks.txt is empty.",
    "picks", lists.picks);
  renderBook("book-gemma", books.gemma, "No open positions.", "gemma", null);
  $("book-other-panel").hidden = !books.other.length;
  if (books.other.length) $("book-other").replaceChildren(positionsTable(books.other));

  $("acct-mode").textContent = `${data.paper ? "paper" : "NOT PAPER"} · ${account.status}`;
  $("account").replaceChildren(
    ...[["Equity", account.equity], ["Cash", account.cash], ["Buying power", account.buying_power]]
      .map(([k, v]) => h("div", {}, h("dt", { text: k }), h("dd", { text: money(v) }))));
  $("meta-market").textContent = data.market_open ? "market open" : "market closed";
}

// ---- system ---------------------------------------------------------------

function renderSystem(sys) {
  const f = sys.freeze || {};
  $("meta-window").textContent = [f.window && `window ${f.window}`,
    f.fingerprint && `policy ${f.fingerprint}`].filter(Boolean).join(" · ");
  $("kill-banner").hidden = !sys.kill_switch;

  const feed = $("activity");
  if (!sys.activity.length) {
    feed.replaceChildren(h("div", { class: "empty", text: "No trading events yet." }));
  } else {
    const body = h("tbody");
    for (const e of sys.activity) {
      const book = e.book === "picks" || e.book === "manual" ? "you" : e.book === "gemma" ? "gemma" : null;
      const tag = book
        ? h("span", { class: "book-tag" }, h("span", { class: `key ${BOOKS[book].key}` }), BOOKS[book].label)
        : h("span", { class: "book-tag", text: e.book || "—" });
      body.append(h("tr", {},
        h("td", { class: "when", text: shortTime(e.at) }),
        h("td", {}, tag),
        h("td", { class: "ev", text: EVENT_LABELS[e.event] || e.event }),
        h("td", { class: "sym", text: e.symbol || "" }),
        h("td", { class: `reason ${e.realized != null ? tone(e.realized) : ""}`, text: e.reason })));
    }
    feed.replaceChildren(h("div", { class: "table-scroll" },
      h("table", { class: "feed" }, body)));
  }

  const jobs = h("div", { class: "jobs" });
  for (const j of sys.jobs) {
    const hours = j.last_run ? (Date.now() - new Date(j.last_run)) / 3.6e6 : Infinity;
    const stale = hours > STALE_HOURS;
    jobs.append(h("div", { class: "job" }, h("span", { text: j.task }),
      h("span", { class: `age${stale ? " stale" : ""}`,
        text: `${ago(j.last_run)}${stale ? " · stale" : ""}` })));
  }
  $("jobs").replaceChildren(jobs);
}

// ---- Gemma's review ------------------------------------------------------

const VERDICT_LABELS = { keep: "keep", watch: "watch", sell_candidate: "sell candidate" };

function renderReview(data) {
  const r = data && data.review;
  if (!r) return;
  $("review-when").textContent = `${r.day} · written ${shortTime(r.at)}`;
  const n = r.numbers || {};
  const figs = h("div", { class: "review-figures" },
    h("div", {}, h("b", { text: money(n.invested) }), "invested"),
    h("div", {}, h("b", { text: `${(n.invested_pct ?? 0).toFixed(0)}%` }), "of her book at work"),
    h("div", {}, h("b", { text: money(n.idle) }), "idle cash"),
    h("div", {}, h("b", { class: tone(n.open_pl), text: signedMoney(n.open_pl) }), "open P/L"),
    forecastFigure(r.forecast_score));

  const body = h("tbody");
  for (const p of r.positions || []) {
    body.append(h("tr", {},
      h("td", { class: "sym", text: p.symbol }),
      h("td", {}, h("span", { class: `verdict-tag verdict-${p.verdict}`,
        text: VERDICT_LABELS[p.verdict] || p.verdict })),
      h("td", { class: `num ${tone(p.pl_pct)}`, text: p.pl_pct == null ? "—" : pct(p.pl_pct) }),
      h("td", { class: "num", text: p.weight_pct == null ? "—" : `${p.weight_pct.toFixed(0)}%` }),
      h("td", { class: "num", text: p.stop_gap_pct == null ? "none" : `${p.stop_gap_pct.toFixed(1)}%` }),
      h("td", { class: "reason" }, p.reason || "",
        p.risk ? h("span", { class: "muted", text: ` Risk: ${p.risk}` }) : null,
        p.sell_if ? h("span", { class: `muted sell-if met-${p.sell_if_met || "unclear"}`,
          text: ` Sell if: ${p.sell_if} (met: ${p.sell_if_met || "unclear"})` }) : null)));
  }
  const table = h("div", { class: "table-scroll" }, h("table", { class: "feed" },
    h("thead", {}, h("tr", {}, ...["Stock", "Verdict", "P/L", "Weight", "To stop", "Why"]
      .map((t) => h("th", { text: t })))), body));

  const list = (items, fill) => {
    const ul = h("ul", { class: "review-list" });
    if (!items || !items.length) ul.append(h("li", { class: "muted", text: "none" }));
    for (const it of items || []) ul.append(fill(it));
    return ul;
  };
  const cols = h("div", { class: "review-cols" },
    h("div", {}, h("h3", { text: "Next stocks coming up" }), list(r.watchlist, (w) =>
      h("li", {}, h("b", { text: w.symbol }), w.why, h("span", { class: "muted", text: `Buy if: ${w.buy_if}` })))),
    h("div", {},
      h("h3", { text: "Coming up" }), list(r.upcoming, (u) => h("li", { text: u })),
      h("h3", { text: "Her proposals (need your OK)", style: "margin-top:14px" }),
      list(r.proposals, (pr) => h("li", {}, h("b", { text: "→" }), pr.change,
        h("span", { class: "muted", text: pr.why })))));

  const think = r.thinking ? h("details", { class: "thinking" },
    h("summary", { text: "Her thinking" }), h("pre", { text: r.thinking })) : null;
  $("review").replaceChildren(h("p", { class: "review-summary", text: r.summary }), figs, table, cols,
    operatorBook(r.operator_book), think);
}

/** Her safety check of the OPERATOR's positions (advisor/operator_watch.py). Advice only. */
function operatorBook(ob) {
  if (!ob || !(ob.positions || []).length) return null;
  const body = h("tbody");
  for (const p of ob.positions) {
    body.append(h("tr", {},
      h("td", { class: "sym", text: p.symbol }),
      h("td", {}, h("span", { class: `verdict-tag verdict-${p.verdict}`, text: p.verdict })),
      h("td", { class: `num ${tone(p.pl_pct)}`, text: p.pl_pct == null ? "—" : pct(p.pl_pct) }),
      h("td", { class: "num", text: p.stop_gap_pct == null ? "none" : `${p.stop_gap_pct.toFixed(1)}%` }),
      h("td", { class: "reason" }, p.reason || "",
        p.watch_for ? h("span", { class: "muted", text: ` Watch: ${p.watch_for}` }) : null,
        ...(p.flags || []).map((f) => h("span", { class: "muted", text: ` Flag: ${f}` })))));
  }
  return h("div", {}, h("h3", { text: "Your positions: Gemma's safety check (advice only)", style: "margin-top:14px" }),
    h("div", { class: "table-scroll" }, h("table", { class: "feed" },
      h("thead", {}, h("tr", {}, ...["Stock", "Her read", "P/L", "To stop", "Why"]
        .map((t) => h("th", { text: t })))), body)));
}

/** Her 5-day forecast score (WORKLIST §65): Brier vs 0.25 for "no view". */
function forecastFigure(sc) {
  if (!sc || !sc.n) return h("div", {}, h("b", { text: "—" }), "forecasts graded: none yet");
  if (sc.n < 30) return h("div", {}, h("b", { text: `${sc.n}` }), "forecasts graded (need 30)");
  const good = sc.brier < 0.25;
  return h("div", {}, h("b", { class: good ? "gain" : "loss", text: sc.brier.toFixed(3) }),
    `forecast score, ${sc.n} graded (0.250 = no skill)`);
}

// ---- Ask Gemma -------------------------------------------------------------

let askPoll = null;

function renderAsks(data) {
  const box = h("div");
  for (const a of data.asks || []) {
    const answer = a.status === "answered" ? h("div", { class: "ask-a", text: a.answer })
      : a.status === "pending" ? h("div", { class: "ask-a muted", text: "Gemma is thinking…" })
      : h("div", { class: "ask-a failed", text: a.error || "not answered" });
    box.append(h("div", { class: "ask-item" },
      h("div", { class: "ask-q", text: a.question }),
      h("div", { class: "muted", text: shortTime(a.at) }), answer));
  }
  $("ask-history").replaceChildren(box);
  $("ask-send").disabled = !!data.pending;
  $("ask-status").textContent = data.pending ? "Gemma is answering — usually under a minute." : "";
  clearTimeout(askPoll);
  if (data.pending) askPoll = setTimeout(loadAsks, 4000);
}

function loadAsks() {
  getJSON("/api/asks").then(renderAsks).catch((e) => console.error(e));
}

$("ask-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const question = $("ask-text").value.trim();
  if (!question) return;
  $("ask-send").disabled = true;
  try {
    const r = await fetch("/api/ask", { method: "POST",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question }) });
    const body = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(body.detail || `HTTP ${r.status}`);
    $("ask-text").value = "";
  } catch (e) {
    $("ask-status").textContent = `Not sent: ${e.message}`;
  }
  loadAsks();
});

// ---- load -----------------------------------------------------------------

let lastScoreboard = null;

/** Each panel renders as soon as its own data arrives; the broker is slowest. */
function load() {
  getJSON("/api/scoreboard")
    .then((sb) => { lastScoreboard = sb; renderScoreboard(sb); })
    .catch((e) => console.error(e));
  getJSON("/api/system").then(renderSystem).catch((e) => console.error(e));
  getJSON("/api/review").then(renderReview).catch((e) => console.error(e));
  loadAsks();
  getJSON("/api/books").then(renderBooks)
    .catch(() => renderBooks({ ok: false, error: "could not load positions" }));
}

let resizeTimer;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => lastScoreboard && renderChart(lastScoreboard.pl_series), 150);
});

load();
setInterval(load, REFRESH_MS);
