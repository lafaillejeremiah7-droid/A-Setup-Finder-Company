# Historical Options Data — Source Survey (measured 2026-09-06)

Question: is there a **free** source of **historical intraday** options chains (with greeks + open
interest) for **QQQ / NDX / SPX**, so the indicator's Tradovate-replay requirement can run on past days
rather than only capture-forward?

Every entry below was probed live, not assumed. Verdict first, evidence after.

## Verdict

**No free source provides historical *intraday* options chains for QQQ/NDX.** One free source
(DoltHub) provides historical *daily EOD* chains with greeks, but for SPY and single names — not the
index/ETF being traded, and not intraday. **Capture-forward remains the only path to the intraday,
QQQ/NDX, replay-correct history the indicator needs.**

## What each source actually returned

| Source | Reachable | Historical? | Intraday? | Greeks/OI? | QQQ/NDX? | Verdict |
|---|---|---|---|---|---|---|
| CBOE `delayed_quotes` (dated / summaries) | 403 | — | — | — | — | blocked, re-confirmed |
| Nasdaq.com `api/quote/.../option-chain` | 200 | **no** — ignores `date`/`forDate` params | live only | OI/bid/ask, **no greeks** | QQQ ✓ | live snapshot only |
| Alpha Vantage `HISTORICAL_OPTIONS` | 200 | yes (advertised) | **unknown — needs free key** | yes (per docs) | likely | **worth a key test** |
| DoltHub `post-no-preference/options` | 200 | **yes** | **no — daily EOD** | **gamma,delta,theta,vega,rho,vol(IV),bid,ask** | **no QQQ/NDX/SPX; SPY + single names** | wrong granularity + wrong symbols |
| ORATS `datav2/hist` | 403 | (paid) | — | — | — | paid |
| Tradier sandbox | 401 | (token) | — | — | — | needs account; live-focused |
| Yahoo `v7/finance/options` | 429 | no (crumb-gated, live) | — | — | — | rate-limited, live only |

## DoltHub detail (the one real free historical DB)

Public SQL-queryable options database. `option_chain` schema is a near-perfect match for what the
indicator needs:

```
date, act_symbol, expiration, strike, call_put, bid, ask,
vol (=IV), delta, gamma, theta, vega, rho
```

But three measured facts rule it out for this project:

1. **Daily EOD, not intraday.** `GROUP BY expiration,strike,call_put HAVING COUNT(*)>1` on a single
   `(date, act_symbol)` returns **zero** duplicate keys → exactly one row per contract per day. Replay
   needs within-session snapshots; this has none.
2. **No index/ETF underlying.** `act_symbol='QQQ'`, `'NDX'`, `'SPX'` all return 0 rows on a populated
   day. `'SPY'` and single-stock names (A, AAL, ...) are present. The instrument being traded is absent.
3. **No open interest column.** GEX = Γ × **OI**; the table has greeks but not OI, so GEX cannot be
   computed from it even for the symbols it does carry.
4. Coverage is sparse and dated: `2020-06-15` and `2021-06-15`+ populated; `2024-01-16` empty. Not current.

Also queryable (not helpful here): `volatility_history` (HV/IV summary stats per symbol/day).

Query note for any future use: the table is PK-ordered on `date` first, so `WHERE date='...'`
(optionally + `act_symbol`) is fast; a bare `WHERE act_symbol='QQQ'` scans the whole table and times
out against the API's ~30s limit.

## The one remaining untested lead

**Alpha Vantage `HISTORICAL_OPTIONS`** explicitly advertises historical options with greeks and rejects
only the `demo` key. A real free key (25 req/day limit) would settle whether it (a) covers QQQ, (b) is
intraday or EOD, and (c) includes OI. **This is the single lead worth pursuing if historical replay must
work on pre-capture dates.** Free tier's 25 calls/day makes it viable for spot-checking specific replay
days, not for bulk history.

## Consequence for the build

- **Replay works forward from first capture.** Every snapshot recorded is a replayable day. No
  look-ahead risk because each snapshot is genuinely point-in-time.
- **Pre-capture replay is not free.** Options: (a) accept forward-only replay; (b) test Alpha Vantage as
  a per-day backfill; (c) a paid vendor (CBOE DataShop / Polygon / OptionsDX) for true historical
  intraday NDX/QQQ.
- This does not block v1: the QQQ indicator can be built and run live/forward now. Historical backfill
  is an independent question that does not gate the pipeline.
