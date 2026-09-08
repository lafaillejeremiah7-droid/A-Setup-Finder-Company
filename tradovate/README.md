# The Gamma Map — Tradovate custom indicator

A **thin renderer** for the Gamma Map engine. It reads a precomputed, point-in-time levels
file and draws **exactly five horizontal lines plus one regime readout on the MNQ chart —
and nothing else.**

## What it draws (and what it deliberately does not)

It draws only:

| Line | Source | Style |
| --- | --- | --- |
| **CALL WALL** | strongest (max-\|GEX\|) call strike, total universe | solid, above price |
| **0DTE CALL WALL** | strongest call strike expiring today | solid, above price |
| **GAMMA FLIP** | `NetGEX(S)=0` solve | neutral gray, dotted |
| **0DTE PUT WALL** | strongest put strike expiring today | solid, below price |
| **PUT WALL** | strongest put strike, total universe | solid, below price |

Each **wall** label is formatted per build-spec §9a:

```
CALL WALL   30,043   $0.83B / 1%   WEAK
```

that is `LABEL   <strike>   $<gex>B / 1%   <STRENGTH>`, where strength is one of
`WEAK / MODERATE / STRONG / EXTREME` (structural dominance, **never** a probability of
reversal). The gamma flip line shows only its label and strike.

Below the walls it shows one small regime readout:

```
TOTAL: NEGATIVE   0DTE: NEGATIVE
```

(`+` → `POSITIVE`, `-` → `NEGATIVE`, `0` → `NEUTRAL`; `(DIVERGENCE)` is appended when the
total and 0DTE regimes disagree.)

**It does NOT** compute GEX and does **not** draw OI, IV, per-strike greeks, Max Pain, the
full option chain, distance-to-level, dashboards, **order flow, or volume profiles**. Those
last two (order flow + Fixed/Session Volume Profile) are added **manually** by the trader;
this indicator never draws them. This scope boundary is a hard rule — do not add plots.

## How the levels file is produced and fed

- **Produced by the engine.** The Python pipeline (`src/gammamap/levels.py`) computes the
  walls, flip, and regime, maps every strike to an MNQ price, and writes a versioned JSON
  file (schema `levels-1.0.0`) to `data/levels/<date>/<time>Z_<instrument>.json`. The
  indicator never recomputes any of these numbers — it only parses and draws them.
- **Fed to the indicator.** A Tradovate custom indicator can read an external data source
  (this is why Tradovate was chosen over TradingView — Pine cannot read an external file).
  Host the levels files (and a small manifest listing each file's `asof` and URL) where the
  indicator can fetch them, then wire the two seams in the adapter section of
  `gamma_map_indicator.js`:
  - `io.getManifest()` → returns `[{ asof, url }, ...]`
  - `io.getLevels(url)` → returns the parsed levels JSON
  - `io.getSurface(ctx)` → returns the platform object that draws horizontal lines + text

The render/formatting rules are declared once in `render_spec.json` and mirrored by the JS
`RENDER_SPEC`; the Python contract test asserts the two stay in sync.

## Replay behavior (strict no-look-ahead)

When you click a **replay date/time**, the indicator selects the levels file whose `asof`
is the **latest that is at or before the chart's current timestamp — never a future one**
(`selectLevelsForReplay`). So a replay shows only the GEX levels that were actually known at
that instant. If no file exists yet for the replayed time, it draws nothing.

## Missing / proxy levels

- A **missing** wall or a **neutral/absent** gamma flip (null price in the file) is
  **skipped** — the indicator never draws a line at a guessed level. In the committed sample
  file the 0DTE walls and the flip are null, so only the two total walls render.
- If the levels file was built from a **volume proxy** chain (no true open interest), the
  regime readout is suffixed with `(proxy)` so it is never mistaken for a classic OI-GEX
  read. Proxy walls are never presented as authoritative OI-GEX.

## Install on the MNQ chart

1. Open an **MNQ** chart in Tradovate.
2. Open the custom-indicator editor and create a new indicator; paste the contents of
   `gamma_map_indicator.js`.
3. Wire the three `io` seams (manifest / levels / drawing surface) to your hosted levels
   files and the platform's drawing primitives (see the **TRADOVATE ADAPTER** section in the
   JS file — it is the only place that touches the Tradovate API).
4. Add the indicator to the MNQ chart. As you scrub the replay timeline, the five lines and
   the regime readout update to the point-in-time levels known at each timestamp.

## Files

| File | Purpose |
| --- | --- |
| `gamma_map_indicator.js` | the indicator: pure render model + replay shim + thin Tradovate adapter |
| `render_spec.json` | machine-checkable formatting contract shared with the test |
| `README.md` | this document |

## Tests

The pure render model is verified by `tests/test_indicator_contract.py` (run with the
engine suite: `PYTHONPATH=src python -m pytest tests/ -q`). It loads the same
`render_spec.json` and the real committed levels file and asserts the render model the JS
builds: line count (≤ 5, nulls skipped), SS9a label formatting, and the regime string. A
`node --test` unit test was the first choice, but Node is not available in this environment,
so the JSON-contract test is the documented fallback.
