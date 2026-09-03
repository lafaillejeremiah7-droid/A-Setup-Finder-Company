# A+ Setup Finder Company

A rule-based MGC futures signal system designed to identify high-quality LONG / SHORT setups while respecting the active prop-firm account rules.

## Core Technology

The company backend, strategy engine, data-processing layer, risk engine, backtesting system, and future dashboard API are built in **Python 3**.

Tradovate is used only for **manual trade execution**. The company does not require a Tradovate custom indicator and does not use JavaScript for strategy logic.

## Objective

The system analyzes:

- MGC trendlines
- Support / resistance
- Order-flow absorption
- Market structure confirmation
- DXY direction
- Prop-firm risk limits

Final output:

- `LONG`
- `SHORT`
- `NO TRADE`

Every approved LONG/SHORT signal must include:

- Entry
- Stop Loss
- Take Profit
- Contract size
- Dollar risk
- Risk / Reward
- Prop-firm rule check

## Architecture

### 1. MGC Analysis Bot

Responsible for:

- ATR / volatility normalization
- Confirmed swing highs / lows
- HH / HL / LH / LL market structure
- Break of structure confirmation
- Trendlines and interactions
- Support / resistance zones and interactions
- Absorption
- Bullish / bearish confirmation

### 2. DXY Bot

Responsible for determining:

- DXY bullish
- DXY bearish
- DXY neutral
- Whether DXY supports or conflicts with the MGC setup

### 3. Signal & Risk Bot

Combines MGC + DXY analysis and is responsible for:

- Final signal approval
- Entry price
- Structural invalidation
- Stop Loss
- Take Profit
- Risk / Reward
- Position size
- Remaining prop-firm drawdown
- Prop-rule compliance

Final output is always one of `LONG`, `SHORT`, or `NO TRADE`.

## Strategy Logic

### LONG

A LONG candidate requires:

1. Bullish MGC location
2. Sell-side absorption
3. Bullish structure confirmation
4. DXY confirmation under the v1 rules
5. Valid structural SL
6. Valid structural TP
7. Acceptable R:R
8. Prop-firm risk check passes

### SHORT

A SHORT candidate requires:

1. Bearish MGC location
2. Buy-side absorption
3. Bearish structure confirmation
4. DXY confirmation under the v1 rules
5. Valid structural SL
6. Valid structural TP
7. Acceptable R:R
8. Prop-firm risk check passes

If required conditions are not satisfied, output `NO TRADE`.

## Prop-Firm Awareness

The system must know the active account rules, including:

- Starting balance
- Current balance / equity
- Maximum loss
- Drawdown type
- Current drawdown floor
- Maximum contracts
- Daily loss limit
- Consistency rule
- Remaining loss allowance

Prop-firm rules must be configurable rather than permanently hard-coded to one account.

A setup is rejected if it violates the configured account rules.

## Backtesting and Validation

The repository includes an event-driven bar-level backtest harness designed to prevent ordinary look-ahead leakage.

Key rules:

- the strategy sees only completed MGC bars at each historical instant;
- it sees only DX bars completed by that same instant;
- a signal created from a completed candle cannot use that candle's earlier high/low as a post-entry outcome;
- same-bar stop/target ambiguity defaults to conservative stop-first handling;
- gap-through-stop events fill at the first available open rather than the theoretical stop;
- commissions and slippage are explicit inputs;
- signal-close and next-bar-open entry timing can be compared;
- unsorted or duplicate historical timestamps are rejected.

This is a **bar-level screening engine**, not final proof of execution quality. Because GIBRC depends on executed bid/offer flow for absorption, final validation requires sufficiently granular historical order-flow data and ultimately tick/trade replay for stop/target ordering and slippage realism.

See `docs/VALIDATION_PROTOCOL.md` for the falsification-first research process.

## Core Definition Groups

The system uses 18 required definition groups:

1. Candle / Market Data
2. ATR / Volatility
3. Swing High / Swing Low
4. Market Structure
5. Structure Confirmation / Break
6. Trendline
7. Trendline Interaction
8. Support / Resistance
9. S/R Interaction
10. Order-Flow Aggression
11. Absorption
12. DXY State
13. Entry / Trigger
14. Invalidation / Stop Loss
15. Take Profit
16. MGC Risk Mathematics
17. Prop-Firm Risk Rules
18. Final Signal Rule

Exact formulas, parameters, assumptions, and testable thresholds belong in `docs/STRATEGY_SPEC.md`.

## Design Principles

- Python 3 is the source-of-truth implementation language
- No subjective chart interpretation inside the signal engine
- No look-ahead bias
- No repainting confirmed historical signals
- No LONG/SHORT signal without required confirmation
- No arbitrary SL/TP placement
- No position size that violates configured prop-firm rules
- `NO TRADE` is a valid and expected output
- Every signal must be explainable from its underlying rule results
- Hypothesized thresholds must be distinguished from empirically validated thresholds
- Tradovate execution remains manual

## Development Stages

1. Freeze mathematical definitions in Strategy Spec v1
2. Build Python market-data foundation
3. Build MGC analysis engine
4. Build DXY filter
5. Build signal / risk engine
6. Build no-lookahead backtest foundation
7. Connect the complete GIBRC setup state machine to historical replay
8. Validate with realistic costs, OOS testing, parameter surfaces, and walk-forward analysis
9. Run live in paper / shadow mode
10. Build dashboard/API layer
11. Use validated dashboard signals for manual execution in Tradovate

## Current Status

**Validation infrastructure in development.**

Implemented foundation includes market-data normalization, ATR, confirmed pivots with explicit confirmation delay, market-structure classification, BOS checks, trendline construction/interactions, support/resistance zones, fail-closed absorption, DXY state, structural trade planning, prop-risk gating, final signal composition, and an event-driven no-lookahead bar backtester with explicit execution friction.

The next major engineering gap is the exact stateful GIBRC setup lifecycle that connects location -> absorption -> later structure confirmation -> trade plan -> risk -> final signal during historical replay.

Do not treat the system as production-ready until that end-to-end state machine has been connected to clean historical MGC/DX data, tested out of sample, stress-tested for costs and parameter sensitivity, and validated in live shadow mode.
