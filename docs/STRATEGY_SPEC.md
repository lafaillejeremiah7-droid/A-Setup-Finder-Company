# Strategy Specification v1

## Purpose

This document is the source of truth for the A+ Setup Finder Company. Every live signal, backtest, and Tradovate integration must implement the same definitions.

Status labels:

- **LOCKED** — structural definition suitable for implementation.
- **HYPOTHESIS** — numerical threshold that must be tested and may change.
- **DATA-DEPENDENT** — final implementation depends on the exact market-data fields available.

The v1 output is `LONG`, `SHORT`, or `NO_TRADE`, accompanied by entry, stop, target, contracts, dollar risk, R:R, and prop-rule status.

---

## 1. Candle / Market Data — LOCKED

For completed bar `t`:

- `O[t]`: open
- `H[t]`: high
- `L[t]`: low
- `C[t]`: close
- `V[t]`: total volume
- `AskV[t]`: volume executed at the ask, when available
- `BidV[t]`: volume executed at the bid, when available
- `T[t]`: timestamp

Structural confirmation uses completed bars. Intrabar order-flow measurements may update live but cannot retroactively turn an unconfirmed historical structure event into a confirmed one.

## 2. ATR / Volatility — LOCKED + HYPOTHESIS

True Range:

`TR[t] = max(H[t]-L[t], abs(H[t]-C[t-1]), abs(L[t]-C[t-1]))`

Use Wilder ATR.

Initial period: `ATR_PERIOD = 14` **(HYPOTHESIS)**.

ATR normalizes distances used by trendline, S/R, break, and stop buffers.

## 3. Swing High / Swing Low — LOCKED + HYPOTHESIS

Initial pivot width: `PIVOT_N = 3` **(HYPOTHESIS)**.

Swing high at `t`:

`H[t]` is strictly greater than each of the previous `N` highs and following `N` highs.

Swing low at `t`:

`L[t]` is strictly lower than each of the previous `N` lows and following `N` lows.

A pivot is not known live until the following `N` bars have completed. Backtests must respect this confirmation delay.

## 4. Market Structure — LOCKED

Using confirmed pivots:

- `HH`: latest swing high > previous swing high beyond tolerance.
- `LH`: latest swing high < previous swing high beyond tolerance.
- `HL`: latest swing low > previous swing low beyond tolerance.
- `LL`: latest swing low < previous swing low beyond tolerance.

`BULLISH = HH + HL`

`BEARISH = LH + LL`

All conflicting/insufficient combinations are `NEUTRAL`.

Initial equality/noise tolerance: `0.05 * ATR(14)` **(HYPOTHESIS)**.

## 5. Structure Confirmation / Break — LOCKED + HYPOTHESIS

Bullish break:

`C[t] > relevantSwingHigh + BOS_BUFFER`

Bearish break:

`C[t] < relevantSwingLow - BOS_BUFFER`

Initial `BOS_BUFFER = 0.05 * ATR(14)` **(HYPOTHESIS)**.

Wicks alone do not confirm BOS.

For reversal confirmation, the relevant swing is the most recent confirmed short-term pivot opposing the intended direction.

## 6. Trendline — LOCKED

Bullish trendline uses two confirmed swing lows `(t1,p1)` and `(t2,p2)` where `p2 > p1`.

Bearish trendline uses two confirmed swing highs where `p2 < p1`.

Slope:

`m = (p2-p1)/(t2-t1)`

Projected value at bar `t`:

`TL[t] = p1 + m*(t-t1)`

Only information available when pivots became confirmed may be used.

## 7. Trendline Interaction — LOCKED + HYPOTHESIS

Touch distance:

`abs(interactionPrice - TL[t]) <= TL_TOUCH_ATR * ATR`

Initial `TL_TOUCH_ATR = 0.15` **(HYPOTHESIS)**.

Bullish trendline uses candle low as primary interaction price; bearish trendline uses candle high.

