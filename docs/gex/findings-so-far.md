# GEX Research — Verified Findings & Corrections

Working record reconciled against **The Gamma Map** (Jay Den, Sept 2026) — the full research paper.
Companion to `docs/foundations/logic-reasoning-math.md` and `docs/gex/build-spec.md`.

Epistemic tiers: **PROVEN** (symbolic) · **MEASURED** (live data) · **PAPER** (from the source document)
· **ASSUMPTION** (unverifiable on available data) · **OPEN** (needs data or testing).

> **Status:** the paper supersedes my earlier working assumptions in several places. Corrections are
> logged explicitly in §1 rather than silently edited, per the epistemic-ledger rule.

---

## 1. Corrections — where my earlier conclusions were WRONG

### C1. Wall location: I said "use OI, not γ×OI." The paper says gamma concentration. ❌→✅

My earlier fix was **wrong**. I measured that ranking by `γ(spot)×OI` returned the *same strikes as raw
gamma alone* (OI contributed nothing) and concluded walls should be located by **OI concentration**.

The paper (§4.1, §4.3.4, Appendix E) explicitly rejects that: a wall is a **gamma concentration**, and
its worked example shows the **highest-OI strike (25,300) is NOT the wall** because its far-OTM/high-IV
combination gives it low gamma per contract. "Call Wall = highest bullish call OI" is listed as a
misconception (§12.1).

**The paper's actual resolution of my degeneracy problem** (§2.2.5) — do not choose one input; separate them:

```
GammaPerOpenContract(Z) = Σ|GEXᵢ| / max(Σ OIᵢ, 1)      # sensitivity per surviving contract
ZoneOverlap = len(Z_OI ∩ Z_GEX) / len(Z_OI ∪ Z_GEX)     # structural agreement, diagnostic only
```

And the binding rule: **OI must never be added to a strength score that already contains GEX** — that
double-counts position size, since `GEX ∝ s·Γ·OI·M`. OI earns its place only through *incremental*
information: persistence, ΔOI, cross-expiry breadth, cluster shape, freshness (hypotheses H10–H12).

**My empirical result still stands and is useful** — it is direct evidence for why the paper's
`GammaPerOpenContract` diagnostic and the anti-double-counting rule are necessary. It just was not
grounds for switching to OI-only wall location.

### C2. I omitted futures basis entirely. ❌→✅

I mapped QQQ→NDX with a ratio (41.1765) and then treated NDX levels as MNQ levels. **The paper §9
forbids this:** "An NDX strike cannot be blindly drawn on NQ because the futures trade at a basis to
the cash index."

```
Basisₜ = NQₜ − NDXₜ
Mapped NQ Levelₜ = NDX-derived Levelₜ + Basisₜ

QQQ path:  rₜ = QQQₜ/NDXₜ ;  K_NDXeq = K_QQQ / rₜ ;  K_NQeq = K_NDXeq + Basisₜ
```

**PROVEN** against the paper's Appendix A: NDX 24,200 / NQ 24,242 → basis **+42**; zone 24,250–24,350
NDX → **24,292–24,392 NQ**, core 24,342. My arithmetic reproduces it exactly.

Also **§9.4:** never map IV by point addition. Compute Greeks in the **native** option market, aggregate
there, *then* map the resulting strike/zone into NQ coordinates.

**NQ and MNQ share the same price level** — only the multiplier differs ($20 vs $2 per index point),
so one mapped level serves both.

### C3. "NDX has no daily expiries" — **WRONG. Now RESOLVED.** ❌→✅ MEASURED

I concluded from one snapshot that NDX cannot support 0DTE. **The paper (§IX) was right and I was
wrong.** NDX expiry list from the live feed:

```
260908 Tue · 260909 Wed · 260910 Thu · 260911 Fri · 260914 Mon
260915 Tue · 260916 Wed · 260917 Thu · 260918 Fri · 260921 Mon
```

**Consecutive weekdays = genuine daily expirations.** 45 distinct expiries on NDX vs 31 on QQQ, and the
nearest expiry carries **764 contracts on NDX vs 394 on QQQ** — NDX is the *richer* chain, not the poorer one.

**Calendar sanity check that confirms the read:** the "missing" Monday **260907 is Labor Day 2026**
(first Monday of September). That is exactly why the Friday 260904 capture jumped to Tuesday 260908 —
Friday's own 0DTE had expired and Monday was a holiday. My original snapshot was not evidence about
NDX listing behaviour at all.

