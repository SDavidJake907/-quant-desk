# Position sizing — per-stock rules, with the numbers

Written 2026-10-04 from `scripts/sizing_research.py`, `position_size_test.py`,
`flatten_gap_test.py` and the exit-plan replay, on the 2024-09 → 2026-09 Alpaca
30m store (916 setups that pass the live gates, Friday flatten applied, 10bps
round trip). Policy `9ed12e112119`. Research only: nothing here changes a live
setting during window 15.

**The short answer:** keep sizing mechanical and roughly where it is. Per-stock
scaling by speed, size or beta did not beat the current rule in both halves of
the data, and no sizing scheme turns a strategy whose two-year expectancy is
−0.024R into a profitable one. Size amplifies an edge; it cannot create one.

**What the model decides about size: nothing.** On 307 point-in-time setups
gemma4:12b called 305 "strong" — a sizing signal that is the same for every
trade — and its per-stock exit choices lost 39.9R against the plain bracket
(WORKLIST §27). Sizing stays arithmetic that a backtest can check.

---

## 1. How sizing actually works today

`execution/sizing.size_position` takes the smaller of

    shares by risk     = equity × risk_pct / (entry − stop)
    shares by position = equity × max_position_pct / entry

At `risk_pct = 1%` the risk rule only wins when the stop is more than
1 / 0.20 = **5%** away. `min_risk_pct` is 1.5% and the median stop is ~2%, so the
**20% position cap binds on nearly every trade**. Real risk is therefore
20% × stop distance:

| | value |
|---|---|
| declared risk per trade | 1.00% |
| **actual mean risk per trade** | **0.43%** |
| spread of actual risk across trades | 0.15% (sd) |
| a 1.5% stop risks | 0.30% |
| a 3.0% stop risks | 0.60% |

So without anyone choosing it, a stock with a wide stop (usually a faster mover)
already carries about twice the risk of one with a tight stop.

## 2. Does what a stock IS change what a trade earns?

Terciles cut on the training two-thirds (to 2026-03-17) and applied unchanged to
the holdout. Mean R per trade, Friday flatten:

| feature | low / high cut | train low · mid · high | holdout low · mid · high | same best in both? |
|---|---|---|---|---|
| stop distance | 1.76% / 2.40% | −0.074 · −0.201 · −0.122 | +0.152 · +0.347 · +0.262 | no |
| 30m ATR | 0.81% / 1.22% | −0.208 · −0.005 · −0.183 | −0.041 · +0.254 · +0.543 | no |
| daily volatility (ann.) | 35.1% / 52.3% | −0.127 · −0.223 · −0.047 | +0.173 · +0.327 · +0.245 | no |
| **daily ATR** | 3.32% / 4.76% | −0.161 · −0.211 · **−0.024** | +0.145 · +0.171 · **+0.412** | **high** |
| dollar volume / 30m bar | $33M / $74M | −0.199 · **−0.050** · −0.147 | +0.033 · **+0.394** · +0.310 | **mid** |
| beta to S&P 500 | 0.95 / 1.49 | −0.024 · −0.277 · −0.099 | −0.141 · +0.701 · +0.365 | no |

Two of six features keep the same best tercile — **exactly what chance gives**
(each has a one-in-three chance). Read them as hypotheses:

