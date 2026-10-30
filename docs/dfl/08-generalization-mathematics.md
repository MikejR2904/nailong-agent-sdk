# Generalized objectives, calibration and robustness: mathematical analysis

Extends [02-mathematical-specification.md](02-mathematical-specification.md). Labels: **[P]** proved
here (or a direct consequence of a cited standard fact), **[A]** assumption, **[E]** empirical (see
the results documents; never proved), **[C]** conjecture. Nothing below claims that a better
objective improves downstream task success; that is an empirical question answered, if at all, in
[09-generalization-evaluation.md](09-generalization-evaluation.md).

## 0. Setting

An episode graph has live episodes `V`, token costs `c_i`, prerequisites `D(i)`, mandatory set `M`
and budget `B`. The feasible selections are
`F = {x ∈ {0,1}^n : Σ c_i x_i ≤ B, x_i ≤ x_j ∀ j ∈ D(i), x_i = 1 ∀ i ∈ M}`.
A model gives each episode a utility `û_i = f_θ(h_i)` from a feature vector `h_i` and scores a set
additively, `Û_θ(S) = Σ_{i∈S} û_i`. For a linear model `f_θ(h) = θᵀh`, so `Û_θ(S) = θᵀΦ(S)` with
`Φ(S) = Σ_{i∈S} h_i`. The solver is never differentiated through.

## 1. Pairwise regret-weighted loss

For a preference `(S⁺, S⁻)` with margin `m = Û(S⁺) − Û(S⁻)`, confidence `c`, effect `ΔQ`:

`ℓ = c |ΔQ|^γ softplus(−m/τ)`, `L = Σ ℓ + λ‖θ‖²`.

**Gradient [P].** `d softplus(z)/dz = σ(z)`, so `∂ℓ/∂m = −c|ΔQ|^γ σ(−m/τ)/τ` and
`∇_θ L = Σ ∂ℓ/∂m · (∇Û(S⁺) − ∇Û(S⁻)) + 2λθ`. For an additive model the items common to `S⁺` and
`S⁻` appear in both sums and cancel, so only the symmetric difference enters the margin and the
gradient (`build_ranking_dataset` and `build_objective_dataset` drop the common items).

**Stability [P].** `softplus(z) = max(z,0) + log1p(e^{−|z|})` and `σ` is evaluated on the branch
that never exponentiates a large positive number, so both are finite for any finite margin.

**Weight cap [design].** `effect_weights` clips weights at a multiple of their median and rescales to
mean one. This bounds the share of the loss any single comparison can carry; it is a robustness
heuristic, not a statistical estimator, and is compared against the unnormalised variant in the
experiments.

## 2. Listwise loss

For a task with candidates `S_1..S_K`, qualities `Q_j`, define `p = softmax(Q/τ_Q)` and
`p̂ = softmax(Û/τ_U)`. `L = −Σ_j p_j log p̂_j`.

**Gradient [P].** With `z_j = Û_j/τ_U`, `∂(−Σ p_j z_j + LSE(z))/∂z_j = p̂_j − p_j` (using `Σ p_j = 1`),
hence `∂L/∂Û_j = (p̂_j − p_j)/τ_U` and `∇_θ L = Σ_j (p̂_j − p_j)/τ_U ∇Û_j`. `log_softmax`
subtracts the maximum before exponentiating, so it is finite for logits of any magnitude.

**Shift invariance [P].** Adding the same constant to every `Û_j` leaves `p̂` unchanged. For an
additive model, an item shared by every candidate shifts every `Û_j` equally, so it can be dropped
(done in `build_objective_dataset`).

**Pool-size bias [P].** `L ≥ H(p)` with equality at `p̂ = p`, and `H(p) ≤ log K`. A pool of `K`
candidates can therefore contribute up to `log K` of irreducible loss; weighting a task by
`1/log K` makes the achievable loss scale comparable across pool sizes.

**Ties [design].** A pool whose measured qualities differ by less than the preference
policy's minimum effect is omitted rather than given a (near-uniform but noisy) target. Exactly tied
qualities give a uniform target and a zero gradient at a uniform prediction.

## 3. Set-level regression

`L = Σ w (Û(S) − Q)² + λ‖θ‖²`, gradient `2w(Û − Q)∇Û`. Regression uses the absolute level of every
candidate's mean outcome. Ranking uses only comparisons whose difference is statistically
distinguishable, and the pairwise form discards the level entirely (Section 6).

## 4. Convexity and uniqueness (linear models)

**Proposition 1 [P].** For a linear model the pairwise, listwise and regression objectives are
convex in `θ`, and strongly convex with modulus `2λ` when `λ > 0`; the minimiser then exists and is
unique.

