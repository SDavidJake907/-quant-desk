"""
signals.py - momentum, trend, trendline, entry and exit engine for stocks.

Input : DataFrame (DatetimeIndex) with columns open, high, low, close, volume
Output: same DataFrame plus indicators, entry/exit signals, stops and a trade log.

Pure pandas/numpy - no TA library needed.
No lookahead: pivots/trendlines only count after they are confirmed.
"""
from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class Config:
    # trend
    ema_fast: int = 21
    ema_mid: int = 50
    ema_slow: int = 200
    adx_len: int = 14
    adx_min: float = 20.0          # below this = chop, no entries
    st_len: int = 10               # supertrend ATR length
    st_mult: float = 3.0
    # momentum
    rsi_len: int = 14
    rsi_entry_min: float = 50.0
    rsi_entry_max: float = 75.0    # don't chase overbought
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    roc_len: int = 20
    vol_len: int = 20
    vol_mult: float = 1.2          # breakout volume must be 1.2x the 20-bar average
    # trendlines
    pivot_left: int = 5
    pivot_right: int = 5           # bars needed to confirm a pivot
    # risk / exits
    atr_len: int = 14
    stop_atr: float = 2.0          # initial stop = entry - 2 ATR
    trail_atr: float = 3.0         # chandelier trail = highest high - 3 ATR
    target_r: float = 3.0          # flag partial profit at 3R
    rsi_exhaust: float = 80.0


# ---------------------------------------------------------------- indicators
def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def rma(s, n):  # Wilder smoothing (what TradingView uses for RSI/ATR/ADX)
    return s.ewm(alpha=1 / n, adjust=False).mean()


def rsi(close, n):
    d = close.diff()
    gain = rma(d.clip(lower=0), n)
    loss = rma(-d.clip(upper=0), n)
    rs = gain / loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(100)


def true_range(df):
    pc = df["close"].shift(1)
    return pd.concat([df["high"] - df["low"],
                      (df["high"] - pc).abs(),
                      (df["low"] - pc).abs()], axis=1).max(axis=1)


def atr(df, n):
    return rma(true_range(df), n)


def adx(df, n):
    up = df["high"].diff()
    dn = -df["low"].diff()
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = rma(true_range(df), n)
    plus_di = 100 * rma(pd.Series(plus_dm, index=df.index), n) / tr
    minus_di = 100 * rma(pd.Series(minus_dm, index=df.index), n) / tr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return rma(dx.fillna(0), n), plus_di, minus_di


def macd(close, f, s, sig):
    line = ema(close, f) - ema(close, s)
    signal = ema(line, sig)
    return line, signal, line - signal


def supertrend(df, n, mult):
    a = atr(df, n).values
    hl2 = ((df["high"] + df["low"]) / 2).values
    close = df["close"].values
    upper, lower = hl2 + mult * a, hl2 - mult * a
    fu, fl = upper.copy(), lower.copy()
    direction = np.ones(len(df))
    for i in range(1, len(df)):
        fu[i] = upper[i] if (upper[i] < fu[i - 1] or close[i - 1] > fu[i - 1]) else fu[i - 1]
        fl[i] = lower[i] if (lower[i] > fl[i - 1] or close[i - 1] < fl[i - 1]) else fl[i - 1]
        if direction[i - 1] == -1 and close[i] > fu[i - 1]:
            direction[i] = 1
        elif direction[i - 1] == 1 and close[i] < fl[i - 1]:
            direction[i] = -1
        else:
            direction[i] = direction[i - 1]
    line = np.where(direction == 1, fl, fu)
    return pd.Series(line, index=df.index), pd.Series(direction, index=df.index)


# ---------------------------------------------------------------- trendlines
def trendlines(df, left, right):
    """
    Auto-draws trendlines from confirmed pivots, like you would by hand:
      resistance = line through the last two pivot highs when they're DESCENDING
                   (a down-trendline; breaking it = momentum shift up)
      support    = line through the last two pivot lows when they're RISING
                   (an up-trendline; breaking it = trend is cracking)
    A pivot at bar c is only known at bar c + right, so no lookahead.
    """
    high, low = df["high"].values, df["low"].values
    n, win = len(df), left + right + 1
    hmax = df["high"].rolling(win).max().values
    lmin = df["low"].rolling(win).min().values
    res = np.full(n, np.nan)
    sup = np.full(n, np.nan)
    swing_high = np.full(n, np.nan)
    swing_low = np.full(n, np.nan)
    ph, pl = [], []
    for t in range(win - 1, n):
        c = t - right
        if high[c] == hmax[t] and (not ph or ph[-1][0] != c):
            ph.append((c, high[c]))
        if low[c] == lmin[t] and (not pl or pl[-1][0] != c):
            pl.append((c, low[c]))
        if ph:
            swing_high[t] = ph[-1][1]
        if pl:
            swing_low[t] = pl[-1][1]
        if len(ph) >= 2:
            (i1, p1), (i2, p2) = ph[-2], ph[-1]
            if p2 < p1:
                res[t] = p2 + (p2 - p1) / (i2 - i1) * (t - i2)
        if len(pl) >= 2:
            (i1, p1), (i2, p2) = pl[-2], pl[-1]
            if p2 > p1:
                sup[t] = p2 + (p2 - p1) / (i2 - i1) * (t - i2)
    idx = df.index
    return (pd.Series(res, idx), pd.Series(sup, idx),
            pd.Series(swing_high, idx), pd.Series(swing_low, idx))