**Decision: use both, NDX primary.** NDX has daily expiries, finer strike resolution in index terms
(25–50 pt grid vs QQQ's \$1 ≈ 41 NDX pts), more contracts, is European/cash-settled (cleaner greeks, no
early-exercise modelling), and needs no ETF ratio step. QQQ becomes the secondary cross-check.

### C4. Max Pain ≈ Gamma Flip — my analytic reasoning was too glib. ❌→✅

I claimed both are "balance points of the same OI vector," so structurally correlated. **Not accurate.**
Per §5.3, Max Pain uses **only** strike, OI, intrinsic payout and multiplier — **no gamma, no IV, no
delta, no sign model**. The flip uses all of those. They are different objects computed from different
inputs; the paper lists "Max Pain is the Gamma Flip" as false (§12.1).

My measurement (111 NDX pts apart, inside the resolution band on one snapshot) is a **coincidence
observation**, not an identity. It maps to the paper's **H8**: does Max Pain add incremental intraday
information once gamma concentration, distance and volatility are controlled? Also note: if IV changes
and OI does not, **Max Pain does not move but GEX does** (Appendix B) — a clean discriminator.

The **conditional-independence concern remains valid** and is now testable rather than assumed.

### C5. My zone algorithm was close but under-specified. ❌→✅

I used FWHM (λ = 0.5). The paper §4.2 specifies a fuller algorithm; Appendix A uses **λ = 0.40**, plus a
**volatility-normalized gap limit**, a **percentile/share peak threshold tuned in training data only**,
cluster merging, minimum-notional rejection, an exposure-weighted **centroid** alongside the core
strike, and **uncertainty bands** from IV error / sign alternatives / bootstrap.

My FWHM collapse-to-one-strike result is explained: a fixed half-max with no gap rule and no
volatility normalization is too brittle for round-number OI spikes.

### C6. Gamma-sign convention — the paper adds a correction I had not stated.

**PROVEN symbolically:** `Γ_call − Γ_put = 0` for the same K, T (verified with full carry terms r, q).

Therefore a dashboard colouring calls **+** and puts **−** is **not** reporting option gamma sign —
option gamma is positive for a long vanilla call *and* a long vanilla put. It is applying a **dealer-
position convention** (§1.0). This sharpens my §3.4 finding: the sign is doubly non-physical.

---

## 2. Confirmed — my findings the paper independently corroborates

| My finding | Paper location | Status |
|---|---|---|
| Dealer-sign problem; 4-tier data ladder | §2.3 (identical table) | **CONFIRMED** |
| We sit at **tier 1** (raw OI + assumed sign) on free data | §2.3 | **CONFIRMED** |
| OI is previous-settlement, stale intraday | §2.2, §2.2.8 | **CONFIRMED** |
| OI staleness is worst for 0DTE → intraday snapshots mandatory | §2.2.8, §3.3–3.4 | **CONFIRMED** |
| Walls are zones, not ticks | §4.2 | **CONFIRMED** |
| Gamma flip = root of NetGEX(S*)=0 via repricing across hypothetical spot | §5.1 | **CONFIRMED** |
| Per-strike IV required; single ATM IV manufactures false walls | §1.4, §1.9, H13 | **CONFIRMED** |
| Exact expiry clock, not days/365 | §1.6, §10.5 | **CONFIRMED** |
| Walls are context, not triggers; price action supplies confirmation | §VII, §12 | **CONFIRMED** |
| Strength ≠ probability of attraction | §4.4, §4.4.1 | **CONFIRMED** |
| Hedge sign from `dΔ = Γ·dS`: long γ counter-cyclical, short γ pro-cyclical | §II | **CONFIRMED** |
| `GEX = Γ·OI·M·S²·0.01` = $ delta per 1% move | §2.1 | **CONFIRMED** |

---

## 3. My contributions the paper does not state (retain)

1. **Gamma unit-mass theorem — PROVEN.** `∫Γ dS = Δ(∞) − Δ(0⁺) = 1` for **every** T, σ, r. Delta is the
   CDF, gamma its density. The paper says near-expiry gamma "localizes"; the sharper statement is that
   total gamma is **conserved and redistributed, never created**. Verified numerically for 30d/1d/2.4h
   (all = 1.00000).
2. **1/√T is asymptotic, not exact — PROVEN.**
   `ratio = √(T₂/T₁)·exp[(T₂−T₁)(2r+σ²)²/(8σ²)]`. Consistent with the paper's use of `≈` in
   `Γ_ATM ≈ 1/[Sσ√(2πT)]`; the correction factor (≈1.0005 at σ=0.22) exactly explains the observed
   17.33 vs √300 = 17.32 discrepancy.