*Proof.* `Û(S) = θᵀΦ(S)` is linear in `θ`. `softplus` is convex, so `softplus(−θᵀΔΦ/τ)` is convex.
`LSE` is convex and `−Σ p_j θᵀΦ_j/τ` is linear, so the listwise term is convex. Squares of linear
maps are convex. Non-negative weights preserve convexity and `λ‖θ‖²` adds `2λI` to the Hessian.
A strongly convex, continuous function is coercive, so a minimiser exists, and it is unique. ∎

**Hessians [P].** Pairwise: `Σ w σ(m/τ)σ(−m/τ)τ⁻² ΔΦΔΦᵀ + 2λI`. Listwise:
`τ⁻² Σ_q Cov_{p̂}(Φ) + 2λI`, the covariance of the candidate features under `p̂`. Both are positive
semidefinite; without regularisation they are positive definite only if the difference vectors span
the feature space. With `λ = 0` and linearly separable preferences the pairwise infimum is not
attained (weights diverge); the `λ > 0` used throughout rules that out.

**Optimiser note.** `train_objective` runs full-batch Adam for both model types. For linear models
the problem is convex, but Adam with early stopping does not return the exact minimiser; the report
states `gradient_norm` and `converged` so the distance from stationarity is visible [E].

## 5. Why MLP (and GNN) training has no global guarantee

**Proposition 2 [P, argument].** For an MLP with `h ≥ 2` hidden units the objective is invariant
under permuting hidden units, so any minimiser has other minimisers with the same function but
different parameters. If the loss were convex its minimiser set would be convex, so the average of a
minimiser and its permutation would also minimise. That average is, for generic weights, a different
network (averaged units) with a different function and generally higher loss, so convexity fails. The
loss is therefore non-convex; gradient methods find stationary points, and no polynomial guarantee of
global optimality exists in general. The same holds for message-passing models. The reported MLP
results are early-stopped Adam iterates and say nothing about the global optimum.

## 6. Identifiability of the offset, and why it matters to PCKP

**Proposition 3 [P].** Let a linear model contain a constant feature (the `bias` column) with
coefficient `b`. If every training pair has `|S⁺∖S⁻| = |S⁻∖S⁺|`, the pairwise loss is invariant to
`b`.

*Proof.* The margin contains `b(|S⁺∖S⁻| − |S⁻∖S⁺|) = 0`. ∎

With the L2 penalty the minimiser sets `b = 0`. The PCKP optimum is not invariant to the offset: adding `δ` to every
utility changes a selection `S`'s objective by `δ|S|`, so it trades the number of retained episodes
against their quality. A pairwise model can therefore rank correctly and still produce selections
that fill the budget poorly. Listwise pools with different cardinalities, and regression, identify
the offset. **[E]** The measured under-spend on the unseen triage family is consistent with this but
was not attributed to it by experiment.

**Floor semantics [P].** `QuantizationPolicy.floor = f` replaces `q_i` by `max(f, q_i)`. If all
predictions are `≤ 0`, every item is worth `f` and the solver maximises the number of retained
episodes subject to closure (it prefers cheap episodes). This is an explicit rule, not a neutral
default.

## 7. PCKP feasibility, quantisation and clipping

**Feasibility and certificate [P by the solver contract].** The solver returns `x ∈ F` (or reports
infeasible mandatory retention). `OPTIMAL` means `x` maximises `Σ q_i x_i` over `F`; `BEST_EFFORT`
means only that `x` is feasible with the reported gap. No learned component can alter `F`.

**Quantisation error [P].** Write `Q_i = max(f, round(s·clip(û_i, 0, C)))`. For
`û_i ∈ [0, C]` and `f = 0`, `|Q_i/s − û_i| ≤ 1/(2s)`. Let `e_i = û_i − clip(û_i)` and
`E(x) = Σ e_i x_i` (`e_i ≤ 0` below the clip, `≥ 0` above the ceiling). If `x̂` is optimal for `Q` and
`x*` for `Û`, then

`Û(x̂) ≥ Û(x*) − n/s − E(x*) + E(x̂)`.

*Proof.* `Û(x̂) = Σ(Q_i/s)x̂_i + (rounding ≤ n/(2s)) + E(x̂)` and `Q(x̂) ≥ Q(x*)`, and
`Σ(Q_i/s)x*_i ≥ Û(x*) − E(x*) − n/(2s)`. ∎ With no clipping the loss is at most `n/s`: about
`3·10⁻⁵` for 30 episodes at the default `s = 10⁶`. Clipping can cost an unbounded amount; the audit
record lists `clipped_below_zero` for this reason.

## 8. Conditional downstream regret

**Proposition 4 [P].** Suppose over the feasible sets `Q(S) = g(U*(S)) + r(S)` with `g`
`L`-Lipschitz and non-decreasing, `|r| ≤ ε`, and a model satisfies `sup_{S∈F} |Û(S) − U*(S)| ≤ δ`.
If the solver is exact for `Û`, the selected `Ŝ` has regret `Q(S_opt) − Q(Ŝ) ≤ 2Lδ + 2ε`.

