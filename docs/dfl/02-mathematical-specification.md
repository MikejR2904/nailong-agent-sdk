# 2. Mathematical specification: Ranking-DFL + exact PCKP

This is a proposed memory-specific adaptation of ranking-based decision-focused learning. It is
*informed by* Mandi et al., "Decision-Focused Learning: Through the Lens of Learning to Rank", ICML 2022,
but it is not their algorithm and inherits none of their results; Section 2.9 states the differences.
The theorems below are elementary and are proved in full; they are about the three things the
implementation must keep apart.

| Quantity | Question it answers | Guaranteed by |
|---|---|---|
| **Learned-objective optimality** | Does the solver return the best feasible set *for the learned coefficients*? | Theorems 1-2, plus the solver certificate |
| **Ranking accuracy** | Does the learned set utility order candidate sets as measured outcomes do? | Measured on held-out pairs only; Theorem 4 says what it does and does not imply |
| **Downstream performance** | Does the chosen set make the agent succeed more often? | Nothing in this document. Only a measured experiment can show it. |

## 2.1 Setting and notation

Live episodes `V = {e_1..e_n}` with integer token costs `c_i >= 0` (the store gives `c_i >= 1`),
immediate prerequisite sets `D(i) ⊆ V` forming a DAG (`PckpProblem` rejects cycles), budget `B`,
and mandatory set `M ⊆ V` closed under prerequisites. The feasible family is

`F = { S ⊆ V : M ⊆ S,  D(i) ⊆ S for every i ∈ S,  Σ_{i∈S} c_i <= B }`

which is exactly the integer program `x_i <= x_j (j ∈ D(i))`, `x_i = 1 (i ∈ M)`, `Σ c_i x_i <= B`,
`x ∈ {0,1}^n`. For a task `q` the predictor outputs real utilities `û_i = f_θ(h_i, q, G)`; `h_i`
is the feature vector of schema `episode-utility-features-v1` (task, graph, provenance, dynamics and
outcome groups; `memory/utility_features.py`).

## 2.2 Quantised objective and its range restriction

