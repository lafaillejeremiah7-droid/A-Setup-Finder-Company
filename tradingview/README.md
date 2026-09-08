# The Gamma Map - TradingView (Pine v6) renderer

`gamma_map_mnq.pine` is the **TradingView** equivalent of the Tradovate renderer
(`tradovate/gamma_map_indicator.js`). It draws EXACTLY three Gamma Map levels as
horizontal lines + labels on an `MNQ1!` chart, plus a one-line regime readout:

```
CALL WALL   30,043   $0.83B / 1%   STRONG    (solid, greenish, above price)
GAMMA FLIP  29,875                            (gray, dotted -- label is price only)
PUT WALL    29,600   $1.12B / 1%   STRONG    (solid, reddish, below price)

TOTAL: NEGATIVE   0DTE: NEGATIVE              (regime readout, bottom right)
```

The label format, separators, sign words, and skip-on-null behavior match
`tradovate/render_spec.json` exactly (the single source of truth shared with the Python
contract test). This is a **thin renderer**: it does not compute GEX and draws nothing but
these three levels + the regime line. The regime readout still shows BOTH the TOTAL and 0DTE
signs (that is a regime summary, independent of which lines are drawn).

## Why you type the numbers in

TradingView Pine **cannot fetch an external file or URL** (a hard platform limit).
So the design is a deliberate, honest manual-input bridge:

1. The Python engine computes the levels for a point in time.
2. You read the three this indicator draws (Call Wall, Gamma Flip, Put Wall) off `scripts/print_levels.py`.
3. You type them into this indicator's **Settings**.
4. Pine draws the lines.

There is no look-ahead and nothing is fabricated: a level you leave at `0` is **skipped**
(no line drawn), mirroring the engine's "null → skip" rule.

## Workflow

### 1. Get the numbers from the engine

Set up the environment (the repo uses pyenv 3.11.15 and `PYTHONPATH=src`) and print a
levels file:

```bash
export PYENV_ROOT="/root/.pyenv"
export PATH="$PYENV_ROOT/shims:$PYENV_ROOT/bin:$PATH"
pyenv global 3.11.15
export PYTHONPATH=src

PYTHONPATH=src python scripts/print_levels.py data/levels/2026-09-06/230612Z_QQQ.json
```

That prints the walls (each with its MNQ price + strength band), the Gamma Flip price,
and the TOTAL / 0DTE regime line. This indicator draws the two total-universe walls (Call
Wall and Put Wall), the Gamma Flip, and the regime readout. For the per-wall
**GEX-per-1%** value (needed for the
`$X.XXB / 1%` part of the label) read the `gex_per_1pct` field from the same levels JSON,
entered in raw dollars (e.g. `830000000` renders as `$0.83B / 1%`).

### 2. Add the indicator to TradingView

1. Open an `MNQ1!` chart on TradingView.
2. Open the **Pine Editor** (bottom panel).
3. Paste the contents of `tradingview/gamma_map_mnq.pine`.
4. Click **Add to chart**.

### 3. Type the numbers into Settings

Open the indicator's **Settings** and fill in, per group:

- **Prices** (Call Wall, Gamma Flip, Put Wall): the MNQ prices from `print_levels.py`.
  Leave any level at `0` to skip it.
- **GEX ($)** for each wall: the raw-dollar `gex_per_1pct` (e.g. `830000000`). The Gamma
  Flip has no GEX/strength; its label is just `GAMMA FLIP   <price>`.
- **Strength** dropdown for each wall (`WEAK` / `MODERATE` / `STRONG` / `EXTREME`). This maps
  to line width: WEAK = 1 … EXTREME = 4.
- **Regime**: the TOTAL and 0DTE sign dropdowns (`POSITIVE` / `NEGATIVE` / `NEUTRAL`) and the
  **Proxy source** checkbox. When TOTAL and 0DTE disagree, the readout appends
  `(DIVERGENCE)`; when the levels came from a volume-proxy source, it appends `(proxy)`.

### 4. Replaying a past date (no look-ahead)

To review a historical day, regenerate the numbers for **that date's** levels file and update
the inputs to match:

```bash
PYTHONPATH=src python scripts/print_levels.py data/levels/kaggle/2021-06-16_qqq.json
```

Then re-enter those numbers in Settings. Because you are entering the levels that were known
as of that date, there is no look-ahead. (Historical Kaggle levels are a volume proxy, so tick
**Proxy source** on so the readout shows `(proxy)`. They are also emitted in native QQQ index
space when no synchronized NQ basis is available, so an MNQ price may be absent; leave that
level at `0` to skip it.)

## Formatting reference (from `render_spec.json`)

| Field       | Rule                                                        | Example            |
| ----------- | ----------------------------------------------------------- | ------------------ |
| price       | thousands separator `,`, 0 decimals                         | `30,043`           |
| gex         | `abs(value) / 1e9`, 2 decimals, `$` prefix, `B / 1%` suffix | `$0.83B / 1%`      |
| strength    | verbatim `WEAK` / `MODERATE` / `STRONG` / `EXTREME`         | `STRONG`           |
| separator   | three spaces                                                | `A   B`            |
| wall label  | `LABEL   price   gex   strength`                            | `CALL WALL   30,043   $0.83B / 1%   STRONG` |
| flip label  | `GAMMA FLIP   price`                                         | `GAMMA FLIP   29,875` |
| regime      | `TOTAL: {total}   0DTE: {zerodte}` (+`(DIVERGENCE)` / +`(proxy)`) | `TOTAL: NEGATIVE   0DTE: NEGATIVE` |

## Scope

This indicator draws ONLY three levels (Call Wall, Gamma Flip, Put Wall) + the regime readout.
It does not draw the 0DTE walls, OI, IV, per-strike greeks, Max Pain, the full chain,
distance-to-level, order flow, or volume profiles. Those are computed upstream (the Python
engine) or added manually by the trader. That scope boundary is a hard rule, not a default.