- **Faster daily movers (ATR ≥ 4.8% of price) did best in both halves.** It
  agrees with the trade anatomy (winners' daily context was stronger) and with
  the intuition that the strategy needs room to run. Unconfirmed.
- **Mid-liquidity names ($33–74M per bar) did best in both halves.** No
  mechanism argues for the middle; likely noise.

## 3. The account under five sizing schemes

5 slots, compounding, cash-limited (no margin), one position per symbol.

| scheme | 2 years | max DD | 1st half | 2nd half |
|---|---|---|---|---|
| **A current** — 1% risk, 20% cap | −11.6% | 23.9% | −10.3% | −1.7% |
| B equal risk — 0.45% every trade, no cap | −13.9% | 23.5% | −13.6% | **−0.7%** |
| C volatility target — 20% × median vol / stock vol, 5–30% | −16.5% | 23.4% | −12.9% | −5.1% |
| D liquidity tiers — 25 / 20 / 15% by $-volume tercile | **−10.0%** | 25.1% | **−4.8%** | −5.5% |
| E beta — 20% / beta, 10–30% | −13.9% | **19.9%** | −8.7% | −6.6% |

**No scheme wins in both halves**, and every one loses. Equal risk is best in
the second half and worst-but-one in the first; liquidity tiers the reverse.
The differences are inside the noise of ~320 trades a half.

Raising the cap does not help either (`position_size_test.py`): 25% → −14.5%,
30% → −13.4%, 30% on 2× margin → −17.3%, drawdown rising to 32.6%.

## 4. What the research says

**Volatility targeting.** Moreira & Muir (2017) found that scaling exposure down
when volatility is high raised Sharpe ratios across the market, value, momentum
and other factors. Cederburg, O'Doherty, Wang & Yan then showed the strategies
behind those results are not implementable in real time, and that reasonable
out-of-sample versions underperformed the unmanaged portfolios in 72 of 103
equity strategies. Scheme C above lost on this data too.

**Except for momentum.** Barroso & Santa-Clara (*Momentum has its moments*)
scaled the momentum factor to a constant 12% volatility using its own recent
volatility, which nearly eliminated momentum crashes and nearly doubled its
Sharpe ratio (16.5% vs 14.5% a year, 1926–2011). This applies to the separate
monthly momentum ENGINE (WORKLIST §5–6), not to the swing setups.

**Kelly, and why to stay small.** The growth-optimal risk per trade is roughly
mean R / variance of R. Over-betting is punished far more than under-betting,
and edges estimated from a sample are usually overstated, so practitioners use
a quarter to a half of it: half Kelly keeps ~75% of the growth for ~25% of the
variance; full Kelly has roughly even odds of halving the account.

On the 307 gated setups since 2026-03-17 (sd of R = 1.66):

| if the true edge per trade is | quarter-Kelly risk per trade |
|---|---|
| −0.024R (two-year, flatten included) | **0%** |
| +0.05R | **0.45%** ← current actual risk |
| +0.10R | 0.91% |
| +0.216R (last six months) | 1.96% |

The current 0.43% is what quarter Kelly would choose for an edge of about
+0.05R — the right size for "possibly a small edge, unproven". Raising risk is
justified only by evidence that the edge is larger, not by a good six months.

**Liquidity.** Market impact follows the square-root law: cost ≈ Y·σ·√(Q/V),
with Y of order one, σ the daily volatility, Q the order and V daily volume.
A 20% position is ~$20,000. The thinnest stock the gates admit trades $20M per
30m bar, ~$260M a day, so Q/V ≈ 0.008% and impact ≈ 0.01·σ — negligible. At
this account size liquidity does not constrain sizing; it starts to around
$10M+ of capital.

## 5. The rules

**Now (window 15, frozen):** unchanged — 1% risk, 20% cap, 5 slots, 3% daily
loss stop. Real risk ~0.43%/trade.

**At window 16, only on evidence:**

| rule | number | condition |
|---|---|---|
| risk per trade | 0.4–0.5% | default; equal-risk sizing (scheme B) is the cleaner way to get it, since today's level is an accident of the cap |
| raise risk toward 1% | quarter Kelly of the measured edge | only once an edge of ≥ +0.10R holds on data that includes a bear market (2018–2022) |
| position cap | 20% | 5 × 20% = 100%: no margin |
| per-stock scaling by speed | none yet | test "daily ATR ≥ 4.8% sizes up" on 2018–2022 first; it is a one-in-three chance match today |
| per-stock scaling by size / liquidity | none | impact is negligible at this size; the mid-tercile result has no mechanism |
| beta scaling | none | flips between halves |
| sector-leading setups | candidate to size up | the only live lead (WORKLIST §26, p=0.030), pending 2018–2022 |
| momentum engine (separate) | 12% annual volatility target | Barroso & Santa-Clara; the one place vol scaling has robust support |
| the model | no role in sizing | conviction is constant; its exit choices lost money |

## 6. Re-running

    python scripts/sizing_research.py      # sections 2 and 3, all CPU cores
    python scripts/position_size_test.py   # cap 20 / 25 / 30%

## Sources

- Moreira & Muir, *Volatility-Managed Portfolios*, Journal of Finance 2017 — [NBER w22208](https://www.nber.org/papers/22208)
- Cederburg, O'Doherty, Wang & Yan, *On the Performance of Volatility-Managed Portfolios* — [paper](https://www.lehigh.edu/~xuy219/research/COWY.pdf), [summary](https://alphaarchitect.com/?p=59058)
- Barroso & Santa-Clara, *Momentum Has Its Moments* — [paper](https://run.unl.pt/bitstream/10362/11836/1/Barroso_2012.pdf), [summary](https://alphaarchitect.com/avoiding-momentum-crashes/)
- Fractional Kelly — [FlashAlpha](https://flashalpha.com/concepts/kelly-criterion), [LuxAlgo](https://www.luxalgo.com/library/concept/kelly-criterion/)
- Square-root market impact — [Bouchaud](https://bouchaud.substack.com/p/the-square-root-law-of-market-impact), [Bouchaud slides](https://staff.fnwi.uva.nl/a.khedher/winterschool/18slidesBouchaud.pdf)
