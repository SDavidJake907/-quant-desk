"""Every real Alpaca trade, bar by bar: signal, order, fill, exit.

    python scripts/trade_timeline.py                 # every closed trade
    python scripts/trade_timeline.py --symbol APP PSX

READ-ONLY. Reads the newest data/broker_archive orders snapshot and Alpaca's own
30-minute SIP bars (raw, unadjusted, so prices match the fills). No backtest:
only what was sent, what filled, and what the tape did around it.

For each bracket entry that filled: the signal bar (from the client order id's
epoch), the limit / stop / target sent, submit and fill times, the fill gap
against the limit, which leg closed it (or a separate sell, e.g. the Friday
flatten), and the bars from three before the signal to one after the exit.
Times are Eastern.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scanner.config import settings
from scanner.features.intraday import EXCHANGE_TZ

DIM, BOLD, RESET = "\033[2m", "\033[1m", "\033[0m"
GREEN, RED, YELLOW, CYAN = "\033[32m", "\033[31m", "\033[33m", "\033[36m"
BASE = "https://data.alpaca.markets/v2/stocks"
_EPOCH = re.compile(r"-(\d{9,})$")


def ts(text: str | None) -> datetime | None:
    return datetime.fromisoformat(text.replace("Z", "+00:00")) if text else None


def et(when: datetime | None) -> str:
    return when.astimezone(EXCHANGE_TZ).strftime("%m-%d %a %H:%M") if when else "-"


async def bars(client, symbol: str, start: datetime, end: datetime) -> list[dict]:
    params = {"timeframe": "30Min", "start": start.isoformat().replace("+00:00", "Z"),
              "end": end.isoformat().replace("+00:00", "Z"), "limit": 10000,
              "feed": "sip", "adjustment": "raw"}
    headers = {"APCA-API-KEY-ID": settings.alpaca_key_id,
               "APCA-API-SECRET-KEY": settings.alpaca_secret_key}
    r = await client.get(f"{BASE}/{symbol}/bars", headers=headers, params=params, timeout=60)
    r.raise_for_status()
    out = []
    for b in r.json().get("bars") or []:
        t = ts(b["t"])
        local = t.astimezone(EXCHANGE_TZ)
        if (local.hour, local.minute) < (9, 30) or local.hour >= 16:
            continue                                  # regular session only
        out.append({"t": t, "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"], "v": b["v"]})
    return out


def trades(orders: list[dict]) -> list[dict]:
    """Filled bracket entries, each with its exit."""
    out = []
    sells = [o for o in orders if o.get("side") == "sell" and o.get("status") == "filled"
             and o.get("order_class") in (None, "", "simple")]
    for o in orders:
        if not (o.get("side") == "buy" and o.get("order_class") == "bracket"
                and o.get("status") == "filled"):
            continue
        legs = o.get("legs") or []
        tp = next((l for l in legs if l.get("type") == "limit"), {})
        sl = next((l for l in legs if l.get("type") in ("stop", "stop_limit")), {})
        exit_leg = next((l for l in (tp, sl) if l.get("status") == "filled"), None)
        how = "target" if exit_leg is tp and exit_leg else "stop" if exit_leg else None
        if exit_leg is None:
            filled = ts(o["filled_at"])
            later = [s for s in sells if s["symbol"] == o["symbol"] and ts(s["filled_at"]) > filled]
            if later:
                exit_leg = min(later, key=lambda s: ts(s["filled_at"]))
                how = "closed (flatten/manual)"
        m = _EPOCH.search(o.get("client_order_id") or "")
        out.append({
            "symbol": o["symbol"], "qty": float(o["filled_qty"]),
            "signal": datetime.fromtimestamp(int(m.group(1)), timezone.utc) if m else None,
            "limit": float(o["limit_price"]), "submitted": ts(o["submitted_at"]),
            "filled": ts(o["filled_at"]), "fill": float(o["filled_avg_price"]),
            "target": float(tp["limit_price"]) if tp.get("limit_price") else None,
            "stop": float(sl["stop_price"]) if sl.get("stop_price") else None,
            "exit_at": ts(exit_leg["filled_at"]) if exit_leg else None,
            "exit": float(exit_leg["filled_avg_price"]) if exit_leg else None,
            "how": how or "OPEN"})
    return sorted(out, key=lambda t: t["filled"])


def show(t: dict, tape: list[dict]) -> None:
    risk = t["limit"] - t["stop"] if t["stop"] else None
    r = (t["exit"] - t["fill"]) / risk if risk and t["exit"] else None
    gap = (t["limit"] - t["fill"]) / t["limit"] * 100
    tint = GREEN if (r or 0) > 0 else RED
    print(f"\n{BOLD}{'=' * 92}{RESET}")
    print(f"{BOLD}{t['symbol']}{RESET}  {tint}{(f'{r:+.2f}R' if r is not None else 'open')}{RESET}  "
          f"{t['how']}   qty {t['qty']:.0f}   P&L "
          f"{((t['exit'] or t['fill']) - t['fill']) * t['qty']:+,.0f}")
    print(f"  signal bar  {et(t['signal'])}    order sent {et(t['submitted'])}    "
          f"filled {et(t['filled'])}  ({(t['filled'] - t['submitted']).total_seconds() / 60:.0f} min)")
    print(f"  limit {t['limit']:.2f}   fill {t['fill']:.2f} "
          f"({YELLOW if gap >= 0.5 else DIM}{gap:+.2f}% under limit{RESET})   stop {t['stop']}"
          f"   target {t['target']}   exit {t['exit']} at {et(t['exit_at'])}")
    if risk:
        print(f"  stop {(t['limit'] - t['stop']) / t['limit'] * 100:.2f}% below limit, "
              f"{(t['fill'] - t['stop']) / t['fill'] * 100:.2f}% below the fill;  target "
              f"{(t['target'] - t['limit']) / risk:.1f}R from limit")
    if not tape:
        print(f"  {DIM}(no bars returned){RESET}")
        return
    sig_i = next((i for i, b in enumerate(tape) if t["signal"] and b["t"] >= t["signal"]), 0)
    end_t = t["exit_at"] or tape[-1]["t"]
    exit_i = next((i for i, b in enumerate(tape) if b["t"] + timedelta(minutes=30) > end_t), len(tape) - 1)
    lo, hi = max(0, sig_i - 3), min(len(tape) - 1, exit_i + 1)
    idx = list(range(lo, hi + 1))
    if len(idx) > 26:
        idx = idx[:13] + [None] + idx[-11:]
    print(f"  {'bar (ET)':<17}{'open':>9}{'high':>9}{'low':>9}{'close':>9}{'vol':>10}  events")
    for i in idx:
        if i is None:
            print(f"  {DIM}  ...{RESET}")
            continue
        b = tape[i]
        start, stop_t = b["t"], b["t"] + timedelta(minutes=30)
        ev = []
        if i == sig_i:
            ev.append(f"{CYAN}SIGNAL (entry = this close){RESET}")
        if t["submitted"] and start <= t["submitted"] < stop_t:
            ev.append("order sent")
        if start <= t["filled"] < stop_t:
            ev.append(f"{BOLD}FILLED {t['fill']:.2f}{RESET}")
        if t["stop"] and b["l"] <= t["stop"] and start >= t["filled"] - timedelta(minutes=30):
            ev.append(f"{RED}low through stop{RESET}")
        if t["target"] and b["h"] >= t["target"] and start >= t["filled"] - timedelta(minutes=30):
            ev.append(f"{GREEN}high through target{RESET}")
        if t["exit_at"] and start <= t["exit_at"] < stop_t:
            ev.append(f"{BOLD}EXIT {t['exit']:.2f} ({t['how']}){RESET}")
        print(f"  {et(b['t']):<17}{b['o']:>9.2f}{b['h']:>9.2f}{b['l']:>9.2f}{b['c']:>9.2f}"
              f"{b['v']:>10,.0f}  {'; '.join(ev)}")


async def main(symbols: list[str]) -> int:
    snaps = sorted(Path("data/broker_archive").glob("*__orders.json"))
    if not snaps:
        print("no broker archive; run scripts/archive_broker.py")
        return 1
    # Merge every snapshot, newest wins: the broker prunes old orders, so the
    # latest file alone holds only the most recent trades.
    merged: dict[str, dict] = {}
    for snap in snaps:
        for order in json.loads(snap.read_text(encoding="utf-8")):
            merged[order["id"]] = order
    rows = [t for t in trades(list(merged.values())) if not symbols or t["symbol"] in symbols]
    print(f"{DIM}{len(snaps)} snapshot(s), {len(merged)} orders: {len(rows)} filled "
          f"bracket entr(ies){RESET}")
    # Exits the broker has pruned are still in the audit log's trade_outcome.
    audit = {}
    for line in Path(settings.audit_log_path).read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("event") == "trade_outcome":
            audit[row["outcome"]["symbol"], round(float(row["outcome"].get("entry_fill") or 0), 2)] = row
    async with httpx.AsyncClient() as client:
        for t in rows:
            known = audit.get((t["symbol"], round(t["fill"], 2)))
            if t["exit"] is None and known and known["outcome"].get("exit_fill"):
                t["exit"] = float(known["outcome"]["exit_fill"])
                t["how"] = f"{known['outcome'].get('exit_reason')} (from audit log)"
            start = (t["signal"] or t["filled"]) - timedelta(days=1)
            # The free data plan refuses SIP bars from the last 15 minutes, and
            # an exit the broker pruned has no time: look a week out, never now.
            end = (t["exit_at"] + timedelta(hours=2)) if t["exit_at"] else (t["filled"] + timedelta(days=7))
            end = min(end, datetime.now(timezone.utc) - timedelta(minutes=20))
            try:
                tape = await bars(client, t["symbol"], start, end)
            except httpx.HTTPError as exc:
                print(f"{t['symbol']}: bars unavailable ({exc})")
                tape = []
            show(t, tape)
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--symbol", nargs="*", default=[])
    raise SystemExit(asyncio.run(main(p.parse_args().symbol)))
