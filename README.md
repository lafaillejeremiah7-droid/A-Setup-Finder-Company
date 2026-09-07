# The Gamma Map

A GEX (gamma exposure) level engine plus a thin Tradovate renderer. The engine turns
option chains (CBOE live, Kaggle historical) into MNQ price levels; the renderer draws
exactly five lines (Call Wall, Put Wall, 0DTE Call Wall, 0DTE Put Wall, Gamma Flip) plus
a TOTAL/0DTE gamma-regime readout. See `docs/gex/build-spec.md` for the authoritative
spec and `docs/gex/findings-so-far.md` for the verified corrections (C1-C9).

## Layout

- `src/gammamap/` — engine package (capture layer is done: `sources`, `distill`, `capture`).
- `scripts/` — CLI entry points (`capture_snapshot.py`).
- `data/snapshots/` — committed distilled captures + content-addressed OI + `index.jsonl`.
- `tests/` — pytest suite (the build gate; there is no packaging step).
- `docs/gex/` — authoritative spec and findings.

## Development setup and running tests

There is no packaging step, so the test suite is the build gate. Python is pyenv-managed
(pinned to 3.11.15). Because the package is imported directly from `src/`, run the suite
with `PYTHONPATH=src`.

```sh
# 1) Pin the interpreter and put pyenv on PATH.
export PYENV_ROOT="/root/.pyenv"
export PATH="$PYENV_ROOT/shims:$PYENV_ROOT/bin:$PATH"
pyenv global 3.11.15

# 2) Install dev/test dependencies.
pip install -r requirements-dev.txt

# 3) Run the tests (src/ on the path so `import gammamap` resolves).
PYTHONPATH=src python -m pytest tests/ -q
```

In this sandbox each shell is a fresh container, so the pyenv exports do not persist
between invocations — chain the export, install, and test into a single command when
running non-interactively.
