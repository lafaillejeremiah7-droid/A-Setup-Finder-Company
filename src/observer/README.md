# MGCV26 Reactive Setup Observer

A small service that watches **Micro Gold (MGCV6, Oct 2026)** on Tradovate,
auto-detects structure top-down, and pushes **A- / A / A+** trend-line setups to
Telegram. **Reactive, never predictive** — it only acts on *completed* 5-minute
bars that break / retest / bounce a higher-timeframe trend line.

## The logic (as implemented)

**Top-down structure (auto-detected, refreshed on HTF bar close):**
- **4H** → AS/AR zones + 4H trend lines
- **1H** → S/R levels + 1H trend lines
- Trend lines are only drawn on 4H/1H (never 5m — 5m line-drawing is noise)

**Reactive trigger (completed 5m bar):**
- break / retest / bounce of a 4H or 1H line
- the broken line = **action**; the opposite line = **safety** (stop goes just
  beyond it, `0.15·ATR`)
- gates: stop distance ≥ `0.50·ATR`, reward:risk ≥ `2.0`

**Adaptive absorption:**
- if price is **inside a zone** (AS/AR/S/R = chop): a **medium/big** confirming
  bubble (`|z| ≥ 2.0`) is **required**, else no alert
- otherwise absorption is **bonus confluence**
- red / lower-wick buying absorption ⇒ bullish; green / upper-wick selling
  absorption ⇒ bearish

**Grade:** `A- = line only`, `A = line + one confluence (S/R or absorption)`,
`A+ = line + both`.

**Limits:** max 2 alerts/day; one alert per setup (deduped by direction + event
+ timeframe).

## Setup

1. Fill in `.env` (copy from `.env.example`). Never commit `.env` — it's gitignored.
   - `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
   - `TRADOVATE_USERNAME`, `TRADOVATE_PASSWORD`, `TRADOVATE_CID`, `TRADOVATE_SEC`
2. Confirm your Tradovate account has a **CME market-data subscription** (needed
   for live quotes; also required for real absorption/order-flow).

## Run

```bash
# 1. Verify Telegram works (sends one message)
python -m src.observer.main --test-telegram

# 2. Offline demo on built-in sample data (no Tradovate needed) — prints alerts
python -m src.observer.main --replay --dry-run

# 3. Offline demo that actually sends to Telegram
python -m src.observer.main --replay

# 4. Live (requires Tradovate creds + entitlement; see note below)
python -m src.observer.main
```

## Live data status

`feed_tradovate.py` handles auth. The **historical `getChart` and websocket
stream methods currently raise `NOT_WIRED`** on purpose: the exact response
shape depends on your entitled account, which can't be seen from the dev
sandbox. To finish live wiring, share one sample `md/getChart` 5m response from
your account (or confirm entitlement) and the parsing is a small, isolated
change in that one file. Everything downstream is done and tested.
