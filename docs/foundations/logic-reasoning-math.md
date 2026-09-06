# Logic, Reasoning, and Mathematics — Foundations and Their Highest Forms

A reference on what these three things actually *are*, what their most advanced forms look like,
what they provably *cannot* do, and how each maps onto reasoning about markets.

Written from first principles. Nothing here is strategy-specific.

---

## Part 1 — LOGIC

### 1.1 What logic is

Logic is the study of **valid inference**. Its subject is *form*, not content. A logical rule is
valid if it is **truth-preserving**: whenever the premises are true, the conclusion must be true.
Logic never tells you whether your premises are true — only what follows from them.

The central distinction:

- **Syntax** — symbol manipulation by rules. `⊢` ("provable from")
- **Semantics** — meaning, truth in a model. `⊨` ("true in all models of")

Two properties bind them:

- **Soundness**: if `Γ ⊢ φ` then `Γ ⊨ φ`. Anything you can prove is actually true. (Your rules don't lie.)
- **Completeness**: if `Γ ⊨ φ` then `Γ ⊢ φ`. Anything true is provable. (Your rules don't miss anything.)

### 1.2 The ascent of formal systems

| Level | Expressive power |
|---|---|
| Propositional logic | truth-functional connectives; decidable |
| First-order logic (FOL) | quantifies over objects; sound & complete (Gödel 1930), but *undecidable* |
| Second/higher-order | quantifies over predicates/sets; more expressive, **loses completeness** |
| Modal logic | necessity/possibility; epistemic (knowledge), temporal (time), deontic |
| Non-classical | intuitionistic (rejects excluded middle, proof = construction), paraconsistent (tolerates contradiction), many-valued/fuzzy (degrees) |

### 1.3 The highest form: metalogic

The highest form of logic is not proving things *within* a system — it is proving things *about*
systems: **metalogic**. Reasoning about the reach and limits of reasoning itself. Its central
results are all *limitative*, and they are among the deepest facts known:

- **Gödel's First Incompleteness Theorem** — Any consistent, effectively axiomatized formal system
  strong enough to express arithmetic contains statements that are **true but unprovable within it**.
  Completeness and consistency cannot be had together at that strength.
- **Gödel's Second Incompleteness Theorem** — Such a system **cannot prove its own consistency**.
  No sufficiently powerful framework can self-certify.
- **Tarski's Undefinability of Truth** — Truth for a language cannot be defined *within* that
  language. Semantics needs a metalanguage.
- **Church–Turing / Halting Problem** — Validity in FOL is undecidable; no algorithm decides all
  cases. **Rice's Theorem** generalizes: essentially all non-trivial semantic properties of
  programs are undecidable.
- **Löwenheim–Skolem** — First-order theories cannot pin down the size of their models; formal
  description always underdetermines reality.

**The lesson:** formal reasoning is extraordinarily powerful and *provably bounded*. Every
sufficiently expressive system has blind spots it cannot see from inside.

### 1.4 What this means for markets

1. **Validity ≠ truth.** A flawlessly valid deduction from a false premise yields a false
   conclusion with total confidence. Most catastrophic failures are not logical errors — they are
   valid reasoning from unexamined premises ("this relationship is stable," "returns are normal,"
   "liquidity will be there"). *Audit premises, not just inference.*
2. **No closed system captures an open, adaptive world.** A market includes agents who model the
   market. That self-reference is exactly the setting where incompleteness bites. There is no
   complete, consistent, finite rule-set that decides every market state correctly.
3. **Undecidability sets a ceiling.** You cannot build a total decision procedure for "is this a
   good trade." You can only build systems with **explicitly bounded scope** that abstain outside it.
   Knowing when a system does *not* apply is part of the system.
4. **Consistency must be checked from outside.** A framework cannot validate itself. This is the
   formal argument for out-of-sample testing, independent review, and adversarial audit.

---

## Part 2 — REASONING

Logic is the skeleton. Reasoning is the living process of moving from evidence to belief to action
under uncertainty — where the premises are *not* given.

### 2.1 The three modes

| Mode | Direction | Certainty | Role |
|---|---|---|---|
| **Deduction** | rule + case → result | Certain (given premises) | Guarantees; adds no new content |
| **Induction** | cases → rule | Probable only | Generalization from observation |
| **Abduction** | result + rule → best case | Plausible only | Hypothesis *generation*; explanation |

**Deduction** is the only truth-preserving mode, and it is *analytic* — the conclusion was already
contained in the premises. It cannot generate new knowledge about the world.

**Induction** generates new knowledge but has no logical justification. **Hume's problem of
induction**: any argument that the future will resemble the past must itself assume that the future
resembles the past — circular. Compounding it, **underdetermination**: infinitely many hypotheses
fit any finite dataset. Induction is indispensable and philosophically unsecured.

**Abduction** (Peirce) is inference to the best explanation. It is the creative act — where
hypotheses come from. It is the *weakest* form (many explanations fit) and yet the most necessary,
because deduction and induction can only operate on hypotheses already proposed.

### 2.2 The highest form: probabilistic + causal + falsificationist reasoning

The most advanced form of reasoning is not any single mode. It is a disciplined **cycle**:
abduce hypotheses → deduce testable consequences → test severely → update beliefs probabilistically
→ seek the causal mechanism → and hold the result only as strongly as the evidence warrants.

Four pillars:

**(a) Probability as the unique extension of logic to uncertainty.**
- **Cox's Theorem** — Any system of reasoning about degrees of belief satisfying basic consistency
  desiderata (representable by a real number, consistent with Boolean logic, path-independent) is
  **isomorphic to probability theory**. Probability is not *a* choice for handling uncertainty; it
  is the *only* consistent one. (Jaynes: probability theory *is* extended logic.)
- **de Finetti / Dutch Book** — Beliefs violating the probability axioms admit a set of bets that
  loses money with certainty. Incoherence is exploitable — a fact with unusually literal force in
  markets.
- **Bayes' theorem** — the update rule: `P(H|E) = P(E|H)·P(H) / P(E)`. In odds form, the cleanest
  statement of evidence: `posterior odds = prior odds × likelihood ratio`.

**(b) The idealized limit of induction.**
- **Solomonoff induction** — the formally optimal inductive method: weight every computable
  hypothesis by its algorithmic simplicity (`2^−K(h)`, where `K` is **Kolmogorov complexity**),
  update on data. This is Occam's Razor made exact. It is provably optimal and **uncomputable** —
  the ceiling exists but is unreachable. Practical shadows: **Minimum Description Length**,
  regularization, model complexity penalties.

**(c) Causal reasoning** (Pearl's ladder — you cannot climb it with statistics alone):
1. **Association** — `P(y|x)`. Seeing. Correlation. What most analysis stops at.
2. **Intervention** — `P(y|do(x))`. Doing. Requires a causal model; this is what a *strategy* needs.
3. **Counterfactuals** — `P(y_x|x′,y′)`. Imagining. "Would this have worked had I not acted?"

Confounding, collider bias, Simpson's paradox, and selection effects all mean observed association
can be *any* sign relative to the true causal effect. **do-calculus** specifies when intervention
can be identified from observation — and when it *cannot*.

**(d) Severe testing.**
- **Popper** — theories are never verified, only falsified. Prefer bold, highly falsifiable claims.
- **Duhem–Quine** — you never test a hypothesis alone, always hypothesis + auxiliary assumptions;
  a failure doesn't say *which* was wrong. So falsification is real but not surgical.
- **Mayo's severity** — evidence supports a claim only to the degree the test **would probably have
  detected the error had it been present**. A test that could not have failed provides no support.
  This is the single most useful epistemic standard for empirical work.

**(e) Decision theory** — separates belief from preference:
- **von Neumann–Morgenstern / Savage** — rational preference under stated axioms is representable
  as maximizing **expected utility**. Probability governs belief; utility governs value.
- Crucially: correct beliefs + wrong utility function = ruin. Getting the probabilities right is
  only half.

### 2.3 The boundary: what probability cannot absorb

- **Knightian uncertainty / ambiguity** — risk (known distribution) vs uncertainty (unknown
  distribution). Ellsberg showed people rationally treat these differently; robust and
  distributionally-ambiguous decision theory takes it seriously.
- **Model risk** — the probability that your entire model class is wrong. Not inside the model.
- **Unknown unknowns** — hypotheses never proposed cannot be updated on. Abduction is the
  bottleneck, and it has no algorithm.

### 2.4 What this means for markets

1. **A backtest is an inductive argument** — and inherits every weakness of induction. It says
   "this pattern held in this sample." Hume's problem is not an abstraction here; it is the
   central practical risk.
2. **Overfitting is a failure of inductive inference**, formally: choosing a hypothesis whose
   complexity exceeds what the data can support. Solomonoff/MDL says the correction is an explicit
   complexity penalty. Searching many strategies makes it worse — **multiple testing** means the
   best of N random strategies looks excellent by construction. Corrections (FWER/FDR,
   deflated performance metrics, probability of backtest overfitting) are not optional bookkeeping;
   they are the difference between evidence and artifact.
3. **Severity is the right bar, not profitability.** The question is never "did it make money in
   the test?" but "**would this test have caught the strategy being worthless?**" A backtest on the
   data used to build the strategy has near-zero severity. Out-of-sample, out-of-regime, and
   out-of-asset tests have high severity.
4. **Climb Pearl's ladder.** A correlation between signal and return sits on rung 1. Trading it
   requires rung 2 — an assumption about intervention. Bridging rungs demands a **mechanism**:
   *who* transacts, *why* they must, and *what* forces the price response.
5. **Confluence must be combined in log-odds, and only survives if evidence is conditionally
   independent.** Bayes in odds form: each independent signal contributes
   `log LR` additively. But if two signals are driven by the same underlying variable, stacking
   them **double-counts the same evidence** and manufactures false confidence. Three correlated
   confirmations of one fact are one fact. *The value of a confluence system is entirely determined
   by the conditional independence of its components* — this is a measurable property, not a
   matter of taste.
6. **Separate belief from bet sizing.** Edge estimation (probability) and position sizing (utility,
   growth, ruin) are distinct problems with distinct mathematics. Conflating them is a standard
   and expensive error.
7. **Keep an explicit epistemic ledger**: what is *proven*, what is *evidenced*, what is
   *plausible*, what is *speculation*. Most bad decisions come from silent promotion between tiers.

---

## Part 3 — MATHEMATICS

### 3.1 What mathematics is

Mathematics is the study of **structure** and the **necessary relations** that hold within precisely
defined systems. Its method is: *definition → conjecture → proof → generalization*. Its output is
not calculation but **theorems** — conditional guarantees of the form "given exactly these
assumptions, this must hold."

Its distinguishing virtue is **rigor**: every term defined, every assumption explicit, every step
justified. A single counterexample destroys a theorem. Nothing else in human knowledge has this
property.

Foundational stances worth knowing (they change what counts as proof):
**Platonism** (structures exist, we discover them) · **Formalism** (meaningless symbols + rules) ·
**Intuitionism/Constructivism** (existence requires construction; rejects excluded middle) ·
**Structuralism** (only relations matter, not objects) · foundations in **ZFC set theory**,
**category theory**, and **type theory**.

### 3.2 The highest form: unification through abstraction

The highest form of mathematics is *not* computational power. Computation is the lowest rung. The
summit is **structural insight** — recognizing that two apparently unrelated things are *the same
object* viewed differently, and then proving it.

- **Isomorphism / invariant** — stripping away representation to find what is genuinely there.
  Mathematical maturity is knowing which features are structural and which are artifacts of
  description.
- **Universal properties, functors, adjunctions** (category theory) — defining objects by their
  relationships rather than their internals; the systematic study of *analogy*.
- **Grand correspondences** — Galois theory (field extensions ≅ group symmetries),
  the Langlands program (number theory ↔ representation theory), Curry–Howard
  (proofs ≅ programs). These reveal that structure recurs across domains.
- **Proof as understanding** — a proof's value is the *insight* into why something must be true,
  not the certificate that it is.

**Mathematics' honest limitation:** it delivers *conditional* truth. "If these axioms, then this
theorem." It is silent on whether the axioms describe reality. **Applying** mathematics is an
empirical act, and the mapping — not the math — is where error enters.

### 3.3 The mathematics that actually governs markets

**(a) Probability, measure-theoretically — and the concept that matters most: information.**
- Kolmogorov's axioms; probability space `(Ω, F, P)`.
- A **σ-algebra is information**. A **filtration** `{F_t}` is *information arriving over time*.
  This is the correct formalization of "what is knowable when," and it is the key to everything below.
- **Conditional expectation** `E[X|F_t]` — the best estimate given information available at `t`.
- **Martingale** — `E[X_{t+1}|F_t] = X_t`. A fair game: no expected drift given current information.
- **Optional Stopping Theorem** — for a martingale, **no stopping rule changes the expected value**.
  Any strategy built purely on *when to enter/exit* a martingale has zero expected edge. Timing
  rules cannot manufacture edge from a fair game; only information or structure can.
- **Doob decomposition** — any process splits into a martingale (unpredictable) plus a predictable
  drift. Edge lives entirely in the predictable part.

> **The sharpest formalization of "edge" available:**
> The efficient-market claim is that price is a **martingale with respect to the market's
> information filtration**. An edge exists precisely when price is **not** a martingale with
> respect to *your* filtration `G_t`. So edge ⟺ `G_t` contains something the market's pricing does
> not reflect — either **information others lack**, or **structure others cannot act on**
> (constraints, mandates, forced flows). Everything else is noise mining.
>
> Corollary: if your `G_t` is a strict subset of public information already in the price, no
> transformation of it — no indicator, no combination, no machine learning — creates edge.
> Transformation of public data adds no information; it only reorganizes it.

**(b) Stochastic calculus and the mechanics of derivatives.**
- Brownian motion; **Itô's lemma** — the chain rule for stochastic processes, where the second-order
  term survives: `df = (∂f/∂t + ½σ²S²∂²f/∂S²)dt + σS(∂f/∂S)dW`.
- **Black–Scholes** arises from replication/no-arbitrage. Its greeks are *derivatives of value*:
  `Δ = ∂V/∂S`, and **`Γ = ∂²V/∂S²`** — the curvature, i.e. how fast `Δ` changes.
- **Why gamma is mechanically real:** a delta-hedged option seller must trade the underlying to stay
  hedged. Since `dΔ ≈ Γ·dS`, holding a short-gamma position requires buying as price rises and
  selling as it falls (destabilizing, trend-amplifying); long gamma requires the opposite —
  selling rallies, buying dips (stabilizing, mean-reverting, "pinning"). This is not a pattern
  someone noticed on a chart. It is a **forced, structural flow implied by the calculus of
  hedging**, concentrated where gamma is largest. That mechanism sits on **rung 2 of Pearl's
  ladder** — it is an intervention story, not a correlation — which is exactly what distinguishes
  a structural hypothesis from curve-fitting.
- Caveat, held with equal rigor: the *mechanism* being real does not establish that any particular
  *measurement* of it is accurate, that the flow dominates other flows, or that it is not already
  priced. Mechanism grants a hypothesis the right to be tested severely — nothing more.

**(c) Market microstructure and game theory.**
- **Kyle model** — informed trader vs market maker vs noise traders; yields **linear price impact**
  and shows how information enters price *through order flow*. Order flow is the channel by which
  `G_t` becomes price. This is why flow/imbalance data is informative in a way that transformed
  public candles are not.
- **Adverse selection / inventory risk** — market makers widen or skew because some flow is informed.
- **Grossman–Stiglitz paradox** — if prices were perfectly efficient, no one would pay to gather
  information; so someone must be paid, so markets must be *nearly but not perfectly* efficient.
  This is the formal reason edges can exist at all — and why they are compensation for real costs,
  risks, or constraints rather than free money.
- **Adversarial adaptation** — markets are populated by optimizers. **Goodhart's law**, the **Lucas
  critique**, and reflexivity all say: *acting on a regularity can destroy it.* Alpha decay is
  endogenous. A strategy is not a static object being evaluated; it is a move in a game.

**(d) Statistics and learning theory — the mathematics of not fooling yourself.**
- Estimation, bias–variance decomposition, sampling distributions, standard errors.
- **p-values** measure surprise under a null; they are *not* the probability the hypothesis is
  false. Misreading this is the most common quantitative error in existence.
- **Multiple comparisons** — searching N hypotheses inflates false positives; requires FWER/FDR
  control or explicit deflation of the best result.
- **Cross-validation breaks under temporal dependence** — shuffling time series leaks the future
  into the past. Requires purging, embargoing, and walk-forward evaluation.
- **Stationarity and ergodicity** — most financial series are neither. **Non-ergodic means the time
  average ≠ the ensemble average**: what happens to *one path over time* differs from the average
  across *many paths*. Expected value computed across scenarios can badly misdescribe your actual
  trajectory.
- **VC dimension / PAC learning** — generalization error is bounded by a function of hypothesis-class
  capacity vs sample size. Capacity must be *paid for* in data.
- **No-Free-Lunch** — averaged over all possible problems, all learners are equal. Performance comes
  *only* from assumptions matching reality. There is no universal method; there is only a correct prior.

**(e) Fat tails and extremes.**
- Financial returns are not Gaussian: excess kurtosis, volatility clustering, power-law tails.
- **Extreme Value Theory** governs maxima. When tail index `α ≤ 2`, **variance is infinite** —
  sample standard deviation is meaningless and never converges. Sharpe-style ratios silently assume
  finite variance.
- Correlations rise toward 1 in crises: diversification weakens exactly when needed.

**(f) Growth, risk of ruin, and the geometry of compounding.**
- **Kelly criterion** — maximize `E[log W]` to maximize long-run growth rate. Optimal fraction
  `f* = edge/odds` (for simple bets). Over-betting reduces growth and then guarantees ruin;
  betting `2f*` has zero growth even with a real edge.
- **Arithmetic vs geometric mean** — you experience the geometric mean. Volatility drag:
  `g ≈ μ − σ²/2`. A strategy with positive expected *value* can have negative expected *growth*.
- **Path dependence and absorbing barriers** — ruin is terminal. Expected value ignores that you
  cannot continue from zero. This is the practical face of non-ergodicity.
- Fractional Kelly as the standard response to *estimation* error: since edge is estimated, not
  known, optimal sizing shrinks toward zero as parameter uncertainty grows.

---

## Part 4 — SYNTHESIS: the highest form applied to markets

### 4.1 The epistemic hierarchy of a market claim

Ascending order of trustworthiness:

1. **Coincidence** — a pattern in one sample. (Backtest on in-sample data.)
2. **Correlation** — a statistically robust association. (Rung 1. Still possibly spurious.)
3. **Out-of-sample persistence** — survives data it was not built on. (Genuine severity.)
4. **Mechanism** — a causal story naming who transacts and what forces them. (Rung 2.)
5. **Invariance** — holds across regimes, assets, and time. (Structural, not fitted.)
6. **Survivability** — persists despite being known, because it is compensation for real risk,
   cost, or constraint rather than an unexploited oversight. (Grossman–Stiglitz-compatible.)

Most analysis lives at 1–2 and is mistaken for 4–6. The discipline is refusing to promote a claim
up this ladder without earning it.

### 4.2 The three questions that must be answerable

For any claimed edge:

1. **What is the mechanism?** Which participants transact, and what compels them? If there is no
   answer, the claim is at best rung 2 — a correlation awaiting a reason.
2. **Who is on the other side, and why do they accept the loss?** Every dollar you make is someone's
   cost. Acceptable answers: they are hedging (paying for insurance), constrained (mandate,
   margin, index rule), forced (liquidation, delta-hedge), or uninformed. If no one is plausibly
   paying, you are likely measuring noise or your own fees.
3. **Why does it survive being known?** If it requires secrecy, it decays. If it is compensation for
   bearing genuine risk or providing a real service, it can persist. Goodhart applies to the rest.

### 4.3 The operating standard

- **Define before you measure.** Ambiguous definitions guarantee unfalsifiable conclusions.
- **State premises explicitly**, then attack them — that is where the real risk lives, not in the
  arithmetic.
- **Design tests that can fail.** Pre-register the falsification condition. A test that cannot
  reject provides no evidence (Mayo).
- **Count every hypothesis you tried.** Otherwise your best result is a selection artifact.
- **Respect the filtration.** Any use of information not available at decision time is leakage, and
  leakage is the most common source of illusory edge.
- **Separate belief from sizing.** Estimate edge; size for survival and growth. Different math.
- **Prefer mechanism over pattern**, and *invariance* over both.
- **Assume adaptation.** Model the strategy as a move in an adversarial game with decay, not a
  static formula.
- **Maintain the epistemic ledger.** Proven / evidenced / plausible / speculative — and never let
  something drift upward silently.

### 4.4 The honest summary

- **Logic** gives certainty, but only *relative to premises*, and is **provably incomplete** for any
  system rich enough to describe a world that includes itself.
- **Reasoning** extends logic to uncertainty; **probability is the unique coherent way** to do it,
  **causality** is required to move from observation to action, and **severity** is the only real
  measure of evidential support.
- **Mathematics** provides exact conditional truth and, at its highest form, reveals that different
  things share one structure. It cannot tell you whether its assumptions match reality.

The highest form of all three, applied to markets, converges on a single discipline: **be rigorous
about what you actually know, ruthless about how you could be fooled, and specific about the
mechanism that makes an edge exist.** Certainty is unavailable. Calibration is achievable, and it
is the whole game.
