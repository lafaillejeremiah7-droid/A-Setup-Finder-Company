# GIBRC v1.0 Validation Protocol

## Purpose

The purpose of validation is to try to **falsify** GIBRC v1.0, not to make the backtest look profitable.

The research reference supplied with this project is explicit that a defensible trading system must survive unseen data, realistic friction, multiple regimes, parameter perturbation, contract rolls, valid within-bar execution ordering, multiple years, and a sufficient number of independent trades. Those principles are adopted here as validation requirements.

This document does **not** replace GIBRC v1.0 with the separate GC-TAER strategy described in the research reference. GIBRC remains the MGC + DX, structure + absorption system defined in `docs/STRATEGY_SPEC.md`.

## Stage 1 — Bar-level no-lookahead harness

The first validation layer uses the repository's event-driven backtest engine.

Requirements:

1. Every input `MarketBar.timestamp` represents the bar's **completion time**.
2. Strategy code receives only the MGC bars completed at the current historical instant.
3. Strategy code receives only DX bars whose completion time is less than or equal to the current MGC completion time.
4. A signal generated from a completed bar may not use that same bar's earlier high/low to determine a post-signal stop or target outcome.
5. Unsorted or duplicate timestamps are invalid input.
6. No ordinary total-volume field may be substituted for executed bid/offer volume when testing absorption.

The bar-level engine is a correctness and screening layer. It is not the final execution model.

## Stage 2 — Execution realism

Every formal result must include explicit trading friction.

At minimum test:

- baseline slippage;
- conservative slippage;
- stress slippage;
- explicit commissions and exchange/broker fees;
- gap-through-stop fills at the first available price rather than the theoretical stop price.

For MGC, slippage assumptions must use MGC economics (`0.1` minimum tick, approximately `$1` per tick per contract), not the standard GC `$10` tick value.

### Same-bar stop/target ambiguity

OHLC bars cannot reveal whether a stop or target traded first when both are touched in one bar.

The bar-level harness therefore defaults to `STOP_FIRST` for conservative validation. `TARGET_FIRST` exists only as a sensitivity scenario. Final validation should replace this ambiguity with tick/trade replay whenever the required historical data are available.

Bar-level MAE/MFE are labelled as bar-level approximations because OHLC cannot prove the exact excursion that occurred before an intrabar exit.

## Stage 3 — Historical data quality

Preferred MGC validation data should use actual tradable futures contracts rather than blindly computing P&L on a synthetic continuous series.

The data pipeline must document:

- source;
- contract symbol;
- contract expiration;
- roll rule;
- timestamp convention and timezone;
- missing bars;
- duplicate handling;
- session boundaries;
- bid/offer executed-volume semantics;
- whether historical fields match the live feed semantics.

DX data must be synchronized separately because the U.S. Dollar Index futures contract is an ICE product, not a COMEX contract.

## Stage 4 — Dataset isolation

Do not use one historical sample for parameter selection and final claims.

Use three logically distinct partitions:

1. **Development** — rule implementation and hypothesis exploration.
2. **Validation** — parameter-neighborhood and design selection.
3. **Locked OOS test** — final untouched evaluation.

The locked OOS partition should not be repeatedly inspected while changing parameters.

After the first locked OOS result is inspected, further parameter changes create a new research version and require a new genuinely unseen evaluation period.

## Stage 5 — Parameter robustness

GIBRC hypothesis parameters must be tested as surfaces/neighborhoods, not as an optimization contest.

Examples include:

- ATR period;
- pivot width;
- structure tolerance;
- BOS buffer;
- trendline touch/break/re-arm ATR multipliers;
- S/R clustering and zone width;
- absorption lookback;
- absorption Z threshold;
- rejection-wick threshold;
- close-location thresholds;
- stop ATR buffer;
- minimum R:R.

A result that is profitable only at one isolated parameter value is evidence against robustness.

Do not introduce unrelated indicators merely because they appear in another strategy. In particular, ADX/EMA/channel parameters from the supplied GC-TAER research framework are **not** GIBRC parameters unless a future, separately versioned research decision adds them.

## Stage 6 — Ablation tests

Run controlled removals of major components to determine whether they add measurable value.

Required ablations should include at least:

- DXY gate on vs off;
- absorption gate on vs off;
- trendline location only vs S/R location only vs either;
- long side vs short side;
- stricter vs looser minimum R:R.

A component that only reduces sample size without improving out-of-sample expectancy or downside risk should be questioned rather than defended by narrative.

## Stage 7 — Metrics

Record at least:

- trade count;
- win rate;
- gross and net P&L;
- profit factor after costs;
- expectancy per trade;
- expectancy in R;
- maximum closed-trade drawdown;
- longest losing streak;
- bar-level MAE/MFE in the bar engine;
- holding time;
- long vs short results;
- session/time-of-day results;
- volatility-regime results;
- DXY-state results;
- absorption-strength buckets;
- baseline vs stressed-cost results;
- concentration of profit in the best few trades.

No single metric is sufficient.

## Stage 8 — Walk-forward validation

After the basic OOS framework is working, add rolling walk-forward windows. The exact window lengths are hypotheses and should be selected before seeing the resulting performance.

Each window must preserve chronology:

`train -> validate -> test -> roll forward`

Future windows may never influence prior windows.

## Stage 9 — Tick / BBO replay

Bar-level validation is not the final standard for a strategy that depends on order flow.

Before production claims, replay sufficiently granular historical data to verify:

- executed bid/offer classification;
- absorption behavior;
- stop/target ordering;
- gaps and fast markets;
- slippage assumptions;
- any material difference between historical and live feed semantics.

If the available historical data cannot reproduce the executed-side information required by the absorption detector, that dataset cannot validate the full GIBRC strategy.

## Stage 10 — Prop-firm probability model

Only after a strategy has positive, robust OOS expectancy after costs should prop-firm challenge simulation be added.

The correct objective is not maximum annual return. It is:

`P(reach profit target before violating the failure boundary)`

Monte Carlo / bootstrap simulations must use the firm's exact verified rules for:

- profit target;
- drawdown calculation;
- daily loss rule;
- consistency rule;
- contract cap;
- time limit, if any.

Do not infer a firm's rule formula when it is not verified.

## Current gate

The repository now has a no-lookahead bar-level execution harness. The next required engineering step is to connect the exact GIBRC setup state machine to this harness and then feed it clean historical MGC executed bid/offer data plus synchronized DX data.

Until that exists, the project has an execution-validating backtest **framework**, not empirical evidence that GIBRC has positive expectancy.
