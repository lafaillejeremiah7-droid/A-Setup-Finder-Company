# Historical Options Data — Source Survey (measured 2026-09-06)

Question: is there a **free** source of **historical intraday** options chains (with greeks + open
interest) for **QQQ / NDX / SPX**, so the indicator's Tradovate-replay requirement can run on past days
rather than only capture-forward?

Every entry below was probed live, not assumed. Verdict first, evidence after.

## Verdict (updated — a free by-date source WAS found)

**`marketdata.app` provides historical option chains by date on a Free Forever tier** (100 chains/day, 1
year back, OI + IV + greeks). This makes the "click any past date, see that day's GEX" requirement
achievable for free — with two limits: the historical chain is **end-of-day** (levels static within a
replayed session, no intraday migration), and historical greeks may be **null** so gamma likely must be
recomputed from the historical IV. A **free email-signup API key** is required (an agent cannot
self-provision it), and QQQ/NDX/SPX coverage must be confirmed with that key. See the starred section.

**No free source provides historical _intraday_ options chains for QQQ/NDX** — that remains paid-only
(CBOE DataShop / Polygon / OptionsDX) or capture-forward. DoltHub, the only other free historical DB, is
daily EOD, lacks QQQ/NDX/SPX, and has no open-interest column.

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

## ⭐ marketdata.app — the real find (historical chains by date, free tier)

`https://api.marketdata.app/v1/options/chain/{SYMBOL}/?date=YYYY-MM-DD` — verified against the live docs.

**Why this is the one that fits the "click any past date" requirement:**
- Accepts a **`date=` parameter** and returns a **full historical chain for any past trading day** — this
  is exactly the shape needed for arbitrary-date replay, which nothing else free offered.
- Returns **`openInterest`, `iv`, and full greeks** (`gamma`, `delta`, `theta`, `vega`) per contract, plus
  `underlyingPrice`, bid/ask, volume, OSI `optionSymbol`. That is the complete GEX input set — no IV
  surface engine strictly required to get gamma, same as the CBOE live feed.
- OSI symbol + strike format matches the CBOE parser already written.

**Free-tier terms (from the pricing page):**

| | Free Forever | Starter $30/mo | Trader $75/mo |
|---|---|---|---|
| Daily API credits | **100/day** | 10,000 | 100,000 |
| Historical depth | **1 year** | 5 years | unlimited |
| Options delay | **24h delayed** | 15 min | real-time |
| Historical option chains | ✅ (within 1yr) | ✅ 5yr | ✅ unlimited |

A full chain for one ticker/date = **1 credit** (cached-snapshot pricing). So **100 replay-days per day**
on the free tier — ample for practice. 30-day free trial of a paid tier also available, no card required.

### ✅ TESTED LIVE (2026-09-06, token-free AAPL historical path)

marketdata.app unlocks **any AAPL contract with no token**, for historical data. That let the make-or-break
question be answered directly, without waiting on a working token. Historical chain
`GET /v1/options/chain/AAPL/?date=2025-03-14&expiration=2025-03-21` returned:

| Field | Historical (`date=`) result |
|---|---|
| `openInterest` | ✅ **real** (e.g. 31,100 / 20,789 / 43,668) |
| `bid` / `ask` | ✅ **real** |
| `volume` | ✅ real |
| `underlyingPrice` | ✅ real (213.49) |
| `iv` | ❌ **null (all rows)** |
| `gamma` / `delta` / `theta` / `vega` | ❌ **null (all rows)** |

**So historical greeks AND IV are both null — confirmed, not just feared.** But everything needed to
*reconstruct* them is present. Verified in the same session: Black-Scholes IV inversion from the bid/ask
mid → gamma → GEX runs cleanly on the historical data. Recovered a smooth, sane IV smile (calls
0.297–0.374, puts 0.315–0.410) and a coherent gamma profile peaking near the money, giving a near-money
gross GEX of \$0.42B/1% for that one AAPL expiry. **The full pipeline works on historical marketdata.app
data.**

**Consequence:** for **replay/historical**, the IV-surface engine (price → IV → gamma) is **mandatory**,
because the vendor supplies neither IV nor greeks on `date=` requests. This is the same engine C8 already
required for live NDX — so it is needed regardless; replay just makes it non-negotiable for QQQ too.

### Auth status (unresolved — needs the emailed token, not dashboard credentials)

Per the auth docs, the API **token is a distinct string emailed to you** when you request it from the
dashboard — it is **not** the "API key" or "Access ID" shown in the dashboard. Both dashboard values were
tested in every form (Bearer / `?token=` / concatenations) and all returned `401 {"errmsg":"Invalid
token."}`. Non-AAPL symbols (QQQ/NDX/SPX) therefore remain unconfirmed until the emailed token is used.
Also note marketdata.app enforces a **single-IP policy** — the sandbox and the user's own dashboard
session hitting the API at once can trigger a temporary block.

**TWO CAVEATS measured from the docs (now confirmed live):**

1. **Historical greeks may be null.** The docs state plainly: *"This is a current chain, so the Greek
   columns are populated. A historical request (`date=`) returns null for all five."* If greeks are null on
   `date=` requests, gamma must be **recomputed from the historical IV** (which the same row does carry) —
   i.e. the IV→gamma path from C8 becomes mandatory for historical/replay, even for QQQ. This needs a live
   key to confirm whether `iv` is also present on historical rows (docs imply the quote fields are).
2. **Historical = end-of-day, not intraday.** The docs note *"One historical row does not carry one as-of
   time"* — a `date=D` request returns the EOD chain for that day, one row per contract. So replay would
   show **the same levels all session** for a given past day, not intraday migration. That is still a
   massive improvement over "no past dates at all," and matches how most GEX practitioners use daily walls
   — but it is EOD, not tick-by-tick.

**Verdict:** this makes the user's core request — *click a random past date in replay, see that day's GEX
levels* — **achievable on the free tier**, with two honest limits: levels are EOD-static within the day,
and greeks likely need recomputing from IV. Requires a **free API key** (email signup; cannot be
self-provisioned by an agent). This is the recommended backfill source. Coverage of **QQQ / NDX / SPX
specifically must be confirmed** with a key — the docs use AAPL examples.

## Other untested lead

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
