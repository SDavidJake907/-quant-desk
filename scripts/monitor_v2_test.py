"""Monitor v2 on real positions: the math as codes, Gemma reads it with headlines.

    python scripts/monitor_v2_test.py

READ-ONLY. No order, no audit row. Two parts:
  REPLAY    every filled swing trade in the broker's history, reviewed at bars
            1, 4, 8 and 16 after the fill (while still open) with its real
            stop and target (audit log), the live 30m store, and headlines
            known at that moment (data/news_history). The final R is known, so
            the calls can be read against it.
  MOMENTUM  the dry-run momentum book's holdings, now, with live headlines.

Output is codes only: trades are T1..Tn and holdings H1..Hn; the ticker key is
written to tmp/monitor_v2_key.json and never printed. Gemma returns letters:
  c  H hold / X exit early      f  0 nothing / 1 watch / 2 red flag
  k  confidence 1-3             e  verbatim headline span (checked) or empty
A sample this small cannot score Gemma; this tests that the format works and
whether its calls look sane. Scoring is TRIALS.md, on forward data.
"""

from __future__ import annotations

import asyncio
import json
import statistics
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scanner.config import settings
from scanner.execution.alpaca import Alpaca
from scanner.features import history
from scanner.features.bars import fetch_bars, fetch_many
from scanner.features.intraday import EXCHANGE_TZ, SESSION_ALIGNED
from scanner.features.technicals import compute
from scanner.llm.ollama import chat_json
from scanner.momentum import state as mstate
from scanner.monitor import codes
from scanner.sources import composite, yahoo_rss
from scanner.sources.index_history import SP400, SP500

sys.path.insert(0, "scripts")
from gate1_test import _atr_pct, _daily  # noqa: E402
from news_event_test import headlines as past_headlines  # noqa: E402

SNAPSHOTS = (1, 4, 8, 16)
KEY = Path("tmp/monitor_v2_key.json")
SCHEMA = {"type": "object", "properties": {
    "c": {"type": "string", "enum": ["H", "X"]},
    "f": {"type": "string", "enum": ["0", "1", "2"]},
    "k": {"type": "string", "enum": ["1", "2", "3"]},
    "e": {"type": "string"}, "w": {"type": "string"}},
    "required": ["c", "f", "k", "e", "w"]}
PROMPT = """You monitor one open position. Python computed every number below.
Do not compute, estimate or repeat any number.

LEGEND
{legend}

POSITION ({kind})
{line}

HEADLINES, newest first
{heads}

Question: do these headlines show that the REASON for holding is broken
(fraud, guidance cut, downgrade cluster, deal/acquisition, lawsuit, offering,
regulatory action)? Price drifting against the position is NOT a reason: {priced}.
Be reluctant: exiting on noise is how this kind of strategy loses money.

c: H hold or X exit early.  f: 0 nothing, 1 watch, 2 red flag (material negative).
k: confidence 1 low, 2 mid, 3 high.  e: copy a span VERBATIM from one headline that
supports f, or "".  w: at most 10 words, no tickers, no numbers."""


def wilder_atr(highs, lows, closes, n=14):
    trs = [max(h - lo, abs(h - c0), abs(lo - c0)) for h, lo, c0 in zip(highs[1:], lows[1:], closes[:-1])]
    if len(trs) < n:
        return None
    atr = statistics.fmean(trs[:n])
    for tr in trs[n:]:
        atr = (atr * (n - 1) + tr) / n
    return atr


async def ask(client, kind, line, heads, priced):
    text = "\n".join(f"- {h}" for h in heads[:6]) or "(none)"
    out = await chat_json(PROMPT.format(legend=codes.LEGEND, kind=kind, line=line, heads=text,
                                        priced=priced), SCHEMA, client)
    out["ev_ok"] = not out.get("e") or any(out["e"] in h for h in heads)
    return out


def intents_by_coid() -> dict[str, dict]:
    out = {}
    for raw in Path(settings.audit_log_path).read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(raw)
        except ValueError:
            continue
        intent = row.get("intent") or {}
        if row.get("event") in ("trade_intent", "order_submitted") and intent.get("client_order_id"):
            out[intent["client_order_id"]] = intent
    return out