3. **Resolution floor / false-precision limit — MEASURED.** Material gamma lives within ≈ **±S·σ·√T**:
   ±6.31% (30d), ±1.15% (1d), **±0.36% (2.4h)** of spot. Any level quoted tighter than this is false
   precision. Complements the paper's zone argument with a *derived* minimum width, and the band
   **breathes intraday** as √T decays.
4. **Flip-root invariance under a GLOBAL sign flip — PROVEN + MEASURED.** Negating `NetGEX(S) → −NetGEX(S)`
   preserves its roots (verified: both conventions → 717.85). So the flip **location** survives any
   *global* convention choice; only the **regime labels** invert. It does **not** survive
   **strike-dependent** signs. This refines the paper's "position signs move the root" (§5.1) — true for
   non-uniform assignments, not for a global flip.
5. **T→0 flip instability — MEASURED.** Sweeping universe × IV treatment × T moved the root **461
   NDX/MNQ points**, driven by one config (near-expiry + per-strike IV + T=2h → 706.75 vs a 714.05–717.85
   cluster). Cause: as `Γ→δ(S−K)`, NetGEX becomes a sum of near-delta spikes and the zero crossing
   **jumps discontinuously between strikes**. Consequence: the flip is **least stable exactly when 0DTE
   traders most want it** — into the close. Direct empirical support for the paper's §5.2 slope-aware
   band and H15.
6. **`γ(spot)×OI` degeneracy — MEASURED.** Ranking by γ-at-spot × OI reproduced the raw-gamma ranking
   exactly (OI contributed nothing), collapsing the "wall" onto spot. Evidence for why §2.2.5's
   `GammaPerOpenContract` and the anti-double-counting rule matter.
7. **Free data-source map — MEASURED** (§4 below).
8. **NDX and QQQ walls DISAGREE materially — MEASURED. New, and it constrains the "use both" design.**
   Running the paper's λ=0.40 zone algorithm on the same expiry (260908) and mapping both into NQ space
   through the full corrected chain:

   | Wall | via NDX (+basis) | via QQQ (÷ratio, +basis) | gap | share in own chain |
   |---|---|---|---|---|
   | Call | 30,021.1 | 30,080.0 | **58.9 NQ pts** | NDX 13.7% · QQQ 8.8% |
   | Put | 29,021.1 | 29,256.4 | **235.3 NQ pts** | NDX 5.4% · QQQ **16.6%** |

   The put disagreement is large and **the dominance ordering inverts** — QQQ's put concentration is far
   more dominant *within its own chain* (16.6%) than NDX's is (5.4%), while NDX's put wall sits on the
   round 29,000 strike. So the two instruments do not merely differ in precision; they disagree about
   **which strike is structurally dominant**.

   **Design consequence:** "use both" cannot mean averaging or summing. §9.3 already forbids naive
   addition (double-counts the same underlying risk channel). This measurement adds that a
   **reconciliation policy is mandatory** — display the disagreement as a first-class diagnostic and let
   §9.3's incremental-value test decide whether QQQ earns inclusion, rather than blending two different
   answers into one false consensus.

   Also note: λ=0.40 again collapsed both NDX zones to a **single strike** (30000–30000, 29000–29000) —
   the round-number spike problem from C5. Confirms that the volatility-normalized gap rule and the
   ±S·σ·√T width floor are both required, not optional.

---

## 4. Data sources (MEASURED)

**Working, free, no key — CBOE delayed quotes (options):**
`https://cdn.cboe.com/api/global/delayed_quotes/options/{QQQ | _NDX | _SPX}.json`

**Working, free — Yahoo (futures quote for BASIS, resolves the C2 blocker):**
`https://query1.finance.yahoo.com/v8/finance/chart/NQ=F` → `meta.regularMarketPrice`

MEASURED: `NQ=F 29,565.25`, `MNQ=F 29,565.25` — **identical**, confirming §IX's claim that NQ and MNQ
share one price level. `^NDX 29,544.154` → **Basis = +21.10**. Full chain now runs end to end.

⚠️ Two caveats before production use: (a) `NQ=F` is a **continuous front-month** series, but §9.1
requires the basis to reference **the contract actually being traded** — must resolve the specific
contract and handle quarterly rolls; (b) Yahoo and CBOE timestamps are **not synchronized**, and §9.1
warns that a 2-minute skew fabricates false level movement. Store both source timestamps and compute
basis only from a synchronized pair.

