# The Gamma Map — Build Specification

Implementation spec extracted from **The Gamma Map: How 0DTE, GEX, Implied Volatility, and Positioning
Shape Intraday Price** (Jay Den, Sept 2026), §X–XII + appendices.

Section references point back to the paper. Empirical corrections live in `findings-so-far.md`.

---

> ### ⚠️ Two measured constraints that reorder this build
> See **[`feed-quality.md`](./feed-quality.md)**.
>
> 1. **Feed gamma is unusable for NDX (C8).** 4-dp quantization = 6.7% of peak gamma; 18 adjacent strikes
>    (**90 index points**) report identical gamma. Gamma must be computed from IV via a fitted surface.
>    **The IV surface engine is therefore a hard prerequisite for NDX walls, not a refinement.**
> 2. **`current_price` contradicts the options' own quotes (C9)** by −46 NDX pts / +0.59 QQQ pts, in
>    opposite directions. Forwards must come from put-call parity per expiry. **This contaminates the basis
>    currently computed in `capture.py`.**
>
> Net effect on ordering: QQQ becomes the near-term path (its feed gamma is well resolved at 0.2% of peak),
> and NDX unlocks once the surface engine lands.

## 0. SCOPE: indicator only (user decision, 2026-09-06)

**Deliverable is an indicator. Not a bot, not a dashboard.** No signals, no entries, no orders, no
trade management, no Streamlit UI. The output is lines and zones on an MNQ chart.

One constraint survives this cut and cannot be removed: **a Tradovate indicator cannot fetch options
data.** Options exist on NDX/QQQ at CBOE; a chart script has no route to them. So the indicator needs a
**level calculator** upstream — a script that reads the chain and writes out the wall levels. That
calculator is not a bot in any trading sense: it makes no decisions and expresses no opinion about
direction. It converts option chains into price levels. Nothing else.

| Was planned | Indicator-only status |
|---|---|
| Capture + distill layer | **KEEP** — the only source of options data |
| IV surface → GEX → zones | **KEEP** — this is what produces the walls |
| Basis mapping to MNQ | **KEEP** — this is what makes levels land on the right price |
| Level calculator → levels file | **KEEP** — minimal; replaces "the bot" |
| Tradovate renderer | **KEEP** — this is the indicator |
| Streamlit dashboard | **DROPPED** |
| Signals / entries / automation | **DROPPED** (was never in scope; explicitly out now) |
| Backtest harness (H1–H15) | **DEFERRED** — nothing to backtest until levels exist |

### Consequence: strength bands no longer block the build

Low/Med/High was going to need weeks of capture history to calibrate percentiles. It does not.
`GlobalShare` — a strike's share of total gamma in the snapshot — is a **within-snapshot** percentage,
computable from a single capture. So strength works on day one:

- **Phase 1 (now):** strength from within-snapshot share. Zero history required.
- **Phase 2 (later):** re-express bands as percentiles against accumulated history, which distinguishes
  "big for today" from "big in absolute terms." Strictly an upgrade, not a prerequisite.

This also relaxes storage: history is a nice-to-have for band calibration, not the product. A rolling
~60 trading days at ~200 KB/snapshot ≈ 84 MB, comfortably version-controllable.

---

## 1. Architecture (§10.1)

```
options data ──▶ IV surface engine ──▶ GEX engine ──▶ mapping engine ──▶ levels file ──▶ Tradovate
   (CBOE)                                                                                indicator
Volume Profile: marked MANUALLY on the futures chart (not computed)
```

