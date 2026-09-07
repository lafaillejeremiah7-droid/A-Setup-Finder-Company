# The Gamma Map engine (`src/gammamap`)

The engine turns option chains into the five MNQ price levels the Tradovate indicator
draws (Call Wall, Put Wall, 0DTE Call Wall, 0DTE Put Wall, Gamma Flip) plus the TOTAL /
0DTE gamma regime readout. It has two ingest paths that meet at one internal schema:

- **CBOE live** (the v1 production path): carries open interest, so it computes classic
  OI-weighted GEX ("true GEX").
- **Kaggle historical QQQ** (the free replay path): has **no** open-interest column, so
  GEX is a **volume proxy** and is flagged as such on every output. It is what lets you
  click a replay date and still see GEX levels.

Everything downstream of ingest is identical between the two paths; only the weight
(OI vs volume) and the MNQ mapping (available live, unavailable historically) differ.

## Pipeline

```
                 scripts/capture_snapshot.py            scripts/backfill_kaggle.py
                          |                                      |
                    (CBOE live)                          (Kaggle CC0 CSV)
                          v                                      v
  capture.py  ->  distilled snapshot            adapters.from_kaggle_csv
  (data/snapshots/<date>/<time>/)                        |
                          |                              |
        adapters.from_distilled_snapshot                 |
                          |                              |
                          v                              v
                 chain.py  --  ONE NormalizedChain schema (root,expiry,type,strike = C7)
                          |
                          v
   surface.py   parity forward per expiry (C9); gamma-from-IV for NDX (C8)
                          |
                          v
   gex.py       per-strike $GEX/1% = s*Gamma*W*M*S^2*0.01
                walls (max-|GEX| per side), gamma flip (root of NetGEX(S)), regime
                W = OI (true) or VOLUME (proxy, Kaggle)
                          |
                          v
   mapping.py   NDX strike -(+basis)-> NQ -(1:1)-> MNQ   (C2)
                QQQ strike -(/ratio)-> NDX-equiv -(+basis)-> NQ -> MNQ
                basis = NQ - parity forward (NOT current_price, C9)
                          |
                          v
   levels.py    build_levels(...)  ->  small versioned JSON (only the 5 lines + regime
                + provenance/flags; OI/IV/greeks/MaxPain are ingredients, NOT written)
                          |
                          v
   tradovate/gamma_map_indicator.js   thin renderer of the levels file
```

Module map:

| Module | Role |
| --- | --- |
| `chain.py` | The internal `NormalizedChain` / `NormalizedContract` schema. Contract identity is the full `(root, expiry, type, strike)` tuple (C7). `oi_available` is the single true-GEX-vs-proxy switch. |
| `adapters.py` | `from_distilled_snapshot` (CBOE, OI present) and `from_kaggle_csv` (Kaggle, no OI -> `oi_absent_volume_proxy`). `is_true_gex()` is the gate. |
| `surface.py` | `forward_from_parity` (C9) and `IVSurface.surface_gamma` (gamma-from-IV, the C8 fix for NDX). QQQ v1 uses the clean feed gamma directly. |
| `gex.py` | `compute_strike_gex`, `select_wall`, `assess_strength` (within-snapshot GlobalShare, not probability), `solve_gamma_flip`, `assess_regime`. |
| `mapping.py` | The two mapping legs (C2) and the C9-corrected basis. |
| `levels.py` | `build_levels` (live) and `build_kaggle_levels` (historical). Deterministic JSON, strict no-look-ahead. |

## Setup (every shell)

The sandbox does not persist environment exports between shells, so re-export pyenv and
set `PYTHONPATH=src` in the **same** command each time. There is no packaging; `src/` on
`PYTHONPATH` is how `import gammamap...` resolves.

```bash
export PYENV_ROOT="/root/.pyenv"
export PATH="$PYENV_ROOT/shims:$PYENV_ROOT/bin:$PATH"
pyenv global 3.11.15
pip install numpy scipy pytest      # once per fresh environment
export PYTHONPATH=src
```