PCKP items need `utility ∈ ℕ`. With scale `s` (default 10^6, the static path's scale) and ceiling `κ`
(default 1000):

`ũ_i = min(max(û_i, 0), κ)`,  `q_i = round(s · ũ_i)`,  `U_q(S) = Σ_{i∈S} q_i`,  `Û(S) = Σ_{i∈S} û_i`.

Restrictions, all enforced by `resolve_learned_coefficients` / `quantize_utilities`:

1. every `û_i` must be a finite real number, otherwise a coded error is raised and the documented
   fallback (static PCKP) is used;
2. negative values are clipped to 0 and values above `κ` to `κ`, and the clipped ids are recorded;
3. `round` is Python's round-half-to-even, so `|q_i - s ũ_i| <= 1/2`.

Clipping is not free: if a negative-utility episode is a prerequisite of positive-utility episodes, the
clipped objective values it at 0 where the unclipped objective would charge for it.

## 2.3 Theorem 1 (feasibility)

*Assumptions.* `ExactPckpSolver` satisfies its contract: its incumbent is initialised to the mandatory
closure and only replaced by sets it has checked to be prerequisite-closed and within budget.

*Claim.* If `status ∈ {OPTIMAL, BEST_EFFORT}`, the returned selection `Ŝ` lies in `F`. If
`status = INFEASIBLE_MANDATORY`, then `F = ∅`.

*Proof.* (i) In the branch-and-bound path the incumbent starts as `closure(mandatory)`, which is
prerequisite-closed by construction and has cost `<= B` because otherwise the solver returns
`INFEASIBLE_MANDATORY` before searching (`solvers.py:69`). A candidate replaces the incumbent only in the
`undecided is None` branch, with `selected_now` produced by `_propagate`, which adds every prerequisite of
a selected item and removes selections that conflict with exclusions, and only after
`selected_cost <= budget` has been checked. Every explored node starts from `mandatory`, so
`M ⊆ selection`. (ii) In the rooted-forest DP every state is an ancestor-closed union of subtrees
(`keep_left` is false for subtrees containing a mandatory root, forcing inclusion), pruned at
`total_cost > budget`. (iii) For `INFEASIBLE_MANDATORY`: every `S ∈ F` contains `M`, and costs are
non-negative, so `c(S) >= c(M) > B`, a contradiction; hence `F = ∅`. ∎

The proof relies on solver correctness. That correctness is not assumed blindly: it is checked against
exhaustive enumeration (`tests/foundations/test_optimization_solvers.py`,
`tests/memory/test_learned_pckp_integration.py::test_learned_selection_is_optimal_for_the_quantized_learned_objective`)
and every selection the benchmark produces is re-validated (`dfl_bench.selection.assert_feasible`).

## 2.4 Theorem 2 (learned-objective optimality, with quantisation error)

*Claim.* If `status = OPTIMAL`, then `Ŝ ∈ argmax_{S∈F} U_q(S)`. Moreover, for every `S ∈ F`,

`Û(Ŝ) >= Û(S) - n/s - 2E`,   where `E = max_{S'∈F} Σ_{i∈S'} |û_i - ũ_i|` (the clipping error).

If no value is clipped then `E = 0` and the loss relative to the unquantised learned objective is at most `n/s`.
If `status = BEST_EFFORT` with certificate gap `g` (in quantised units), the same holds with `n/s` replaced
by `n/s + g/s`.

*Proof.* The first sentence is the definition of `OPTIMAL` (a complete search; the certificate then has
`upper_bound = utility` and `optimality_gap = 0`). For the inequality, set `Ũ(S) = Σ ũ_i`. Because
`|q_i - s ũ_i| <= 1/2`, `|U_q(S)/s - Ũ(S)| <= |S|/(2s)`. For any `S ∈ F`:

`Ũ(Ŝ) >= U_q(Ŝ)/s - |Ŝ|/(2s) >= U_q(S)/s - |Ŝ|/(2s) >= Ũ(S) - |S|/(2s) - |Ŝ|/(2s) >= Ũ(S) - n/s`.

Finally `Û(S') = Ũ(S') + Σ_{i∈S'} (û_i - ũ_i)` and the second term is at least `-E` and at most `E`, so
`Û(Ŝ) >= Ũ(Ŝ) - E >= Ũ(S) - n/s - E >= Û(S) - n/s - 2E`. For `BEST_EFFORT`, `U_q(Ŝ) >= OPT_q - g` and the
chain starts from `OPT_q`. ∎

With `s = 10^6` and a few hundred episodes, `n/s < 10^-3` in utility units. The clipping term `E` is the
price of the integer-valued, non-negative solver interface; it is reported per decision
(`clipped_below_zero`, `clipped_above_ceiling`).

## 2.5 Theorem 3 (conditional downstream regret)

Let `Q(S, q)` be the expected downstream performance of retaining `S`. Let `Û` denote the objective the
solver actually optimised (`U_q/s`).

*Assumptions.* (A1) `sup_{S∈F} |Û(S) - Q(S,q)| <= ε`. (A2) `Û(Ŝ) >= max_{S∈F} Û(S) - δ`, with
`δ = (n + g)/s + 2E` from Theorem 2.

*Claim.* For `S† ∈ argmax_{S∈F} Q(S, q)`: `Q(S†, q) - Q(Ŝ, q) <= 2ε + δ`.

*Proof.* `Q(S†) <= Û(S†) + ε <= Û(Ŝ) + δ + ε <= Q(Ŝ) + ε + δ + ε`. ∎

The bound is tight: with `δ = 0`, take two feasible sets with `Û(S†) = Û(Ŝ) = 0`, `Q(S†) = ε` and
`Q(Ŝ) = -ε`; both satisfy (A1) and the regret is exactly `2ε`.

**The uniform-approximation assumption (A1) can fail for every additive model.** `Û` is additive by
construction, `Q` generally is not. Two minimal examples with feasible sets `{∅, {a}, {b}, {a,b}}`:

* *Redundancy*: `Q = 1` if `a ∈ S` or `b ∈ S`, else 0. The additive fit minimising the sup error is
  `û_a = û_b = 2/3`, giving errors `0, 1/3, 1/3, 1/3`: `ε* = 1/3`.
* *Complementarity*: `Q = 1` iff `{a, b} ⊆ S`. The best additive fit is `û = 1/3`, giving errors
  `0, 1/3, 1/3, 1/3`: `ε* = 1/3`.

(By convexity of the sup-error in `û` and the symmetry of the two items, the symmetric point is optimal.)
The benchmark's synthetic oracle is deliberately redundancy- and complementarity-shaped, so (A1) does not
hold there either; Theorem 3 is a conditional statement, not a property of the trained models.

## 2.6 Ranking-based DFL: data and loss

*Preferences.* For a task `q` and two feasible sets `S+, S-`, a preference is recorded only when
repeated, matched downstream trials give `Q̂(S+) > Q̂(S-)` that is statistically distinguishable
(two-proportion z test, level `α`, with add-half smoothing) and at least `min_effect`; otherwise the pair
is a tie and is omitted. The weight is `w = 1 - p`. Identical pairs merge (weights add), opposite pairs
cancel by net weight, and synthetic and measured outcomes can never share a dataset
(`dfl_bench.preferences`).

*Set utility.* `Û_θ(S, q) = Σ_{i∈S} f_θ(h_i, q, G)`.

*Loss.*

`L(θ) = Σ_{(S+,S-)} w · log(1 + exp(-(Û_θ(S+) - Û_θ(S-)) / τ)) + λ ‖θ‖²`

with explicit `τ > 0` and `λ >= 0` (`RankingTrainingConfig`). Because `Û` is additive, items in
`S+ ∩ S-` cancel exactly, so only the symmetric difference enters; `build_ranking_dataset` uses this.
The loss is the Bradley-Terry log-likelihood of the preference with logit `(Û(S+) - Û(S-))/τ`; it is
the logistic pairwise ranking loss (RankNet-style) applied to *sets* with an additive scorer.

## 2.7 Theorem 4 (ranking consistency, and why a finite sample does not give it)

*Claim (sufficient condition).* Suppose `Û` is order-consistent with `Q` on `F`: for all `S, S' ∈ F`,
`Q(S) > Q(S') ⇒ Û(S) > Û(S')`. Then every exact maximiser of `Û` over `F` maximises `Q` over `F`.
It suffices that consistency holds for pairs `(S†, S)` with `S†` a `Q`-maximiser and `Q(S) < Q(S†)`.

*Proof.* Let `Ŝ ∈ argmax_F Û` and suppose `Q(Ŝ) < Q(S†)`. Then `Û(S†) > Û(Ŝ)`, contradicting maximality of
`Ŝ`. ∎

*With the quantised, possibly best-effort solver:* the returned set is `δ`-optimal for `Û`, so it is
`Q`-optimal if `Û(S†) - Û(S) > δ` for every `S ∈ F` with `Q(S) < Q(S†)` (a margin condition).

*Why this does not follow from a good training result.* Pairwise accuracy of 1 on a sampled preference set `P`
establishes consistency only on the sets appearing in `P`. `|F|` is exponential in `n`, the solver
maximises `Û` over *all* of `F`, and its maximiser is generally outside the sampled sets: the optimisation
selects precisely where model errors are largest and favourable (the optimiser's curse). Further, the
preferences themselves come from noisy trials and are only retained when significant, so even on `P` the
ordering is a statistical statement. A uniform-convergence argument over `F` would be needed for a
guarantee and is not provided. The on-policy rounds in the training procedure (learned-policy candidates are
added to the pool and the model is refit) reduce, but do not remove, this shift.

## 2.8 Theorem 5 (convexity of the linear baseline)

For `Û_θ(S) = θᵀ φ(S)` with `φ(S) = Σ_{i∈S} h_i`, let `Δ_p = φ(S_p+) - φ(S_p-)` and `m_p = θᵀΔ_p`:

`L(θ) = Σ_p w_p softplus(-m_p/τ) + λ‖θ‖²`.

With `softplus' = σ` (logistic) and `σ' (z) = σ(z) σ(-z)`:

* **Gradient** `∇L(θ) = - Σ_p (w_p/τ) σ(-m_p/τ) Δ_p + 2λθ`.
* **Hessian** `H(θ) = Σ_p (w_p/τ²) σ(m_p/τ) σ(-m_p/τ) Δ_pΔ_pᵀ + 2λ I`.

Both are implemented (`ranking_gradient`, `linear_ranking_hessian`) and checked against finite differences
(`tests/memory/test_ranking_loss.py`).

*Claims.*
1. `L` is convex: each term of the Hessian is positive-semidefinite (`w_p > 0`, `σσ > 0`).
2. If `λ > 0`, `L` is `2λ`-strongly convex (`H ⪰ 2λI`), `L(θ) >= λ‖θ‖²` is coercive, hence a minimiser exists
   and is **unique**; Newton's method with backtracking line search converges (the gradient is Lipschitz with
   constant at most `Σ w‖Δ‖²/(4τ²) + 2λ`, since `σσ <= 1/4`).
3. If `λ = 0`: `H` is positive-definite everywhere iff the `Δ_p` span `ℝ^d`; but a minimiser exists only if
   the data are not strictly linearly separable (if some `θ` has `θᵀΔ_p > 0` for all `p`, then scaling `θ`
   drives `L → 0` without being attained). The trainer reports a non-positive-definite Hessian as
   `UTILITY_TRAINING_NOT_STRONGLY_CONVEX`.

*Proof.* Items 1-2 follow from the displayed formulas. For coercivity `L(θ) >= λ‖θ‖² ` since the data term is
non-negative; a continuous coercive function attains its minimum; strong convexity gives uniqueness. For 3,
`vᵀHv = Σ c_p (vᵀΔ_p)²` with `c_p > 0`, which vanishes only if `v ⟂ Δ_p` for all `p`; separability gives a
recession direction of `L`. ∎

**No claim for the MLP.** `L` composed with a tanh network is non-convex; the trainer is deterministic for a
fixed seed and keeps the best validation loss, but no global-convergence statement is made or implied.

## 2.9 Differences from the ICML 2022 paper

The abstract of Mandi et al. (checked for this work) frames decision-focused learning as learning to rank
feasible points and proposes pointwise, pairwise and listwise losses over a subset of solutions, with
competitive empirical regret. The following are different here, and are this work's own design (the
paper's full text was not re-read for this document, so statements about its internals are not made):

| Aspect | Original (per its abstract) | Here |
|---|---|---|
| What is predicted | cost/objective coefficients of a given optimisation problem | per-episode utilities in an additive set objective |
| Supervision | feasible points ranked under a learning-to-rank view of DFL | measured *set-level* downstream outcomes of whole retention sets; no per-item truth |
| Outcome noise | not the focus | repeated stochastic trials, significance filter, ties omitted, confidence weights |
| Candidate sets | a subset of solutions | six generators (static PCKP, greedy PASK, random closed, perturbed PCKP, targeted edits, current learned policy) |
| Constraints | those of the studied problems | prerequisite closure, mandatory retention, token budget, solver certificate |
| Theory | none claimed in the abstract | Theorems 1-5 above, all elementary and specific to this setup |

## 2.10 Limits of the additive model

* It cannot represent redundancy or complementarity between episodes (the `ε* = 1/3` examples).
  Marginal-diversity scoring (as in the existing `PASK` strategy) is submodular, not additive, and is
  excluded from exact PCKP for this reason; combining learning with it would give up exactness.
* Utilities are clipped to a non-negative integer range, see 2.2.
* The model ranks sets only through feature sums; interactions have to be in the features (the graph group
  encodes a few) or in the MLP's per-item nonlinearity, not between items.
* Features are limited to what is observable at selection time. "Unresolved issues" and "evidence
  importance" do not exist as fields in the episode store and are not used.