| Layer | Responsibility |
|---|---|
| Data ingestion | chains/trades, quotes, IV inputs, NDX/QQQ/NQ prices, calendars, rates, timestamps |
| IV surface engine | quote filtering, forward/carry inference, IV inversion, surface fit, exact clocks, no-arb checks, scenario dynamics |
| GEX engine | greeks **computed from the fitted surface — NEVER read from the feed (C8)**; signed inventory scenarios; total & 0DTE net/gross GEX; gamma-by-strike; roots; zones; concentration; persistence |
| Mapping engine | NDX/QQQ → NQ basis + scaling, futures-roll awareness, settlement handling |
| ~~Dashboard~~ | **dropped — indicator-only scope (§0)** |
| Tradovate indicator | minimal rectangles + optional core-strike lines **only** |
| Research store | immutable timestamped snapshots for backtest and model audit |

**Tradovate's role is confirmed as a thin renderer** — the paper never asks the indicator to compute
GEX. It draws Call Wall Zone, Put Wall Zone, 0DTE Call/Put Zones, a shaded Gamma Flip Zone, and optional
thin core-strike lines (§10.4). This resolves the earlier blocker: Tradovate cannot fetch options data,
and per the paper it does not need to.

---

## 2. Core formulas (all verified — see `findings-so-far.md`)

```
Δ = ∂V/∂S                     Γ = ∂Δ/∂S = ∂²V/∂S²
Γ = φ(d₁)/(S σ √T)            d₁ = [ln(S/K) + (r−q+σ²/2)T]/(σ√T)
Γ_call = Γ_put                (PROVEN — sign convention is a DEALER assumption, §1.0)
Γ_ATM ≈ 1/[S σ √(2πT)]        (asymptotic)

GEXᵢ ≈ sᵢ · Γᵢ · OIᵢ · Mᵢ · S² · 0.01        # $ delta per 1% move
NetGEX  = Σ GEXᵢ              GrossGEX = Σ |GEXᵢ|       (report BOTH — §2.1.1)

Pain(P) = Σ_calls OIᶜ·max(P−K,0)·M + Σ_puts OIᵖ·max(K−P,0)·M
MaxPain = argmin_P Pain(P)

Basisₜ = NQₜ − NDXₜ           MappedNQ = Level_NDX + Basisₜ
rₜ = QQQₜ/NDXₜ                K_NDXeq = K_QQQ/rₜ
NQ level ≡ MNQ level          (1:1 — verified difference 0.0000)
GammaPerOpenContract(Z) = Σ|GEXᵢ| / max(ΣOIᵢ, 1)
ZoneOverlap = len(Z_OI ∩ Z_GEX)/len(Z_OI ∪ Z_GEX)
ExpectedMoveScale ≈ S·σ_IV·√τ         (normalization only — NOT a range guarantee, §1.1.1)
k = ln(K/F_T)                          (surface coordinate, §1.3)
```

**Units warning (§2.1.1):** "$5B GEX" is meaningless without its convention. Publish units, sign
convention, expiry universe, multiplier and underlying level. Cross-vendor comparison is invalid until
normalized.

### 2.1 Two DIFFERENT mappings — do not conflate (this caused correction C2)

| Mapping | Relationship | MEASURED | Applies to |
|---|---|---|---|
| **NQ ↔ MNQ** | **exactly 1:1** — same quoted price | diff **0.0000** (both 29,565.25) | price levels, walls, zones |
| **NDX → NQ** | **+ Basisₜ** — NOT 1:1 | **+21.10** pts = 84 ticks = **\$422/NQ contract** | every index-derived level |
| **QQQ → NDX** | ÷ rₜ (drifts, recompute live) | r = 0.024286 (1/r = 41.1765) | ETF-derived levels |

```
NDX strike ──(+basis)──▶ NQ level ──(1:1)──▶ MNQ level
QQQ strike ──(÷ratio)──▶ NDX-equiv ──(+basis)──▶ NQ ──(1:1)──▶ MNQ
```

**So one mapped level serves both NQ and MNQ charts** — confirmed (refs [22][23]). Tick is 0.25 index
points on both. But the **multiplier differs by 10×**: NQ = \$20/pt (\$5.00/tick), MNQ = \$2/pt
(\$0.50/tick).

