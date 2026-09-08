# MNQ GEX Bot + Dashboard

Minimal live GEX structural map for trading **MNQ**. The chart/dashboard intentionally focuses on only three structural levels:

1. **Call Wall**
2. **Put Wall**
3. **Gamma Flip**

It also shows:

- current total gamma regime: `POSITIVE / NEGATIVE / NEUTRAL`
- modeled **$GEX per 1% move**
- wall concentration: `WEAK / MODERATE / STRONG / EXTREME`
- source timestamp / freshness

No wall zones. No 0DTE wall lines. No IV high/low display. No Max Pain. No fake order-flow signal. No automatic buy/sell signal.

---

## Live source: FlashAlpha CME NQ futures

The primary provider is now **FlashAlpha**.

FlashAlpha documents CME Nasdaq futures under the symbol:

```text
NQ=F
```

The bot uses two live flow endpoints:

```text
GET /v1/flow/levels/NQ%3DF
GET /v1/flow/gex/NQ%3DF
```

The `levels` endpoint supplies the provider's published live:

- Call Wall
- Put Wall
- Gamma Flip
- underlying NQ futures price

The `gex` endpoint supplies:

- live Net GEX
- positive/negative regime
- per-strike call GEX
- per-strike put GEX

The per-strike profile is used only to attach the **$GEX value** to the published wall and calculate the simple wall-concentration label.

### Important NQ → MNQ point-scale rule

FlashAlpha's CME endpoint is `NQ=F`, not `MNQ=F`.

NQ and MNQ are both quoted in **Nasdaq-100 index points**, so an NQ options-on-futures strike such as `25,250` is plotted at `25,250` on an MNQ chart. There is no NDX cash-index basis conversion for an NQ-native wall.

That means:

```text
FlashAlpha NQ Call Wall = 25,250
MNQ chart level          = 25,250
```

The contracts have different dollar multipliers, but the quoted index-point level is the same scale.

---

## Core logic

### Call Wall

Use FlashAlpha's published `live_call_wall` when available.

If the provider returns the wall as `null`, the engine can fall back to the strike with the largest absolute call-side GEX from the supplied per-strike profile.

Only **one horizontal line** is displayed.

### Put Wall

Use FlashAlpha's published `live_put_wall` when available.

If unresolved, fall back to the strongest put-side GEX strike.

Only **one horizontal line** is displayed.

### Gamma Flip

Use FlashAlpha's `live_gamma_flip`.

It is displayed as **one horizontal line**, not a zone.

### Gamma regime

The bot reads the sign of `live_net_gex`:

```text
Net GEX > 0  => POSITIVE
Net GEX < 0  => NEGATIVE
Net GEX = 0  => NEUTRAL
```

Interpretation:

- positive gamma: hedging tends to be more countercyclical / mean-reverting
- negative gamma: hedging tends to be more procyclical / momentum-amplifying

This is structural context, not a guaranteed trade outcome.

### $GEX unit

FlashAlpha describes GEX as dollar hedge exposure for a **1% move in the underlying**. The dashboard therefore labels values as:

```text
$GEX / 1%
```

### Wall strength

Current fallback strength is a concentration score, not a probability.

For each side:

```text
wall share = abs(wall GEX) / sum(abs(all same-side strike GEX))
```

Default engineering thresholds:

- `EXTREME`: >= 35%
- `STRONG`: >= 22%
- `MODERATE`: >= 12%
- `WEAK`: < 12%

These thresholds are placeholders for historical validation. `EXTREME` does **not** mean an extreme probability of rejection.

---

## Architecture

```text
FlashAlpha NQ=F
      |
      |-- /v1/flow/levels
      |      Call Wall
      |      Put Wall
      |      Gamma Flip
      |
      |-- /v1/flow/gex
             Net GEX
             GEX by strike
             |
             v
        GEX engine
             |
             v
      Minimal dashboard
             |
             v
        /api/levels
             |
             v
   later Tradovate bridge
```

---

## Setup

Requires Node.js 20+.

```bash
cp .env.example .env
npm install
npm test
npm start
```

Open:

```text
http://localhost:3000
```

### Demo mode

```env
GEX_PROVIDER=flashalpha
DEMO_MODE=true
```

### Live FlashAlpha mode

```env
GEX_PROVIDER=flashalpha
DEMO_MODE=false
FLASHALPHA_API_KEY=your_flashalpha_key
FLASHALPHA_SYMBOL=NQ=F
FLASHALPHA_POLARITY=convention
```

Optional:

```env
FLASHALPHA_EXPIRY=
FLASHALPHA_BASE_URL=https://lab.flashalpha.com
FLASHALPHA_GEX_REFRESH_MS=300000
```

`FLASHALPHA_POLARITY=convention` is the default structural view. FlashAlpha also documents a `flow` polarity mode on higher plans; that is a different concept because it signs gamma using inferred session dealer-inventory change. Do not silently swap between these modes during testing.

---

## Request-budget handling

The server defaults to:

```env
POLL_MS=60000
FLASHALPHA_GEX_REFRESH_MS=300000
```

The live wall/flip endpoint is refreshed more often than the full per-strike GEX profile. The wall level is the primary live signal; the per-strike GEX profile is cached because it is only needed for the displayed wall dollar value and concentration score.

If your API plan supports more requests and you only run the bot during a limited trading window, you may reduce these intervals deliberately.

---

## API endpoints exposed by this bot

### `GET /api/state`

Full dashboard state plus freshness and error metadata.

### `GET /api/levels`

Compact output intended for an indicator bridge:

```json
{
  "ok": true,
  "timestamp": "2026-09-08T14:30:00.000Z",
  "regime": "NEGATIVE",
  "netGex": -2850000000,
  "gexUnit": "USD_PER_1PCT_MOVE",
  "mnqPrice": 25142.25,
  "callWall": {
    "strike": 25250,
    "gex": 1780000000,
    "mnqLevel": 25250,
    "strength": "EXTREME",
    "source": "PUBLISHED_WALL"
  },
  "putWall": {
    "strike": 24950,
    "gex": 1460000000,
    "mnqLevel": 24950,
    "strength": "STRONG",
    "source": "PUBLISHED_WALL"
  },
  "gammaFlip": {
    "sourceLevel": 25077,
    "mnqLevel": 25077
  }
}
```

### `GET /health`

Returns HTTP 200 only while a recent snapshot exists.

---

## Replay / historical rule

Replay must never use future information.

At replay time `t`, every wall, flip, Net GEX value, and per-strike value must come from data that was available at or before `t`.

Do not use:

- later-session walls for an earlier replay time
- future OI
- future GEX profile values
- future quotes

FlashAlpha documents historical replay support separately. The provider adapter in this repository is currently the **live** adapter; a dedicated historical/replay adapter should be added before claiming timestamp-accurate Tradovate replay integration.

---

## Deliberately excluded from the visible strategy

- Call/Put Wall zones
- top-three clustering
- 0DTE walls
- IV highs/lows
- Max Pain
- raw OI display
- raw Greeks display
- order-flow claims
- automatic entries/exits

The intended division of labor is:

```text
GEX bot     = structural location + regime + magnitude
Order flow  = separate confirmation tool
Trader      = execution decision
```

---

## Accuracy limitation

The code can reproduce FlashAlpha's published/model-derived levels deterministically from the API response. That does **not** make any public GEX wall a 100% objective law of the market.

The provider itself uses modeling assumptions and effective-OI methodology. Treat the levels as structured, testable market information rather than guaranteed support/resistance.