async def replay(client, key) -> list[tuple]:
    broker = Alpaca(settings.alpaca_key_id, settings.alpaca_secret_key, client)
    orders = await broker._get("/v2/orders", status="all", limit=500, nested="true", direction="asc")
    flat = []
    for o in orders:
        flat.append(o)
        flat.extend(o.get("legs") or [])
    fills = [o for o in flat if o.get("filled_at") and float(o.get("filled_qty") or 0) > 0]
    intents = intents_by_coid()
    rows, n = [], 0
    for buy in [o for o in fills if o["side"] == "buy" and str(o["client_order_id"]).startswith("smc-")]:
        intent = intents.get(buy["client_order_id"])
        if not intent:
            continue
        sym, t_in = buy["symbol"], datetime.fromisoformat(buy["filled_at"].replace("Z", "+00:00"))
        sells = [o for o in fills if o["symbol"] == sym and o["side"] == "sell"
                 and datetime.fromisoformat(o["filled_at"].replace("Z", "+00:00")) > t_in]
        if not sells:
            continue
        sell = min(sells, key=lambda o: o["filled_at"])
        t_out = datetime.fromisoformat(sell["filled_at"].replace("Z", "+00:00"))
        entry, stop, target = float(buy["filled_avg_price"]), float(intent["stop_price"]), float(intent["target_price"])
        final_r = (float(sell["filled_avg_price"]) - entry) / (entry - stop)
        bars = history.accumulated(sym, 30, SESSION_ALIGNED)
        if bars is None:
            continue
        start = next((i for i, t in enumerate(bars.times) if t + timedelta(minutes=30) > t_in), None)
        if start is None:
            continue
        n += 1
        key[f"T{n}"] = sym
        daily = _daily(bars)
        for k in SNAPSHOTS:
            j = start + k
            if j >= len(bars) or bars.times[j] + timedelta(minutes=30) > t_out:
                break
            price = bars.closes[j]
            atr30 = wilder_atr(bars.highs[:j + 1], bars.lows[:j + 1], bars.closes[:j + 1]) or 0
            pct = _atr_pct(daily, bars.times[j].date()) or 0
            line = codes.render(codes.swing(
                entry=entry, stop=stop, target=target, price=price,
                highs=bars.highs[start:j + 1], lows=bars.lows[start:j + 1], atr30=atr30,
                atr_daily=pct * price / 100, bars_held=k, max_bars=60,
                sessions_to_flatten=max(0, 4 - bars.times[j].weekday())))
            moment = (bars.times[j] + timedelta(minutes=30)).astimezone(timezone.utc)
            heads = [h for _, h in past_headlines(sym, moment)]
            out = await ask(client, "swing trade with a stop and a target", line, heads,
                            "the stop already prices that")
            rows.append((f"T{n}", line, out, final_r, len(heads)))
            print(f"  T{n:<3} {line:<52} c{out['c']} f{out['f']} k{out['k']} "
                  f"{'e+' if out['e'] and out['ev_ok'] else 'e?' if out['e'] else 'e-'}  h{len(heads)}  "
                  f"final R{final_r:+.2f}")
    return rows


async def momentum_now(client, key) -> list[tuple]:
    book = mstate.load(Path("data/momentum/dry_run_book.json"))
    if book is None:
        print("  no dry-run book yet")
        return []
    held = {s: q for t in book.tranches for s, q in t.holdings.items()}
    members = await composite.load_universe(client, (SP500, SP400))
    sector = {m.symbol: m.sector for m in members}
    daily, _ = await fetch_many(tuple(sorted(sector)), client, range_="2y")
    bench = await fetch_bars("^GSPC", client, range_="2y")
    scores = {s: compute(b).momentum_12_1 for s, b in daily.items()}
    ranked = sorted((v, s) for s, v in scores.items() if v is not None)[::-1]
    rank = {s: i + 1 for i, (_, s) in enumerate(ranked)}
    price = {s: daily[s].closes[-1] for s in held if s in daily}
    total = sum(t.cash for t in book.tranches) + sum(q * price[s] for s, q in held.items())
    sec_val: dict[str, float] = {}
    for s, q in held.items():
        sec_val[sector.get(s, "?")] = sec_val.get(sector.get(s, "?"), 0) + q * price[s]
    invested = sum(sec_val.values())
    sma = statistics.fmean(bench.closes[-210:])
    rows = []
    for n, (s, q) in enumerate(sorted(held.items()), 1):
        key[f"H{n}"] = s
        b = daily[s]
        dvol = statistics.fmean(c * v for c, v in zip(b.closes[-20:], b.volumes[-20:]))
        line = codes.render(codes.momentum(
            rank=rank[s], universe=len(ranked), weight_pct=q * price[s] / total * 100,
            sector_share_pct=sec_val[sector.get(s, "?")] / invested * 100,
            trend_gap_pct=(bench.closes[-1] / sma - 1) * 100, dollar_volume=dvol, mom_12_1=scores[s]))
        try:
            heads = [x.title for x in await yahoo_rss.fetch(s, client)]
        except Exception:                                   # noqa: BLE001
            heads = []
        out = await ask(client, "momentum holding, no stop, rebuilt monthly", line, heads,
                        "the monthly rebuild handles fading momentum")
        rows.append((f"H{n}", line, out, None, len(heads)))
        print(f"  H{n:<3} {line:<44} c{out['c']} f{out['f']} k{out['k']} "
              f"{'e+' if out['e'] and out['ev_ok'] else 'e?' if out['e'] else 'e-'}  h{len(heads)}  w: {out['w'][:60]}")
    return rows


async def main() -> int:
    key: dict[str, str] = {}
    async with httpx.AsyncClient(timeout=600) as client:
        print("REPLAY  (codes | Gemma c f k evidence | headlines | final R)")
        swing_rows = await replay(client, key)
        print("\nMOMENTUM NOW")
        mom_rows = await momentum_now(client, key)
    KEY.parent.mkdir(exist_ok=True)
    KEY.write_text(json.dumps(key, indent=1), encoding="utf-8")
    exits = [r for r in swing_rows if r[2]["c"] == "X"]
    holds = [r for r in swing_rows if r[2]["c"] == "H"]
    if swing_rows:
        mean = lambda xs: statistics.fmean(x[3] for x in xs) if xs else float("nan")
        print(f"\nREPLAY {len(swing_rows)} reviews: X {len(exits)} (final R {mean(exits):+.2f}) | "
              f"H {len(holds)} (final R {mean(holds):+.2f}) | flags f2 "
              f"{sum(r[2]['f'] == '2' for r in swing_rows)} | bad evidence quotes "
              f"{sum(1 for r in swing_rows + mom_rows if r[2]['e'] and not r[2]['ev_ok'])}")
    print(f"key -> {KEY} (not printed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
