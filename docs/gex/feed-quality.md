# Feed Quality Study — CBOE delayed quotes, measured

**Data:** one capture, `data/snapshots/2026-09-06/230612Z`. Market data inside is **Friday
2026-09-04**'s close (Monday 2026-09-07 is Labor Day). Reference closes from independent sources:
Yahoo `NQ=F` daily bars (Thu 29,524.75 / Fri 29,565.25) and CBOE historical dailies (QQQ Thu 717.67).

Prompted by the question "is the feed actually updating, or am I looking at stale numbers?" The answer
turned out to be less important than what the test uncovered on the way.

---

## 1. Real time, established independently

The sandbox clock disagreed with the ambient assumption, so it was checked against two unrelated servers:

| Source | Reports |
|---|---|
| sandbox clock | 2026-09-06 23:11:10Z |
| `cdn.cboe.com` HTTP `Date` | Sun, 06 Sep 2026 23:11:10 GMT |
| `query1.finance.yahoo.com` HTTP `Date` | Sun, 06 Sep 2026 23:11:10 GMT |

**Sandbox clock is correct.** Session timeline: Thu 09-03 and Fri 09-04 traded, Sat/Sun closed,
**Mon 09-07 is Labor Day**, next session **Tue 09-08**.

---

## 2. The feed's own `timestamp` is not a freshness signal ⚠️

| Field | Value | What it actually is |
|---|---|---|
| `timestamp` (top level) | **2026-09-06 13:57:18** — a Sunday | when the *file* was republished |
| `data.last_trade_time` | **2026-09-04T15:59:59** — Friday | when the market last traded |

The file republishes with no market open and no new market data. **Any freshness check keyed on
`timestamp` will report fresh data on a weekend.** Freshness must key on `last_trade_time`.

`capture.py` records both separately, which is why this was visible at all. Per-contract
`last_trade_time` values spread across Friday's session (09:40:03, 11:24:13, 15:29:47, 15:59:58,
16:06:12 — NDX trades to 16:15 ET), confirming the feed does carry intraday granularity.

---

## 3. FINDING: the feed's gamma is quantized into uselessness for NDX 🔴

Every greek is published to **4 decimal places**. Gamma scales as `1/(S·σ·√T)`, so its magnitude is
inversely proportional to the underlying's price level — and a fixed 4-dp field therefore delivers
wildly different precision depending on the instrument.

Measured on `260908` calls within ±2% of the forward:

| | NDX (`NDXP`) | QQQ |
|---|---|---|
| peak gamma | 0.0015 | 0.0637 |
| quantization step | 0.0001 | 0.0001 |
| **step as % of peak gamma** | **6.7%** | **0.2%** |
| strikes in window | 142 | 27 |
| **distinct gamma values reported** | **14** | **27** |
| longest run of strikes sharing one gamma | **18 strikes** | 1 strike |
| **span of indistinguishable gamma** | **90 index points** | 1 point |

Across the *full* `260908` expiry it is worse: **382 NDX strikes report only 16 distinct gamma values**,
with one run of 64 consecutive strikes — **320 index points** — reporting identical gamma.

### Why this matters more than anything else measured so far

The product is a wall *location*. Correction C2 was about a **21-point** basis error and was treated as
serious. Gamma quantization introduces a **90-point** ambiguity near the money, and 320 points in the
wings. **It is 4–15× larger than the error C2 was fixing**, and it lands directly on the quantity the
zone algorithm is supposed to resolve.

It also has a subtle consequence for C1. C1 established that walls come from gamma concentration, not raw
OI. That is correct in theory — but with the free feed's NDX gamma flattened into 14 levels, `γ×OI`
variation across adjacent strikes is driven almost entirely by OI, with gamma contributing mostly
quantization noise. **On raw feed values, C1 is not implementable for NDX.**

### The fix, and why the paper already prescribed it

Precision of the *other* fields, same capture:

| field | quant step as % of median \|value\| |
|---|---|
| gamma | **50.0%** (median over all strikes) |
| delta | 0.02% |
| iv | 0.05% |
| bid / ask | 0.00–0.04% |

**IV and delta are three orders of magnitude better resolved than gamma.** So gamma should never be read
from the feed — it should be *computed* from IV. Recomputing `Γ = φ(d₁)/(F·σ·√T)` from the feed's own IV:

| | feed | recomputed from IV |
|---|---|---|
| distinct gamma values across 142 NDX strikes | 14 | **142** |

Strike-level resolution is fully recovered. Agreement with the feed's coarse values is good where
quantization is least damaging (QQQ ratio 0.989–1.041 across the window), confirming the recomputation is
consistent with the vendor's own model rather than a different one.

This is exactly what paper §1.3 asks for — an IV surface engine that produces greeks, rather than
consuming vendor greeks. **The paper's design is now empirically justified rather than merely good
practice.** One caveat: pointwise recomputation inherits IV's own 4-dp quantization, so gamma is not
perfectly smooth. Gamma must come from a **fitted** surface, which is the §1.3 requirement in full.

---

## 4. Greeks are NOT stale relative to quotes ✅

This was the original cadence question. Method: recover the forward two independent ways for the same
expiry and compare. No assumed interest or dividend rates are needed, which is the point.

1. **From quotes** — put-call parity `C − P = DF·(F − K)`, regressed across near-money strike pairs.
2. **From greeks** — least-squares fit of the forward that reproduces the published delta profile, using
   each contract's own IV.

