"""Check scanner/signals/indicators.py against the ORIGINAL Pine scripts run by PineTS.

    .venv/Scripts/python.exe tools/pinets/check_translations.py [SYMBOL]

Runs the operator's Pine downloads (everget's trailing stops, v6) and Pine's
built-ins (builtins_check.pine) on the same candles as the Python versions and
reports, per indicator, how many bars agree after the warm-up. Read-only.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from scanner.signals import indicators as ind  # noqa: E402

PINE = Path("C:/Users/kitti/Downloads/pinescript-indicators/tradingview-pinescript-indicators-master/trailing_stops")
WARMUP = 150
TOL = 1e-6


def pine(candles_file: str, script: Path, *extra: str) -> dict:
    out = subprocess.run(["node", str(ROOT / "tools/pinets/run_pine.mjs"), candles_file, str(script), *extra],
                         capture_output=True, text=True, check=True, cwd=ROOT / "tools/pinets")
    return json.loads(out.stdout)


def agree(name: str, ref, mine, *, rel: bool = True) -> tuple[str, int, int, float]:
    n = ok = 0
    worst = 0.0
    for a, b in list(zip(ref, mine))[WARMUP:]:
        if a is None or b is None or (isinstance(a, float) and a != a):
            continue
        n += 1
        err = abs(a - b) / (abs(a) if rel and abs(a) > 1e-9 else 1.0)
        worst = max(worst, err)
        ok += err <= TOL
    return name, ok, n, worst


def main() -> int:
    sym = sys.argv[1] if len(sys.argv) > 1 else "MRVL"
    rows = json.loads((ROOT / f"data/intraday_alpaca/{sym}__15m__session.json").read_text())[-600:]
    o, h, lo, c, v = ([r[k] for r in rows] for k in range(1, 6))
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump([{"open": r[1], "high": r[2], "low": r[3], "close": r[4], "volume": r[5],
                    "openTime": int(r[0] * 1000)} for r in rows], f)
    cf = f.name
    results = []
    b = pine(cf, ROOT / "tools/pinets/builtins_check.pine")
    results += [agree("EMA 9", b["EMA9"], ind.ema(c, 9)), agree("EMA 21", b["EMA21"], ind.ema(c, 21)),
                agree("RSI 14", b["RSI14"], ind.rsi(c, 14)), agree("ATR 22", b["ATR22"], ind.atr(h, lo, c, 22)),
                agree("MFI 14", b["MFI14"], ind.mfi(h, lo, c, v)), agree("PVT", b["PVT"], ind.pvt(c, v)),
                agree("OBV", b["OBV"], ind.obv(c, v)), agree("ADX", b["ADX"], ind.adx(h, lo, c)),
                agree("Parabolic SAR", b["SAR"], ind.psar(h, lo, c=c)), agree("Schaff STC", b["STC"], ind.stc(c)),
                agree("Connors RSI", b["CRSI"], ind.connors_rsi(c))]
    for title, script, var, mine in (
            ("SuperTrend dir", "supertrend.pine", "dir", ind.supertrend(o, h, lo, c)),
            ("Chandelier dir", "chandelier_exit.pine", "dir", ind.chandelier(h, lo, c)),
            ("NRTR dir", "nrtr_nick_rypock_trailing_reverse.pine", "trend", ind.nrtr(c)),
            ("HalfTrend dir", "halftrend.pine", "trend", [0 if d == 1 else 1 for d in ind.halftrend(h, lo, c)])):
        ref = pine(cf, PINE / script, f'plot({var}, "X")')["X"]
        if title.startswith("NRTR"):
            ref = [None if x is None else (1 if x >= 0 else -1) for x in ref]
        results.append(agree(title, ref, mine, rel=False))
    print(f"{sym} 15m, last 600 candles, compared after {WARMUP} warm-up candles (tolerance {TOL:g})")
    for name, ok, n, worst in results:
        print(f"  {name:16} {ok:4}/{n:<4} {'MATCH' if n and ok == n else 'DIFFERS'}  (worst gap {worst:.2e})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
