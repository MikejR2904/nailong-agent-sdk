# 4. Comparison with related work

**Verification status.** The only source checked during this work is the abstract of Mandi et al.
(ICML 2022, <https://proceedings.mlr.press/v162/mandi22a.html>). Every other reference below was
written from memory and has *not* been re-verified; check titles, venues and claims before citing.
Statements about what a work does are limited to its widely known core idea.

## 4.1 Decision-focused learning

| Work | Core idea | Relation to this design |
|---|---|---|
| Mandi et al., ICML 2022 | DFL viewed as learning to rank feasible points; pointwise, pairwise, listwise losses over a subset of solutions; competitive regret; runtime controlled by the subset (per the abstract) | Direct inspiration for using a *ranking* loss and a *candidate-set* view. Here the ranked objects are whole retention sets, the supervision is measured set-level outcomes, and the objective is additive over episodes. No claim is made that their guarantees transfer. |
| Wilder, Dilkina, Tambe (AAAI 2019), "Melding the Data-Decisions Pipeline" | Differentiate through a combinatorial optimiser's relaxation to train a predictor for decision quality | We avoid differentiating through the solver; the solver stays exact and unmodified. |
| Elmachtoub, Grigas (Management Science 2022), "Smart Predict, then Optimize" | SPO loss and convex surrogate SPO+ with regret guarantees under linear objectives | Needs the true cost vector at training time. We do not observe per-episode true utility, only outcomes of sets. |
| Amos, Kolter (ICML 2017), OptNet | Differentiable quadratic-program layers | Not applicable to a discrete knapsack with closure constraints without relaxation. |
| Berthet et al. (NeurIPS 2020); Vlastelica et al. (ICLR 2020) | Perturbed optimisers; black-box combinatorial solver differentiation | Alternative routes to end-to-end gradients. The perturbed-utility candidate generator here borrows the idea of perturbing coefficients, but only to create diverse feasible candidates, not to train. |
| Mulamba et al. (IJCAI 2021) | Contrastive losses and solution caching for predict-and-optimise | The closest relative of our candidate pool; the pool here is built from deliberate strategies and is validated for feasibility. |
| Mandi et al., JAIR 2024 survey | Foundations, state of the art and benchmark of DFL | Context; its benchmark problems do not include prerequisite-closed retention. |

## 4.2 Learning from comparisons

Training an additive scorer so that `Û(S+) > Û(S-)` with a logistic link is the Bradley-Terry model
(Bradley, Terry 1952) over sets, and a RankNet-style pairwise loss (Burges et al. 2005). The closest
analogue in RL is learning a reward as a *sum over steps* from preferences between trajectory segments
(Christiano et al. 2017). Our additive set utility plays the role of that summed reward. As there, the
learned objective is only as good as the comparisons and as the additivity assumption.

## 4.3 Agent memory

| Work | Retention / retrieval policy | Relation |
|---|---|---|
| MemGPT (Packer et al. 2023) | Virtual context management with paging decided by the model | Retention decided by an LLM. Here, retention is a verified optimisation with a certificate. |
| Generative Agents (Park et al. 2023) | Retrieval scores = recency + importance + relevance, with hand-set weights | The static utility here has the same shape (hand-set weights over relevance, recency, centrality, provenance, frequency). The new work learns those coefficients from outcomes and adds graph/outcome features. |
| Reflexion (Shinn et al. 2023); Voyager (Wang et al. 2023) | Episodic reflections / skill libraries kept for later tasks | They choose *what to write*; this work chooses *what to keep under a token budget with dependencies*. |
| Mem0, A-MEM (2025) | Structured, updatable long-term memory stores | Storage designs; none, to my knowledge, couples retention to an exact constrained optimiser with provenance closure. This is a claim from memory and should be checked. |

## 4.4 Constrained knapsack

The solver cites the precedence-constrained knapsack literature in `foundations/optimization/solvers.py`
(<https://dlnext.acm.org/doi/10.5555/3182683.3183062>, <https://www.sciencedirect.com/science/article/abs/pii/S0377221706010642>).
This work changes only the objective coefficients; the solver, its certificate and its complexity are
unchanged.

## 4.5 What is and is not new here

*Engineering contribution (implemented):* an opt-in learned-utility path that feeds the existing exact
PCKP solver, with versioned schemas, digest-checked artifacts, an auditable decision record, and a
documented fallback.

*Methodological contribution (implemented, evaluated only on a synthetic oracle):* constructing
preferences for a set-level additive objective from repeated matched trials with significance filtering,
leakage-safe splits, and on-policy candidate rounds.

*Not established:* that learned coefficients improve real downstream agent performance. That requires the
measured experiment described in `05-experimental-report.md` §5.6.