Per contract: `open_interest, gamma, delta, vega, theta, rho, iv, volume, bid, ask, bid_size, ask_size,
last_trade_price, theo`. Top level: `current_price`, OHLC, `iv30`. Symbol encodes expiry+type+strike
(`QQQ260904C00715000`). Underlying history free at `charts/historical/{sym}.json` (daily OHLC to 2004).

**Blocked (HTTP 403 / actively refused):**
- historical options chains by date (every URL pattern tried)
- open/close + participant class — the paper's **tier 3**
- CME NQ/MNQ options — CME returns an explicit anti-scraping block

**Consequences:**
- No free historical options snapshots ⇒ **capture-forward** storage, or a paid source
  (CBOE DataShop / Polygon / OptionsDX) for true arbitrary-date backtests.
- We operate at the paper's **tier 1**. Two free partial upgrades exist: **ΔOI across daily snapshots**
  (net opening vs closing — also a paper feature, §10.2) and **last-trade vs bid/ask midpoint** (crude
  aggressor lean; weak — snapshot, not a trade tape).
- **This is the binding constraint on §XI.** The paper's research program requires timestamp-accurate
  reconstruction of quotes, IV surface and OI at each event. Capture-forward can satisfy that **going
  forward** but cannot recreate history.

**Feed adequacy vs the paper's §10.2 requirements:**

| Requirement | Free CBOE feed | Gap |
|---|---|---|
| OI by strike/expiry | ✅ | settlement date not published — must stamp capture time |
| Per-contract greeks | ✅ (vendor) | vendor model/version unknown |
| Per-series IV | ✅ | our own surface fit still needed for scenarios |
| Bid/ask/spread | ✅ | — |
| Exact expiry timestamp | ⚠️ derived from symbol | AM/PM settlement must be mapped separately |
| Forward/carry inputs | ❌ | must infer from parity or supply rates/dividends |
| Trade-level participant class | ❌ | tier 3 unavailable → sign stays an assumption |
| NQ price for basis | ❌ | needs a futures quote source |
| ΔOI history | ❌ | created by capture-forward |

---

## 5. Live measurement snapshot (MEASURED — one weekend capture, QQQ spot 717.5)

Recorded for reproducibility; **not** validated levels. Ratio NDX/QQQ = 41.1765 measured live;
**basis not applied** (no NQ quote) — so the NDX column is *not* an NQ level (see C2).

| Structure | QQQ | NDX-equiv | vs spot |
|---|---|---|---|
| Call Wall | 750.00 | 30,882 | +4.53% |
| Put Wall | 700.00 | 28,824 | −2.44% |
| 0DTE Call Wall | 730.00 | 30,059 | +1.74% |
| 0DTE Put Wall | 710.00 | 29,235 | −1.05% |
| Gamma Flip | 717.70 | 29,552 | +0.03% |
| Max Pain | 715.00 | 29,441 | −0.35% |

0DTE chain confirmed real: **622 contracts**, 475 with OI > 0, near-money OI 710P = 12,393 / 715C = 12,782,
gamma 0.0045 → 0.0335 approaching spot. Forced flow at the 715 strike alone ≈ **24,541 shares per $1
move (~$126M delta per 1% move)**; three strikes ≈ **$270M per 1%**.

**Quantization (why zones, restated):** QQQ \$1.00 strike = **41 NDX pts**; \$0.50 = **21 NDX pts**.
The measured ratio **drifts** (dividends, expense ratio) and must be recomputed live, never hardcoded.

---

## 6. Open questions for the build

- ~~**C3:** re-verify NDX daily expiries~~ → **RESOLVED**: NDX has dailies. Decision: **both, NDX primary.**
- ~~**NQ quote source** for `Basisₜ`~~ → **RESOLVED**: Yahoo `NQ=F`. Two follow-ups remain:
  identify the **specific traded contract** (not continuous) and enforce **timestamp synchronization** (§9.1).
- **NDX/QQQ reconciliation policy** — required by finding #8. Display disagreement; test QQQ's
  incremental value per §9.3 before including it in any composite.
- **Forward/carry inputs** for surface work (§1.3) — infer via put-call parity or supply rates?
- Whether **QQQ adds incremental value over NDX** after normalization (§9.3) — must be tested, not assumed.
- **Volume Profile** stays manual on the futures chart (§6.1, §10.1) — confirmed by the paper as the
  intended design, not a gap.
- Whether **Flip ≈ Max Pain** coincidence is systematic (**H8**) — needs multi-session capture.