# ---------------------------------------------------------------- signals
def crossed_above(a, b):
    return (a > b) & (a.shift(1) <= b.shift(1))


def crossed_below(a, b):
    return (a < b) & (a.shift(1) >= b.shift(1))


def add_indicators(df, cfg=Config()):
    df = df.copy()
    c = df["close"]
    df["ema_fast"] = ema(c, cfg.ema_fast)
    df["ema_mid"] = ema(c, cfg.ema_mid)
    df["ema_slow"] = ema(c, cfg.ema_slow)
    df["rsi"] = rsi(c, cfg.rsi_len)
    df["macd"], df["macd_sig"], df["macd_hist"] = macd(c, cfg.macd_fast, cfg.macd_slow, cfg.macd_signal)
    df["adx"], df["plus_di"], df["minus_di"] = adx(df, cfg.adx_len)
    df["atr"] = atr(df, cfg.atr_len)
    df["st_line"], df["st_dir"] = supertrend(df, cfg.st_len, cfg.st_mult)
    df["roc"] = c.pct_change(cfg.roc_len) * 100
    df["rel_vol"] = df["volume"] / df["volume"].rolling(cfg.vol_len).mean()
    df["res_line"], df["sup_line"], df["swing_high"], df["swing_low"] = trendlines(
        df, cfg.pivot_left, cfg.pivot_right)

    # ---- trend
    df["trend_up"] = (c > df["ema_mid"]) & (df["ema_mid"] > df["ema_slow"]) & (df["st_dir"] == 1)
    df["trend_strong"] = (df["adx"] > cfg.adx_min) & (df["plus_di"] > df["minus_di"])

    # ---- momentum
    hist = df["macd_hist"]
    df["momentum_up"] = ((hist > 0) & (hist > hist.shift(1))
                         & df["rsi"].between(cfg.rsi_entry_min, cfg.rsi_entry_max)
                         & (df["roc"] > 0))
    # 0-100 score for ranking a watchlist (or feeding to your agents)
    df["momentum_score"] = (
        25 * ((df["rsi"] - 30) / 50).clip(0, 1)
        + 25 * (df["roc"] / 20).clip(0, 1)
        + 25 * ((df["adx"] - 15) / 25).clip(0, 1) * (df["plus_di"] > df["minus_di"])
        + 25 * (hist > 0) * (hist > hist.shift(1)).astype(float)
    ).round(1)

    # ---- entries
    # A) trendline / swing-high breakout on volume
    brk_line = crossed_above(c, df["res_line"])
    brk_swing = crossed_above(c, df["swing_high"])
    df["entry_breakout"] = (df["trend_up"] & df["trend_strong"] & df["momentum_up"]
                            & (brk_line | brk_swing) & (df["rel_vol"] > cfg.vol_mult))
    # B) pullback to the fast EMA inside an uptrend, momentum turning back up
    #    (price tagged the 21 EMA in the last 3 bars, now closing back above it
    #     with the MACD histogram rising)
    touched = (df["low"] <= df["ema_fast"] * 1.005).astype(int).rolling(3).max() == 1
    df["entry_pullback"] = (df["trend_up"] & df["trend_strong"] & touched
                            & (c > df["ema_fast"]) & (c > df["open"]) & (df["rsi"] > 45)
                            & (hist > hist.shift(1)))
    df["entry"] = df["entry_breakout"] | df["entry_pullback"]

    # ---- raw exit conditions (the position loop decides which one fires)
    df["exit_st_flip"] = (df["st_dir"] == -1) & (df["st_dir"].shift(1) == 1)
    df["exit_support_break"] = crossed_below(c, df["sup_line"])
    df["exit_trend_lost"] = crossed_below(c, df["ema_mid"])
    df["exhaustion"] = (df["rsi"] > cfg.rsi_exhaust) & (hist < hist.shift(1))

    # don't trust anything until the 200 EMA has warmed up
    warm = np.arange(len(df)) < cfg.ema_slow
    for col in ["entry", "entry_breakout", "entry_pullback"]:
        df.loc[warm, col] = False
    return df


