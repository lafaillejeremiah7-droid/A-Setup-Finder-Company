# Data Requirements & Validation Gate

This document records what data the strategy requires before implementation begins.

## MGC Price Data

Required:

- OHLCV bars
- 5m execution bars
- 30m/4H context bars (initial hypothesis)
- timestamps with consistent session/timezone handling

Tradovate's documented Market Data API supports real-time quotes, DOM, charts, and histograms through WebSocket market-data requests. Chart requests support minute bars and optional histogram data.

**Status: FEASIBLE, integration not yet authenticated/tested against this account.**

## MGC Absorption / Order Flow

Required to implement the intended strategy faithfully:

- trade-level or price-level information sufficient to distinguish aggressive buying from aggressive selling, OR
- an equivalent documented histogram/order-flow feed whose semantics allow this calculation.

Tradovate documents quote, DOM, chart, histogram, and tick-chart market data. However, the strategy must not assume that ordinary OHLCV or resting DOM size equals executed bid/ask volume.

Before the Absorption module is coded, we must validate exactly which Tradovate response fields are available for MGC and whether they provide the executed-side information needed for the absorption formula.

**Status: BLOCKING VALIDATION REQUIRED.**

## DXY Data

Required:

- 5m/15m DXY bars for execution-time state
- 1H/4H bars for higher-timeframe context (initial hypothesis)

We must identify a reliable DXY data source accessible to the application. Do not assume that the futures brokerage feed exposes the cash ICE U.S. Dollar Index under the same `DXY` symbol used by charting platforms.

**Status: DATA SOURCE MUST BE VERIFIED.**

## Prop-Firm Account Data

Required:

- current account balance/equity
- current drawdown floor or sufficient information to calculate it
- max contracts
- active rule configuration

The strategy engine should receive these values through a provider/configuration boundary so the core strategy is not tied permanently to one prop firm.

**Status: RULE SOURCE + ACCOUNT DATA PATH MUST BE VERIFIED BEFORE LIVE RISK ENFORCEMENT.**

## Security

Never commit:

- Tradovate passwords
- access tokens
- API secrets
- prop-firm credentials

Secrets must be supplied through environment variables or an external secret store.

## Implementation Gate

Coding the deterministic price-structure primitives can begin independently, but a production LONG/SHORT signal engine must not be declared complete until all four data paths above are validated.