**Where the multiplier still matters despite 1:1 pricing:** the strength score's
**HedgeFlow/Liquidity** component (§4.4) is *modeled delta change ÷ expected liquidity*. MNQ and NQ have
**different liquidity pools**, so the denominator is instrument-specific even though the numerator's
price level is shared. Do not reuse one hedge-capacity figure across both contracts.

**And basis is not negligible** — +21.10 pts is 84 ticks. Dropping it (my original error) silently
mis-places every wall by roughly \$422 per NQ contract of level error.

---

## 3. Zone algorithm (§4.2, Appendix C)

```
1. Filter invalid/stale series; compute gamma for every option from the CURRENT surface
2. Convert to declared GEX units; aggregate by strike
3. Universe: broader chain → Call/Put Walls ; today's expiries → 0DTE Walls
4. Find local peaks above a training-set concentration threshold (e.g. 90th pct or min share)
5. Grow contiguous clusters while exposure ≥ λ · local_peak  AND  gap ≤ max_gap
      λ = 0.40 (Appendix A);  max_gap = VOLATILITY-NORMALIZED, not a fixed point count
6. Merge overlapping clusters; reject clusters below a minimum economic notional
7. core strike = argmax concentration inside cluster
8. centroid   = exposure-weighted strike (additional descriptor)
9. Compute global share, local dominance, persistence, breadth, confidence
10. Map index/ETF zones → NQ via synchronized basis/scaling  (greeks FIRST, map SECOND — §9.4)
11. Save immutable snapshot + model version
```