# ---------------------------------------------------------------- position loop
def run_positions(df, cfg=Config()):
    """
    Walks bar by bar holding at most one long position.
    Signals fire on the bar's close; fills are assumed at the NEXT bar's open.
    Exits: stop (initial 2 ATR, then 3 ATR chandelier trail, never moves down),
           supertrend flip, rising-support trendline break, close below 50 EMA.
    """
    n = len(df)
    o, h, l, c = (df[k].values for k in ["open", "high", "low", "close"])
    a = df["atr"].values
    pos = np.zeros(n, dtype=int)
    stop = np.full(n, np.nan)
    reason = np.array([""] * n, dtype=object)
    partial = np.zeros(n, dtype=bool)
    trades = []

    in_pos, pending_entry, pending_exit = False, False, None
    entry_px = stop_px = risk = hh = 0.0
    entry_t, setup, took_partial = None, "", False

    for t in range(n):
        # fills at the open
        if pending_exit and in_pos:
            exit_px = o[t]
            trades.append(dict(entry_time=entry_t, exit_time=df.index[t], setup=setup,
                               entry=entry_px, exit=exit_px,
                               ret_pct=100 * (exit_px / entry_px - 1),
                               r_mult=(exit_px - entry_px) / risk, reason=pending_exit))
            in_pos, pending_exit = False, None
        if pending_entry and not in_pos:
            in_pos, pending_entry = True, False
            entry_px, entry_t = o[t], df.index[t]
            risk = cfg.stop_atr * a[t - 1]
            stop_px, hh, took_partial = entry_px - risk, h[t], False

        if in_pos:
            # intrabar stop hit -> exit at stop (or the open if it gapped through)
            if l[t] <= stop_px:
                exit_px = min(o[t], stop_px)
                trades.append(dict(entry_time=entry_t, exit_time=df.index[t], setup=setup,
                                   entry=entry_px, exit=exit_px,
                                   ret_pct=100 * (exit_px / entry_px - 1),
                                   r_mult=(exit_px - entry_px) / risk, reason="stop"))
                in_pos = False
                reason[t] = "stop"
                continue
            hh = max(hh, h[t])
            stop_px = max(stop_px, hh - cfg.trail_atr * a[t])   # trail only moves up
            pos[t], stop[t] = 1, stop_px
            if not took_partial and c[t] >= entry_px + cfg.target_r * risk:
                partial[t], took_partial = True, True
            for col, why in [("exit_st_flip", "supertrend_flip"),
                             ("exit_support_break", "support_trendline_break"),
                             ("exit_trend_lost", "lost_50ema")]:
                if df[col].iat[t]:
                    pending_exit, reason[t] = why, why
                    break
        elif df["entry"].iat[t] and t + 1 < n:
            pending_entry = True
            setup = "breakout" if df["entry_breakout"].iat[t] else "pullback"
            reason[t] = "entry_" + setup

    out = df.copy()
    out["position"], out["stop"], out["signal"], out["take_partial"] = pos, stop, reason, partial
    return out, pd.DataFrame(trades)


def summarize(trades):
    if trades.empty:
        return "No trades."
    wins = trades[trades.r_mult > 0]
    return (f"trades {len(trades)} | win rate {100 * len(wins) / len(trades):.0f}% | "
            f"avg R {trades.r_mult.mean():.2f} | total R {trades.r_mult.sum():.1f} | "
            f"avg win {wins.ret_pct.mean() if len(wins) else 0:.1f}% | "
            f"avg loss {trades[trades.r_mult <= 0].ret_pct.mean():.1f}%")


def scan(frames, cfg=Config()):
    """frames = {symbol: ohlcv_df}. Returns today's ranked watchlist."""
    rows = []
    for sym, d in frames.items():
        x = add_indicators(d, cfg).iloc[-1]
        rows.append(dict(symbol=sym, close=x.close, momentum=x.momentum_score,
                         trend_up=bool(x.trend_up), adx=round(x.adx, 1), rsi=round(x.rsi, 1),
                         breakout=bool(x.entry_breakout), pullback=bool(x.entry_pullback),
                         stop=round(x.close - cfg.stop_atr * x.atr, 2)))
    return pd.DataFrame(rows).sort_values("momentum", ascending=False)


if __name__ == "__main__":
    import yfinance as yf  # pip install yfinance

    raw = yf.download("NVDA", period="5y", interval="1d", auto_adjust=True, progress=False)
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)
    raw.columns = [col.lower() for col in raw.columns]

    df, trades = run_positions(add_indicators(raw))
    print(summarize(trades))
    print(trades.tail(10).round(2).to_string())