*Proof.* Let `S° = argmax_F U*`. Exactness gives `Û(Ŝ) ≥ Û(S°)`, so `U*(Ŝ) ≥ U*(S°) − 2δ`.
`Q(S_opt) ≤ g(U*(S°)) + ε` and `Q(Ŝ) ≥ g(U*(S°) − 2δ) − ε ≥ g(U*(S°)) − 2Lδ − ε`. ∎

`δ` is a uniform error over feasible *sets* and is at most `n·η` for a per-item error `η`. A ranking
loss controls only differences on the sampled pairs, so it does not bound `δ`. The bound also
needs `ε` small, which is the additivity assumption of Section 9. The proposition is conditional on
assumptions that the experiments cannot verify for real agents; exact PCKP optimality is not
downstream optimality.

## 9. Additive misspecification

**Example [P].** Items `a, b, c` cost 1, budget 2. `Q(S) = 1·[a∈S ∨ b∈S] + 0.9·[c∈S]` (`a` and `b`
are redundant). The unique optimum is `{a,c}` (1.9). Training only on singleton comparisons,
`{a} ≻ {c}` and `{b} ≻ {c}`, is satisfied with zero loss by `u_a = u_b = 1`, `u_c = 0.9`. The exact
PCKP optimum for those utilities is `{a,b}` (2.0 vs 1.9), whose true quality is 1: regret 0.9 with
zero training loss and an exact solver. The failure comes from additivity plus a candidate pool
that never contains the discriminating set, so it can be reduced by richer pools (including the
decision-boundary candidates of Section 3.5 of the task) but not removed by a better loss.

## 10. Ranking consistency and its limits

For a well-specified Bradley–Terry model `P(S⁺ ≻ S⁻ | q) = σ((U*(S⁺) − U*(S⁻))/τ)` with `U*` in the model class, the pairwise logistic loss
is minimised in the population by a model with the same pairwise differences [P, standard
calibration of convex margin losses]. Consequences: (i) it recovers `U*` only up to the identifiability limits of Section 6;
(ii) it says nothing about sets never compared; (iii) misspecification (Section 9) and finite
pools break it. Preference construction also discards pairs whose difference is not significant, so
ranking uses less of the outcome information than regression does [design].

## 11. Group-DRO

`min_θ max_{q∈Δ} Σ_e q_e L_e(θ)` equals `min_θ max_e L_e(θ)` because the maximum of a linear function
over the simplex is attained at a vertex. `dro_update` is exponentiated-gradient ascent on `q`:
`q_e ← q_e exp(η L_e)/Z`. For convex `L_e` and a bounded step the averaged iterates converge at
`O(1/√T)` to the saddle point [cited, Nemirovski et al.]; for non-convex models there is no such
guarantee.

**What it does not give [P].** For any distribution that is a mixture of the training environments,
`Σ q_e L_e ≤ max_e L_e`, so the worst training-environment loss bounds mixtures. It gives no bound
for an environment outside the convex hull of the training ones: two environments with opposite
optimal parameters (the relevance feature helps in one and hurts in the other) admit a model that
is mediocre on both and arbitrarily bad on a third. With six family labels, the hull is a
small subset of possible shifts. **[E]** DRO also overfits when a group has few examples.

## 12. Calibration and the solver guarantee

`mix_utilities(learned, static, α) = α·û/max|û| + (1−α)·u_s/max|u_s|`.

- Positive rescaling never changes the argmax of the real-valued objective (**[P]**) but changes
  the quantised one by at most the error of Section 7.
- Mixing creates a new objective. The solver's certificate holds for the quantised mixed objective,
  not for either component, and `α` is a hyperparameter with no guarantee about downstream quality.
- Centring is deliberately avoided: a constant offset changes the PCKP optimum (Section 6).
- Fallback sets `α = 0`. The selection is then the static one scaled by a positive constant, so it
  is the static optimum up to quantisation ties; bit-identical selection is not guaranteed in
  general and is only checked on the test instances.
- Every fallback is logged with its evidence (`shift`, `clip_fraction`, `dispersion`,
  `disagreement`) and reasons in `dossier["learned_ranking"]["calibration"]`.

## 13. Claim status

| Claim | Status |
|---|---|
| Gradients of the three objectives, finite-difference checked | [P] + unit tests |
| Convexity, uniqueness with `λ > 0` (linear) | [P] |
| No global optimality for MLP/GNN | [P] argument |
| Offset unidentifiable under equal-size pairs; PCKP is offset-sensitive | [P] |
| Quantisation/clipping bound | [P] |
| Regret bound | [P], conditional on [A] |
| Additive misspecification can give zero loss and regret 0.9 | [P] |
| DRO bounds mixtures of training families only | [P] |
| Any objective generalises better downstream | [C]; see 09 |