| root / expiry | pairs | parity R² | `F_quotes` | `F_greeks` | diff |
|---|---:|---:|---:|---:|---:|
| NDXP 260908 | 213 | 0.99979 | 29,511.16 | 29,515.60 | **+4.44** |
| NDXP 260909 | 213 | 0.99973 | 29,514.00 | 29,521.48 | +7.48 |
| NDXP 260910 | 213 | 0.99991 | 29,513.89 | 29,520.06 | +6.16 |
| NDXP 260911 | 71 | 0.99995 | 29,521.20 | 29,528.22 | +7.03 |
| QQQ 260908 | 44 | 0.99993 | 718.41 | 718.48 | **+0.07** |
| QQQ 260909 | 44 | 0.99993 | 718.39 | 718.48 | +0.10 |

The Thursday→Friday move was **+40.50 NQ points**. Greeks and quotes agree to **4–7 points** — an order of
magnitude tighter than the session move. **The greeks track the same market state as the quotes; they are
not lagging a session.** Parity R² ≈ 0.9998 across 213 strike pairs also shows all strikes reflect one
coherent market state, so quotes are not staggered across different times.

Caveat: the delta fit's amplitude parameter sat at its grid boundary (0.9950), so `F_greeks` carries a few
points of systematic bias. It does not affect the conclusion at this effect size.

---

## 5. FINDING: `current_price` disagrees with the forward implied by the options 🔴

Same capture, deriving implied spot by removing carry from the parity forward (net carry read off the
forward term structure: NDX forwards rise ~3.3 pts/day across 260908→260911, ≈ 4.1%/yr, consistent with
r ≈ 4.7% / q ≈ 0.6%):

| | `current_price` | spot implied by option quotes | disagreement |
|---|---:|---:|---:|
| NDX | 29,544.15 | ≈ 29,498 | **−46 pts (−0.156%)** |
| QQQ | 717.50 | ≈ 718.09 | **+0.59 (+0.082%)** |

The disagreement runs in **opposite directions**, so this is not a simple one-session lag. Combined,
the two chains disagree about the underlying by **≈0.24%, about 70 NDX points.**

Consequences:

1. **Do not use `current_price` as the spot for greek computation.** Derive the forward per expiry from
   put-call parity, which is internally consistent with the quotes (R² ≈ 0.9998) and needs no rate
   assumptions. `current_price` is also what `capture.py` currently uses for basis — so the basis is
   contaminated by this too, and must be revisited.

2. **This partly explains finding #8.** The NDX↔QQQ wall disagreement (Call 58.9 / Put 235.3 NQ pts) was
   attributed to genuinely different positioning. But the two chains disagree about the *underlying* by
   ~70 NDX points before any positioning is considered. **A material share of finding #8 may be a forward
   inconsistency, not a positioning signal.** It must be re-measured on parity-implied forwards before any
   conclusion about positioning is drawn.

---

## 6. FINDING: open interest is dominated by the AM-settled monthly

NDX open interest by root/expiry, same capture:

| root | expiry | contracts | OI | sum \|gamma\| |
|---|---|---:|---:|---:|
| **NDX** | **260918** | 628 | **48,679** | 0.0826 |
| NDXP | 260908 (nearest) | 764 | 3,066 | 0.2724 |
| NDXP | 260909 | 744 | 2,104 | 0.2560 |
| NDXP | 260911 | 400 | 3,151 | 0.0826 |
| NDXP | 260914 | 358 | 3,635 | 0.0808 |

The AM-settled monthly holds **~16× the open interest of any daily expiry**. Since GEX ∝ Γ×OI, total NDX
GEX is dominated by the monthly, while the dailies carry far higher gamma per contract.

This makes the C7 AM/PM distinction operationally central rather than a technicality: the single largest
OI concentration in the chain is an **AM-settled** root, which on its expiry date has already stopped
trading. Total-GEX walls and 0DTE walls will therefore sit in genuinely different places, and the
`NDX 260918` block must not leak into a 0DTE view.

---

## 7. Instrument choice: reopened

The earlier decision was **NDX primary, QQQ cross-check** — on grounds of confirmed dailies, a finer
strike grid in index terms, European cash settlement, and no ETF ratio step. Those still hold. But on the
free feed's *raw* values, NDX gamma resolution is ~34× worse than QQQ's (6.7% vs 0.2% of peak gamma), and
90 points of the very quantity being solved for is indistinguishable.

This does **not** flip the decision, because recomputing gamma from IV restores NDX to full strike-level
resolution (14 → 142 distinct values). It does change a prerequisite:

> **NDX is only viable once greeks are computed from a fitted IV surface. Until then, QQQ is the only
> instrument whose feed values can locate a wall.**

QQQ's role is upgraded from "cross-check" to "the precision reference the NDX pipeline is validated
against."

---

## 8. Still unmeasured

**True intraday update cadence.** The feed holds only its latest state and no free historical chain
exists, so a single capture cannot show how values evolve within a session. Section 4 shows greeks are
synchronized with quotes, and §2 shows per-contract trade times spanning the session — together these
strongly imply intraday movement, but that is inference, not measurement.

Direct test requires consecutive captures during a live session: **Tuesday 2026-09-08** (Monday is Labor
Day). Until then, the intraday migration of walls is assumed, not demonstrated.
