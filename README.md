# MNQ GEX Bot + Dashboard

Minimal live GEX structural map for MNQ. The system intentionally shows **three price lines only**:

1. Call Wall
2. Put Wall
3. Gamma Flip

It also shows the current total gamma regime, modeled $GEX per 1% move, wall concentration strength, source timestamp, and NDX→MNQ basis when applicable.

## Core logic

### Call Wall
The single call-side strike with the largest absolute modeled/published GEX magnitude.

### Put Wall
The single put-side strike with the largest absolute modeled/published GEX magnitude.

### Gamma Flip
The source/model's Net-GEX zero crossing. It is mapped to MNQ like the wall levels.

### Gamma regime
- `POSITIVE` when Net GEX > 0
- `NEGATIVE` when Net GEX < 0
- `NEUTRAL` when Net GEX = 0

Positive/negative gamma is structural context, not an automatic trade signal.

### Strength
`WEAK / MODERATE / STRONG / EXTREME` currently measures how dominant the strongest wall is relative to total absolute same-side GEX in the supplied level set. It is **not** a rejection probability.

Default concentration thresholds:

- EXTREME: strongest wall >= 35% of same-side absolute GEX
- STRONG: >= 22%
- MODERATE: >= 12%
- WEAK: below 12%

These are engineering defaults and should be validated against your historical data before treating them as meaningful trading categories.

## NDX → MNQ mapping

When the GEX source is NDX:

```text
basis = MNQ price - NDX price
MNQ level = NDX GEX level + basis
```

Example:

```text
NDX = 25,000.00
MNQ = 25,042.25
basis = +42.25
NDX Call Wall = 25,200
MNQ Call Wall = 25,242.25
```

NQ and MNQ share the same quoted index level, so an NQ-native source is plotted directly.

QQQ is also supported if the payload includes both QQQ and NDX prices. The engine first normalizes the QQQ strike to an NDX-equivalent level and then applies the MNQ basis.

## Live provider payload

The bot is deliberately provider-agnostic. Point `GEX_SOURCE_URL` at your live endpoint. The endpoint should return JSON like:

```json
{
  "timestamp": "2026-09-07T14:30:00Z",
  "sourceUnderlying": "NDX",
  "sourcePrice": 25000.0,
  "mnqPrice": 25042.25,
  "netGex": -2850000000,
  "gammaFlip": 25035.0,
  "gexUnit": "USD_PER_1PCT_MOVE",
  "callLevels": [
    { "strike": 25200, "gex": -1780000000 },
    { "strike": 25300, "gex": -920000000 }
  ],
  "putLevels": [
    { "strike": 24900, "gex": 1460000000 },
    { "strike": 24800, "gex": 700000000 }
  ]
}
```

For QQQ sources also include:

```json
{
  "sourceUnderlying": "QQQ",
  "sourcePrice": 500.0,
  "ndxPrice": 25000.0,
  "mnqPrice": 25042.25
}
```

If your actual provider uses different field names, edit only `src/providers/genericProvider.js` or add a dedicated provider adapter. Keep the GEX engine independent from provider-specific details.

## Run locally

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

With `DEMO_MODE=true`, the dashboard runs without a real provider so the mapping/UI can be tested immediately.

To use live data:

```env
DEMO_MODE=false
GEX_SOURCE_URL=https://your-provider.example/api/gex
GEX_API_KEY=your-key-if-needed
```

## API endpoints

### `GET /api/state`
Full dashboard state including freshness/error metadata.

### `GET /api/levels`
Compact output intended for a chart/indicator bridge:

```json
{
  "regime": "NEGATIVE",
  "mnqPrice": 25142.25,
  "callWall": {
    "strike": 25200,
    "gex": -1780000000,
    "mnqLevel": 25242.25,
    "strength": "EXTREME"
  },
  "putWall": {
    "strike": 24900,
    "gex": 1460000000,
    "mnqLevel": 24942.25,
    "strength": "STRONG"
  },
  "gammaFlip": {
    "sourceLevel": 25035,
    "mnqLevel": 25077.25
  }
}
```

### `GET /health`
Returns HTTP 200 only when a recent GEX snapshot exists. Stale/no-data state returns 503.

## Replay / historical requirement

For replay, the upstream source must provide the **historical snapshot that was available at the replay timestamp**. Never use later-day GEX, future OI, future IV, or a later NDX→MNQ basis to draw an earlier level.

The engine itself is timestamp-agnostic: if the provider returns timestamp-correct historical data, it will map and display that state without look-ahead.

## Deliberately excluded

The dashboard does not display:

- Call/Put wall zones
- top-three wall clusters
- 0DTE wall lines
- IV highs/lows
- Max Pain
- raw OI
- order-flow claims
- automated buy/sell signals

Those can be added later only if testing proves they add enough value to justify the extra information.

## Important model limitation

The code can produce **deterministic, reproducible output from a chosen GEX source/model**, but no public GEX wall is a 100% objective market truth. Accuracy depends on the provider's universe, dealer-sign assumptions, IV/Greek methodology, OI freshness, and its definition of GEX and Gamma Flip.