Run the tests (the build gate):

```bash
PYTHONPATH=src python -m pytest tests/ -q
```

## (a) Produce a levels file for TODAY (live CBOE, true OI-GEX)

1. Capture a fresh snapshot (needs market hours; writes an immutable distilled snapshot):

   ```bash
   export PYENV_ROOT="/root/.pyenv" && export PATH="$PYENV_ROOT/shims:$PYENV_ROOT/bin:$PATH" && pyenv global 3.11.15
   PYTHONPATH=src python scripts/capture_snapshot.py --root data/snapshots
   # prints e.g.  snapshot: data/snapshots/2026-09-06/230612Z
   ```

2. Build the levels file from that snapshot directory (QQQ is the v1 instrument):

   ```bash
   PYTHONPATH=src python -c "
   from gammamap.levels import build_levels, write_levels
   levels = build_levels('data/snapshots/2026-09-06/230612Z', 'QQQ')
   print(write_levels(levels))            # -> data/levels/<date>/<time>_QQQ.json
   "
   ```

   The live path maps each wall/flip to an **MNQ price** using the C9-corrected parity
   basis, and emits `proxy: false` (true OI-GEX). Strict no-look-ahead is enforced: a
   source timestamp later than `asof` raises `LookAheadError`.

## (b) Produce a levels file for a HISTORICAL Kaggle date (volume proxy)

`scripts/backfill_kaggle.py` downloads the free CC0 dataset
(`kylegraupe/qqq-daily-option-chains-q1-2020-to-q4-2022`, no auth) into the gitignored
`data/kaggle_raw/`, streams the 628MB CSV grouped by `QUOTE_DATE`, and writes one small
levels file per date to `data/levels/kaggle/<date>_qqq.json`. Download + parse happen in
one process run (the sandbox wipes `/tmp` and env between calls).

```bash
export PYENV_ROOT="/root/.pyenv" && export PATH="$PYENV_ROOT/shims:$PYENV_ROOT/bin:$PATH" && pyenv global 3.11.15

# Full run: download (if absent) + build the documented monthly sample across the
# dataset's coverage (see the data-coverage note below).
PYTHONPATH=src python scripts/backfill_kaggle.py

# Bounded run (no full 628MB read): stream only the first N distinct dates.
PYTHONPATH=src python scripts/backfill_kaggle.py --max-dates 6

# Specific dates:
PYTHONPATH=src python scripts/backfill_kaggle.py --dates 2021-03-01 2022-01-03
```

Only the small per-date JSON files under `data/levels/kaggle/` are committed; the raw zip
and CSV under `data/kaggle_raw/` are gitignored.

### PROXY caveat for historical dates (read this)

Historical Kaggle levels are **not** classic OI-GEX and are **not** on the MNQ price axis:

- **Volume proxy, always.** The Kaggle CSV has no open-interest column. GEX is computed
  from **volume** as a proxy weight. Every historical file carries `proxy: true` and the
  `oi_absent_volume_proxy` flag. A volume-proxy GEX is a *different quantity* from
  OI-GEX; never read it as classic OI-GEX.
- **No MNQ mapping (no fabricated basis).** There is no synchronized NQ futures quote for
  a 2021-2022 date, so the NDX->NQ futures basis (C2) cannot be measured. Rather than
  invent one, historical levels are emitted in the options' **native QQQ index space**
  (`native_level`), with `mnq_price: null`, `render_target: "QQQ_native"`, and the flag
  `basis_unavailable_no_synchronized_nq_quote`. The honest parity-forward index anchor is
  still recorded in provenance. Supplying a dated basis to map these onto MNQ is a v2
  item. This is a documented v1 limitation.

### Data-coverage note

Despite the dataset title ("Q1 2020 to Q4 2022"), the shipped `qqq_2020_2022.csv` actually
contains only **2021-01-04 .. 2022-12-30** (508 trading days; no 2020 rows). The default
sample therefore spans 2021-2022. A requested date that is a holiday/weekend (no chain) is
skipped, never fabricated.
