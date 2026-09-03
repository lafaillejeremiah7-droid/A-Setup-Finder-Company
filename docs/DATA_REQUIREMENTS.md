# Data Requirements & Validation Gate

This document records the verified v1 market-data path and the remaining runtime checks.

## Decision

Use **Tradovate market data for MGC and ICE U.S. Dollar Index futures (`DX`)** when the account's exchange entitlements expose those products.

This keeps v1 on one market-data architecture and avoids silently mixing a TradingView cash-index feed with futures data.

## MGC Price + Order-Flow Data

Required bar fields:

- timestamp
- open
- high
- low
- close
- total volume
- bid volume
- offer volume

Tradovate chart messages document bar-level `bidVolume` and `offerVolume`. These are the v1 inputs for bar-level delta and absorption research.

Required tick fields when tick-level refinement is enabled:

- trade price
- trade size
- bid price / size
- ask price / size
- timestamp
- tick id

Tradovate tick-chart messages document trade price/size together with bid/ask information. Tick packets can arrive out of chronological order, so the ingestion layer must sort and deduplicate them before strategy use.

### Critical distinction

Resting DOM size is **not** executed aggressive volume. The absorption model must use executed bid/offer bar volume and/or classified trade ticks. It must not treat DOM snapshots as absorption.

**Status: DATA FIELDS VERIFIED IN DOCUMENTATION; ACCOUNT ENTITLEMENT + LIVE SAMPLE STILL REQUIRE RUNTIME CHECK.**

## DXY / USDX Data

Use the ICE U.S. Dollar Index futures contract `DX` as the v1 DXY filter source.

ICE publishes `DX` as the symbol for U.S. Dollar Index futures. Current Tradovate trading-products/rate materials include ICE U.S. products and list `DX` as U.S. Dollar Index.

Therefore:

`DXY_FILTER_SOURCE = DX futures`

Do not hard-code a perpetual contract month. The live adapter must resolve the active DX futures contract through Tradovate metadata/roll logic.

**Status: PRODUCT PATH VERIFIED; EXACT ACTIVE CONTRACT + USER ENTITLEMENT REQUIRE RUNTIME RESOLUTION.**

## Initial Timeframes

### MGC
- 4H macro context
- 30m location / S&R context
- 5m execution / absorption

### DX
- 4H / 1H higher-timeframe context
- 15m / 5m confirmation

These are configurable hypotheses and must be backtested.

## Prop-Firm Account Data

Required:

- current account balance/equity
- current drawdown floor or sufficient information to calculate it
- maximum contracts
- active rule configuration

The strategy engine receives these values through a provider/configuration boundary so core strategy logic is not permanently tied to one prop firm.

**Status: RULE SOURCE + ACCOUNT DATA PATH MUST BE VERIFIED BEFORE LIVE RISK ENFORCEMENT.**

## Data Quality Gates

Return `NO_TRADE` whenever a required input is missing, stale, malformed, or ambiguous.

Minimum checks:

1. timestamps parse successfully
2. completed bars are ordered
3. completed bars are deduplicated
4. OHLC values are finite
5. `high >= max(open, close)`
6. `low <= min(open, close)`
7. volumes are non-negative when present
8. MGC and DX are fresh enough for the configured timeframe
9. active contract mapping is known
10. absorption-dependent signals are disabled if bid/offer executed-volume fields are unavailable

## Security

Never commit:

- Tradovate passwords
- access tokens
- API secrets
- prop-firm credentials

Secrets must be supplied through environment variables or an external secret store.

Future live adapter environment variables:

- `TRADOVATE_ACCESS_TOKEN`
- `TRADOVATE_ENV=demo|live`

## Implementation Gate

The deterministic data-normalization and market-structure primitives may now be implemented.

A production LONG/SHORT signal engine is **not** considered complete until:

1. a real MGC sample confirms bid/offer executed-volume fields under the user's entitlement,
2. a real DX subscription resolves the active contract and streams successfully,
3. prop-account data/rules are wired and validated,
4. backtest/live code paths use the same strategy functions.