Confirmed break requires a completed close beyond the projected line by:

`TL_BREAK_ATR * ATR`

Initial `TL_BREAK_ATR = 0.20` **(HYPOTHESIS)**.

A new independent touch cannot be counted until price has moved at least `0.50 * ATR` away from the line **(HYPOTHESIS)**.

## 8. Support / Resistance — LOCKED + HYPOTHESIS

Support is formed from a cluster of at least two confirmed swing lows.

Resistance is formed from a cluster of at least two confirmed swing highs.

Pivots may belong to the same cluster when their normalized price distance is no greater than:

`SR_CLUSTER_ATR * ATR`

Initial `SR_CLUSTER_ATR = 0.25` **(HYPOTHESIS)**.

Zone center v1 = arithmetic mean of member pivot prices.

## 9. S/R Interaction — LOCKED + HYPOTHESIS

Initial half-width:

`SR_HALF_WIDTH = 0.20 * ATR` **(HYPOTHESIS)**.

Touch occurs when the candle range intersects the zone.

Resistance break requires a completed close above the upper boundary plus `0.05 * ATR` **(HYPOTHESIS)**.

Support break requires a completed close below the lower boundary minus the same buffer.

Bullish location = active support and/or valid bullish trendline interaction.

Bearish location = active resistance and/or valid bearish trendline interaction.

## 10. Order-Flow Aggression — LOCKED / DATA-DEPENDENT

When bid/ask execution volume is available:

`Delta[t] = AskV[t] - BidV[t]`

`DeltaPct[t] = Delta[t] / max(AskV[t] + BidV[t], epsilon)`

Aggressive buying = executions at ask.

Aggressive selling = executions at bid.

Implementation must verify the exact semantics and granularity of the live data source before production use.

## 11. Absorption — DATA-DEPENDENT + HYPOTHESIS

Conceptual definition is locked:

**Buy-side absorption:** abnormally strong aggressive buying occurs with abnormally poor upward price progress. This is potential bearish evidence.

**Sell-side absorption:** abnormally strong aggressive selling occurs with abnormally poor downward price progress. This is potential bullish evidence.

V1 candidate measurements:

`AggressionZ = zscore(directionally aggressive volume, lookback)`

`Range = max(H-L, minimumTick)`

`CloseLocation = (C-L)/Range`

Buy absorption candidate requires all of:

1. positive aggressive-flow extreme,
2. `AggressionZ >= ABS_Z_MIN`,
3. limited upward efficiency / failure to close strongly at high,
4. occurrence at bearish location for a SHORT candidate.

Sell absorption is symmetric.

Initial `ABS_Z_MIN = 2.0` **(HYPOTHESIS)**.

**IMPORTANT:** the exact bubble algorithm is NOT considered frozen until the available MGC bid/ask/footprint data is verified. Do not substitute ordinary total volume and call it absorption.

## 12. DXY State — LOCKED + HYPOTHESIS

DXY returns exactly one state: `BULLISH`, `BEARISH`, or `NEUTRAL`.

V1 initial structure-based definition:

- `BULLISH`: confirmed DXY `HH + HL`
- `BEARISH`: confirmed DXY `LH + LL`
- otherwise `NEUTRAL`

Use the same pivot algorithm as Section 3.

Initial execution timeframe: 5m/15m; higher-timeframe context: 1H/4H **(HYPOTHESIS)**.

For v1 gating:

- Gold LONG requires DXY `BEARISH`.
- Gold SHORT requires DXY `BULLISH`.
- DXY `NEUTRAL` produces `NO_TRADE` until testing demonstrates that neutral should be allowed.

## 13. Entry / Trigger — LOCKED

A candidate does not become a signal merely because absorption occurs.

LONG trigger sequence:

1. bullish location exists,
2. sell-side absorption qualifies,
3. bullish structure confirmation occurs,
4. DXY state is bearish,
5. risk checks pass.

SHORT trigger is symmetric.

