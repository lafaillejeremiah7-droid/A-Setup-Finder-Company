# The Gamma Map — Build Specification

Implementation spec extracted from **The Gamma Map: How 0DTE, GEX, Implied Volatility, and Positioning
Shape Intraday Price** (Jay Den, Sept 2026), §X–XII + appendices.

Section references point back to the paper. Empirical corrections live in `findings-so-far.md`.

---

## 1. Architecture (§10.1)

```
options data ──▶ IV surface engine ──▶ GEX engine ──▶ mapping engine ──▶ ├─ dashboard
                                                                        └─ Tradovate indicator
Volume Profile: marked MANUALLY on the futures chart (not computed by the bot)
```

| Layer | Responsibility |
|---|---|
| Data ingestion | chains/trades, quotes, IV inputs, NDX/QQQ/NQ prices, calendars, rates, timestamps |
| IV surface engine | quote filtering, forward/carry inference, IV inversion, surface fit, exact clocks, no-arb checks, scenario dynamics |
| GEX engine | greeks from current surface; signed inventory scenarios; total & 0DTE net/gross GEX; gamma-by-strike; roots; zones; concentration; persistence |
| Mapping engine | NDX/QQQ → NQ basis + scaling, futures-roll awareness, settlement handling |
| Dashboard | regime, zone table, gamma-by-strike, intraday migration, divergence, data quality, IV context |
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

## 10. Build order

1. **Capture-forward snapshot job** — CBOE QQQ (+NDX) → immutable timestamped JSON. *Starts history; unblocks ΔOI, persistence, and every quantile calibration.*
2. **IV surface engine** — quote filtering, exact clocks, per-strike IV, quality flags, versioning.
3. **GEX engine** — greeks, sign scenarios, total/0DTE net + gross, gamma-by-strike, zone algorithm, flip + swept zone.
4. **Mapping engine** — `Basisₜ` source resolved (Yahoo `NQ=F`). Must add: specific-contract resolution
   (not continuous front-month), quarterly-roll handling, timestamp synchronization, basis outlier filter,
   and a theoretical `F ≈ S·exp[(r−q)τ]` fallback for staleness detection (§9.1).

   **Instrument policy — both, NDX primary.** NDX: daily expiries confirmed, finer strike grid in index
   terms, more contracts, European/cash-settled (no early-exercise modelling), no ETF ratio step.
   QQQ: secondary cross-check only.

   **Reconciliation is mandatory, not optional.** Measured NDX-vs-QQQ wall disagreement is 58.9 NQ pts
   (call) and 235.3 NQ pts (put), with the dominance ordering *inverting* between chains. §9.3 already
   forbids naive addition (double-counts one risk channel). Therefore: compute each instrument
   independently, normalize to common index-point/dollar-gamma units, **display the disagreement as a
   first-class diagnostic**, and admit QQQ into any composite only if it passes §9.3's incremental
   out-of-sample test. Never blend two different answers into a false consensus.
5. **Dashboard** (Streamlit) — regime, zone table, gamma-by-strike, migration, data integrity, IV context, date picker over captured days.
6. **Tradovate indicator** — thin renderer: zone rectangles + flip band + optional core lines, fed by the engine.
7. **Research store + backtest harness** — event states, matched controls, walk-forward, H1–H15.

**Do not ship strength bands as calibrated until enough snapshots exist to compute stable quantiles.**