**Thresholds must be tuned in training data only.** Zone width must additionally be floored at the
physical resolution limit **±S·σ·√T** (my measurement; complements §4.2's uncertainty bands).

---

## 4. Strength = concentration, NOT probability (§4.4) — the Low/Med/High answer

The paper defines strength explicitly. It is **structural importance**, never "chance price returns."

**Components:**
- **Global share** — zone |GEX| ÷ total |GEX| in the chain universe
- **Local dominance** — zone exposure ÷ median/mean exposure of neighbouring strikes
- **Peak ratio** — core-strike exposure ÷ second-largest nearby peak
- **Persistence** — fraction of recent snapshots the zone stayed within a small mapped distance
- **Cross-expiry breadth** — number/weight of expiries contributing to the same mapped area
- **Inventory confidence** — high with participant data, **low under OI-only sign assumptions (us)**
- **Surface/greek confidence** — quote freshness, spread quality, IV-fit stability, clock accuracy
- **Estimated hedge-flow capacity** — modeled delta change for a standard move ÷ expected liquidity

**Composite (research specification — weights must be validated out of sample):**

```
C = 0.35·z(GlobalShare) + 0.25·z(LocalDominance) + 0.20·z(Persistence) + 0.20·z(HedgeFlow/Liquidity)
```

**Banding:** map `C` to **Weak / Moderate / Strong / Extreme** using **stable historical quantiles** —
*not* fixed percentages. "Extreme" means extreme concentration **relative to history**, not extreme
probability of reversal.

**Two fields must stay separate:** *structural importance* and *measurement confidence*. A very large
but poorly measured wall is not a high-confidence wall.

> Answering the original question directly: the paper's strength bands are **quantile-based on a
> composite concentration score**, so they cannot be hardcoded — they require captured history to
> calibrate. Until enough snapshots exist, display raw components + "uncalibrated."

---

## 5. Gamma Flip (§5.1–5.2)

Solve `NetGEX(S*) = 0` by repricing **every** option's gamma across a dense hypothetical-spot grid.

**Requirements:**
- Evaluate the **actual sign on each side** — do **not** hardcode "above flip = positive gamma."
- If **multiple roots** exist, do not silently pick one: report the nearest economically relevant root
  and **flag** the others.
- State the **surface-dynamics assumption** used during the scan (sticky-strike / sticky-delta / other)
  and stress at least one alternative (§1.7).

**Flip Zone — three defensible methods (a fixed ±10/±20/±1% band is arbitrary):**
1. **Model ensemble** — recompute the root under plausible IV surfaces, sign assumptions and expiry
   filters; zone edges = quantiles of the root distribution. *(This is what I implemented.)*
2. **Exposure-tolerance band** — contiguous S range where `|NetGEX(S)|` sits below a small historical
   percentile of absolute Net GEX (genuinely near-neutral).
3. **Slope-aware confidence band** — widen when the curve is flat at the root, narrow when it crosses
   steeply. *(Directly addresses my measured T→0 instability.)*

---

## 6. 0DTE handling (§III)

Compute **two** regimes and flag divergence (§3.1):

| Total GEX | 0DTE GEX | Interpretation |
|---|---|---|
| + | + | both lean counter-cyclical → mean-reversion hypothesis strongest |
| − | − | both lean pro-cyclical → momentum/expansion strongest |
| + | − | longer horizon stabilizing, same-day destabilizing near spot → **divergence flag** |
| − | + | broader fragility, same-day damping → **divergence flag** |

**Five mechanisms move a wall intraday — record attribution separately (§4.3.2):**
spot/moneyness · time decay/exact clock · IV-skew change · new/closing flow · NDX→NQ basis.
Collapsing these into one "wall moved +50" destroys the backtest's ability to separate informative
migration from mechanical noise.

**Stale-OI discipline (§2.2.8):** keep three distinct objects — prior-settlement 0DTE OI, intraday 0DTE
volume, inferred live dealer inventory. Without a good flow classifier, use morning OI as a structural
prior and **progressively decay confidence** through the session. Display OI settlement date.

**Volume ≠ positioning (§3, §11 Fed survey):** institutional 0DTE spans spreads, directional, vol and
calendar trades. Gross call/put volume does **not** reveal dealer inventory direction.

---

## 7. Data-quality rules (§10.5) — non-negotiable

- Timestamp **OI separately from quotes**; fresh quotes ≠ fresh position data.
- Reject/flag crossed or zero markets, impossible IVs, stale quotes, negligible liquidity.
- Use **exact time to actual settlement**, never rounded days/365 for 0DTE.
- Handle rates and dividend/carry consistently; European formulas for index options, appropriate
  American model (or trusted vendor greeks) for QQQ.
- Never mix AM- and PM-settled expiries without correct clocks.
- **Never** use one constant ATM IV for all strikes when skew exists.
- Snapshot exact inputs + model version for every historical level.
- **Never retroactively overwrite a historical zone with later OI.**
- For flip scans, declare IV repricing behaviour and stress an alternative.
- Treat wide spreads, zero bids, stale forwards and parity inconsistencies as **surface-quality
  warnings** rather than forcing a smooth IV through every quote.

---

## 8. Research program (§XI) — what makes this falsifiable

**Look-ahead bias is the top risk.** Testing a 10:00 event with a 16:00 chain uses future information.

**Event states (§VII, §11.2)** — replace "support/resistance" with measurable states:
`approach → interaction → rejection | acceptance → continuation | retest hold`, plus penetration depth
(volatility-normalized), dwell time, pinning, revisit time. Parameters H, X, T, m, Y, H₂ must be fixed
**before** looking at results — sweeping thresholds manufactures an edge.

**Matched controls (§11.4):** every wall interaction needs pseudo-level controls matched on time of day,
distance, volatility, **round-number status** and recent trend. Otherwise the study merely rediscovers
that orders cluster at salient prices (§8.2). Use "predicts"/"is associated with" — not "causes" —
absent credible identification.

**Validation (§11.5):** walk-forward by calendar time (never shuffle intraday); hold out whole
months/quarters; report effect sizes + CIs; control FDR across the large hypothesis family; include
commissions/fees/spread/slippage; stress CPI, FOMC, payrolls, quarterly expiry, roll week, high-VIX,
thin sessions; re-run under alternative sign models and alternative IV surfaces; re-run NDX-only vs
QQQ-only vs combined; **publish failures**.

**Hypotheses:** H1–H9 (regime, concentration, overlap, max pain, migration), H10–H12 (OI incremental
value), H13–H15 (IV surface value, surface-driven migration, scenario robustness).

**Economic significance (§11.6):** a statistically significant 2-point NQ reaction is worthless if
spread + slippage + stop distance consume it. Objective = conditional expectancy and risk-adjusted
performance, **not win rate**.

---

## 9. Operating framework (§XII) — five questions

1. **WHERE?** Call/Put Wall Zones, 0DTE Call/Put Zones, Gamma Flip Zone, OI concentration, manual Volume Profile.
2. **HOW IMPORTANT?** GEX concentration, OI persistence, 0DTE overlap, breadth, surface quality, data confidence — *not* an invented attraction score.
3. **WHAT REGIME?** Total and 0DTE gamma signs; do they agree; is the sign robust to alternative inventory/surface assumptions?
4. **WHAT IS THE SURFACE DOING?** Did 0DTE ATM IV, skew or event repricing move the gamma map? High IV is **context, not a level**.
5. **WHAT IS PRICE DOING?** Interaction vs rejection vs acceptance vs breakout vs retest. **The auction decides whether the structure is active.**

**Order-flow delta (§6.3)** — stays a **manual** confirmation layer (no reliable free feed).
Negative delta is *not* support. The signal is **absorption**: aggressive flow with **no price progress**.
This matches the discretionary method already in use (absorption + delta + walls + price action).

---

## 9a. User's minimal indicator spec — reconciliation (2026-09-06)

The user supplied a concrete, minimal spec for the finished Tradovate indicator. It agrees with this
document almost point-for-point. Recording it as the **authoritative shape of the output**, plus the
three places it meets a measured constraint.

**The user's rules (verbatim intent):**
- Four independent structures: **Call Wall, Put Wall, 0DTE Call Wall, 0DTE Put Wall.**
- Each: **rank strikes by side-specific GEX magnitude, take top 3.** Clustered → **Zone** (core = strongest
  of the three). Dispersed → **single line at the strongest strike.** Cluster-vs-dispersed threshold is to
  be **fixed and tested**, not left subjective.
- **Total GEX** = broad expiry universe; **0DTE GEX** = only options expiring the current trading day.
- **Gamma Flip** = solve `NetGEX(S)=0` by repricing across hypothetical spot; render a **small neutral gray
  zone**, not a precise point, and not automatically S/R.
- **Strength** = WEAK / MODERATE / STRONG / EXTREME, meaning structural dominance, **never** probability
  of reversal.
- **Unit**: dollar GEX **per 1% move**, one consistent convention (e.g. "$1.82B GEX / 1%").
- **Chart shows only**: the four structures (zone or line), core strike, $GEX/1%, strength rating, flip
  zone, and a tiny `TOTAL: ± / 0DTE: ±` regime line. **Hide** raw IV, OI, per-strike greeks, Max Pain,
  full chain, distance-to-level, dashboards.
- **NDX→NQ**: `NQ Wall = NDX Wall + Basisₜ`, synchronized prices; NQ and MNQ share levels.
- **Replay**: strict no-look-ahead — at a replay timestamp use only data known then.

**Three reconciliations with what was measured (see `feed-quality.md`):**

1. **"top-3 clustered → zone, else line" is a clean simplification of the §4.2 λ-cluster algorithm, and it
   wins for a v1.** Keep the fuller λ=0.40 / volatility-normalized-gap growth as the internal engine, but
   the top-3 rule is what the indicator surfaces. **The cluster/dispersed threshold must be floored at the
   physical resolution limit ±S·σ·√T** — two strikes closer than that are not distinguishable structures
   regardless of GEX, so "clustered" below the floor is automatic.

2. **The user lists "option gamma, or inputs needed to calculate gamma."** Measurement forces the second
   branch for NDX: **feed gamma is quantized to ~14 distinct values across 142 near-money NDX strikes (C8)**,
   a 90-point smear. The top-3 ranking would tie dozens of strikes. **Gamma MUST be computed from IV via the
   fitted surface for NDX** before ranking. QQQ's raw feed gamma (0.2% of peak) can rank directly for a v1.

3. **The user's basis formula uses `NDX_t`.** Do **not** use the feed's `current_price` for that term:
   it disagrees with the options' own parity-implied spot by −46 NDX pts (C9), which would bias every
   mapped wall. Use the **parity-implied forward per expiry** as the index anchor, then apply basis.

**Consequent v1 vs v2 split (indicator-only scope, §0):**
- **v1 (QQQ):** raw-feed gamma is clean enough to rank → the four structures + flip + regime + strength
  (Phase-1 within-snapshot share) render immediately. Proves the whole pipeline end to end.
- **v2 (NDX/NDXP):** unlocks once the IV surface engine computes gamma (C8) and parity forwards feed the
  basis (C9). This is the higher-resolution, daily-expiry-native path the user ultimately wants.

---

## 10. Build order

> Reordered by measurement (`feed-quality.md`): the IV surface moved up because NDX gamma is unusable
> raw (C8), and the basis fix (C9) belongs with it. Dashboard removed — indicator-only scope (§0).

1. ✅ **DONE — Capture-forward snapshot job.** CBOE `_NDX`+QQQ → immutable timestamped snapshots, distilled
   storage, round-trip-verified. Commits `e581aa8`, `bc7391c`, `dc3cbe9`.
2. **IV surface engine** — quote filtering, exact clocks, **parity forward per expiry (C9)**, per-strike IV,
   **fitted surface so gamma is computed not read (C8)**, quality flags, versioning. *Hard prerequisite for
   NDX; not optional.*
3. **GEX engine** — greeks from the surface, sign scenarios, total/0DTE net + gross, gamma-by-strike,
   **top-3 → zone-or-line** rule with the ±S·σ·√T floor, flip zone via `NetGEX(S)=0`.
4. **Mapping engine** — `Basisₜ` from Yahoo `NQ=F` **minus parity-implied index anchor (C9)**. Must add:
   specific-contract resolution (not continuous front-month), quarterly-roll handling, timestamp
   synchronization, basis outlier filter, theoretical `F ≈ S·exp[(r−q)τ]` fallback for staleness (§9.1).

   **Instrument policy — both, NDX primary.** NDX: daily expiries confirmed, finer strike grid in index
   terms, more contracts, European/cash-settled (no early-exercise modelling), no ETF ratio step.
   QQQ: secondary cross-check only.

   **Reconciliation is mandatory, not optional.** Measured NDX-vs-QQQ wall disagreement is 58.9 NQ pts
   (call) and 235.3 NQ pts (put), with the dominance ordering *inverting* between chains. §9.3 already
   forbids naive addition (double-counts one risk channel). Therefore: compute each instrument
   independently, normalize to common index-point/dollar-gamma units, **display the disagreement as a
   first-class diagnostic**, and admit QQQ into any composite only if it passes §9.3's incremental
   out-of-sample test. Never blend two different answers into a false consensus.
5. ~~**Dashboard** (Streamlit)~~ — **DROPPED, indicator-only scope (§0).**
6. **Level calculator + Tradovate indicator** — the engine writes the four structures (zone/line + core +
   $GEX/1% + strength + flip + regime) to a levels file; the Tradovate JS indicator draws only those, with
   **replay-correct no-look-ahead** (levels update only when historically-available data would have changed
   them). This is the deliverable.
7. **Research store + backtest harness** — **DEFERRED** until levels exist; event states, matched controls,
   walk-forward, H1–H15.

**Do not ship strength bands as calibrated until enough snapshots exist to compute stable quantiles.**