V1 entry price = close of the completed confirmation bar **(HYPOTHESIS to compare against next-open/retest execution)**.

## 14. Invalidation / Stop Loss — LOCKED + HYPOTHESIS

LONG invalidation anchor = setup swing low / structural low responsible for the setup.

SHORT invalidation anchor = setup swing high / structural high responsible for the setup.

Initial stop buffer:

`SL_BUFFER = 0.15 * ATR` **(HYPOTHESIS)**.

LONG:

`SL = invalidationLow - SL_BUFFER`

SHORT:

`SL = invalidationHigh + SL_BUFFER`

Never tighten the structural stop solely to force a desired contract quantity.

## 15. Take Profit — LOCKED

LONG target = nearest valid opposing resistance/structural swing above entry.

SHORT target = nearest valid opposing support/structural swing below entry.

Target must exist before signal approval.

Do not manufacture a farther target solely to satisfy R:R.

## 16. MGC Risk Mathematics — LOCKED

MGC contract size = 10 troy ounces.

Price movement of `1.0` = approximately `$10` per MGC contract; minimum `0.1` price tick = `$1` per contract.

`RiskPerContract = abs(Entry-SL) * 10`

`RewardPerContract = abs(TP-Entry) * 10`

`RR = RewardPerContract / RiskPerContract`

`TotalRisk = RiskPerContract * Contracts`

Initial minimum `RR = 2.0` **(HYPOTHESIS)**.

## 17. Prop-Firm Risk Rules — CONFIGURATION + LOCKED ENGINE BEHAVIOR

The risk engine accepts configurable account rules rather than embedding one firm's rules in strategy logic.

Required configuration fields:

- starting balance
- current balance/equity
- maximum loss
- drawdown type and exact calculation
- current drawdown floor
- maximum contracts
- daily loss limit, if any
- consistency rule, if any
- maximum permitted risk per trade

Current account context to support first: nominal `$25,000`, maximum loss `$1,500`, no daily loss limit, maximum `2` contracts, EOD drawdown, `40%` consistency. These values must be verified against the firm's current written rules before production risk enforcement.

`RemainingLossAllowance = currentEquity - currentDrawdownFloor`

A signal is rejected if its worst-case configured loss would violate the drawdown floor, contract cap, per-trade risk cap, or another active prop rule.

## 18. Final Signal Rule — LOCKED

Allowed outputs:

- `LONG`
- `SHORT`
- `NO_TRADE`

LONG requires:

`BullishLocation AND SellAbsorption AND BullishConfirmation AND DXY_Bearish AND RiskPass AND RRPass`

SHORT requires:

`BearishLocation AND BuyAbsorption AND BearishConfirmation AND DXY_Bullish AND RiskPass AND RRPass`

Everything else returns `NO_TRADE`.

Each LONG/SHORT result must include:

- timestamp
- symbol
- direction
- entry
- stop
- target
- contracts
- dollar risk
- R:R
- DXY state
- rule-by-rule pass/fail reasons

Duplicate alerts for the same confirmed setup must be suppressed until that setup is invalidated/completed or a genuinely new location/absorption/confirmation sequence forms.

---

# Non-Negotiable Engineering Rules

1. No look-ahead bias.
2. No repainting confirmed historical signals.
3. Closed-bar structure rules remain closed-bar rules in both backtest and live operation.
4. Backtest and live engines must call the same strategy functions.
5. Unknown/missing data produces `NO_TRADE`, never a guessed LONG/SHORT.
6. Absorption cannot be declared implemented until the required order-flow data has been verified.
7. Hypothesis thresholds must be configurable and tested rather than silently treated as proven.

# Next Validation Gate

Before coding the signal engine, verify the exact data path for:

1. MGC OHLCV
2. MGC bid/ask or footprint/order-flow data needed for absorption
3. DXY OHLC data
4. Live account/risk information needed to enforce the prop rules

Once these inputs are verified, implementation can begin without changing the meaning of the strategy.
