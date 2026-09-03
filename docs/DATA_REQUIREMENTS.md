# Data Requirements & Validation Gate

This document records the verified v1 market-data path and the remaining runtime checks.

## Decision

Use a verified futures data source for **MGC** and **ICE U.S. Dollar Index futures (`DX`)**. Tradovate remains manual execution only and is not required as the production market-data source if another source is selected and its semantics are verified.

Do not silently mix incompatible data semantics across backtest and live operation.

## MGC Price + Order-Flow Data

Required bar fields:

- completion timestamp
- open
- high
- low
- close
- total volume
- bid volume executed at bid
- offer volume executed at ask

For the existing bar-level absorption research, `bidVolume` / `offerVolume` or an equivalent verified executed-side classification is required.

Required tick/trade fields when tick-level refinement is enabled:

- trade price
- trade size
- bid price / size, when available
- ask price / size, when available
- timestamp
- stable sequence/tick identifier when available

Historical packets must be sorted and deduplicated before strategy use.

### Critical distinction

Resting DOM size is **not** executed aggressive volume. The absorption model must use executed bid/offer bar volume and/or classified trade ticks. It must not treat DOM snapshots or ordinary total volume as absorption.

### Verified Databento path

Databento `GLBX.MDP3` supports MGC history well before the requested 2015 start. Its `trades` schema includes a trade `side` field where `A` means a seller aggressor and `B` means a buyer aggressor. For GIBRC aggregation:

- `side=A` -> executed at bid -> `bid_volume`
- `side=B` -> executed at ask -> `offer_volume`
- `side=N` or any unknown side -> strict downloader rejects the affected data rather than inventing classification

The downloader uses Databento volume-continuous symbology only to choose the actual tradable front contract. Databento documents that continuous-contract prices are original and unadjusted. The output keeps the actual mapped instrument ID in the canonical `contract` field, so no synthetic or back-adjusted price series is created.

**Status: VERIFIED SOURCE SEMANTICS; USER ENTITLEMENT/API KEY AND FULL-SPAN SIDE COMPLETENESS MUST STILL BE TESTED AT RUNTIME.**

## DXY / USDX Data

Use the ICE U.S. Dollar Index futures contract `DX` as the v1 DXY filter source unless a later research version explicitly changes the source.

`DXY_FILTER_SOURCE = DX futures`

Do not hard-code a perpetual contract month. Historical and live pipelines must resolve the appropriate contract and document roll logic.

### Verified Databento coverage limit

Databento `IFUS.IMPACT` provides ICE Futures U.S. DX data beginning **2018-12-23 UTC**, not 2015. Databento normalizes ICE iMpact trade aggressor side when ICE specifies it, but ICE can publish trades where the aggressing side is unspecified; those normalize to `side=N` and must be rejected in strict absorption-grade aggregation.

ICE Consolidated History publicly documents tick-by-tick historical coverage extending 10+ years / up to roughly 15 years depending on the offering. However, the public material checked for this project does **not** establish an export/API field contract guaranteeing aggressor-side classification for the 2015-2018 DX span. Under the project rule, that source is therefore not accepted yet for executed-side volume. It may be added only after a licensed sample/file specification proves the needed trade-side field.

**Status: DATABENTO DX VERIFIED FROM 2018-12-23; 2015-2018 DX EXECUTED-SIDE SOURCE REMAINS UNVERIFIED AND MUST NOT BE FABRICATED.**

## Initial Timeframes

### MGC
- 4H macro context
- 30m location / S&R context
- 5m execution / absorption

### DX
- 4H / 1H higher-timeframe context
- 15m / 5m confirmation

These are configurable hypotheses and must be backtested.

## Timestamp Semantics

The no-lookahead backtest engine assumes every `MarketBar.timestamp` represents the **bar completion time**.

If a vendor labels a candle by its opening time, the ingestion layer must convert or attach the true completion time before replay.

Why this matters: at historical instant `t`, the engine exposes only MGC/DX bars whose completion time is known by `t`. Using bar-open labels as though they were close times can leak an entire future candle into the strategy.

Minimum timestamp requirements:

- timezone-aware or consistently normalized timestamps;
- strictly increasing completed-bar sequence;
- no duplicate completion timestamps for the same instrument/timeframe;
- explicit exchange/session calendar handling;
- documented daylight-saving-time handling where local session labels are used.

## Contract and Roll Handling

Formal futures validation should use actual tradable contracts or a rigorously documented continuous-series construction.

For every historical observation, retain enough metadata to identify:

- root symbol;
- specific contract;
- expiration;
- roll date/rule;
- whether price was adjusted for continuity;
- whether P&L is computed on the actual tradable contract rather than an adjusted synthetic price.

Do not allow a back-adjusted continuous series to create synthetic P&L across roll gaps.

## Prop-Firm Account Data

Required:

- current account balance/equity;
- current drawdown floor or sufficient verified information to calculate it;
- maximum contracts;
- active rule configuration.

The strategy engine receives these values through a provider/configuration boundary so core strategy logic is not permanently tied to one prop firm.

**Status: RULE SOURCE + ACCOUNT DATA PATH MUST BE VERIFIED BEFORE LIVE RISK ENFORCEMENT.**

## Data Quality Gates

Return `NO_TRADE` whenever a required input is missing, stale, malformed, or ambiguous.

Minimum checks:

1. timestamps parse successfully;
2. completed bars are strictly ordered;
3. completed bars are deduplicated;
4. OHLC values are finite;
5. `high >= max(open, close)`;
6. `low <= min(open, close)`;
7. volumes are non-negative when present;
8. MGC and DX are fresh enough for the configured timeframe;
9. active/historical contract mapping is known;
10. absorption-dependent signals are disabled if executed-side volume is unavailable;
11. bar timestamp semantics are known;
12. historical contract roll logic is documented;
13. backtest and live order-flow fields have compatible semantics.

## Backtest Data Tiers

### Tier 1 — Bar-level research

Use completed bars with verified executed bid/offer volume for strategy screening and no-lookahead logic testing.

This tier can model explicit slippage, costs, gaps, and conservative same-bar ambiguity, but it cannot prove exact intrabar order.

### Tier 2 — Higher-resolution execution validation

Use one-minute or finer data to improve fill-path realism where compatible with the strategy inputs. Ordinary one-minute OHLCV alone still cannot recreate true absorption.

### Tier 3 — Tick/trade/BBO replay

Final execution validation should use sufficiently granular historical trades/quotes to resolve:

- stop vs target order;
- fast-market gaps;
- executed-side classification;
- realistic slippage assumptions;
- feed-specific absorption behavior.

## Security

Never commit:

- Tradovate passwords;
- access tokens;
- API secrets;
- prop-firm credentials;
- paid market-data credentials.

Secrets must be supplied through environment variables or an external secret store.

## Implementation Gate

The deterministic normalization, market-structure, absorption, DXY, trade-plan, risk, final-signal, and no-lookahead backtest primitives are implemented.

A production LONG/SHORT system is **not** considered empirically validated until:

1. a clean historical MGC dataset confirms executed bid/offer semantics;
2. synchronized historical DX data and roll handling are verified;
3. the exact stateful GIBRC setup lifecycle is connected to the replay engine;
4. realistic friction is included;
5. out-of-sample, parameter-sensitivity, and walk-forward tests are completed;
6. the system survives live shadow validation;
7. prop-account rules are verified before any prop-specific probability simulation.
