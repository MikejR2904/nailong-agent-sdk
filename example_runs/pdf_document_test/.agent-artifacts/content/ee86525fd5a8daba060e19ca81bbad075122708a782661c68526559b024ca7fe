# Summary: "Fast Inference from Transformers via Speculative Decoding"

**Authors:** Yaniv Leviathan, Matan Kalman, Yossi Matias (Google Research)
**Venue:** Proceedings of the 40th International Conference on Machine Learning (ICML), PMLR 202, 2023. arXiv:2211.17192v2 [cs.LG], 18 May 2023.

## Document length and reading confirmation

The document is **13 pages long** (total_pages = 13). I fetched and read **all 13 pages** in this session via `web_fetch`, in three consecutive calls covering pages 1–4, pages 5–8, and pages 9–13 (the final call returned `next_page: null`, confirming the end of the document). No page was skipped, and no content below relies on prior training knowledge of the paper.

---

## 1. Problem and motivation

Inference from large autoregressive models such as Transformers is slow: decoding K tokens requires K **serial** runs of the model. A single decode step from a large model is much slower than one from a smaller model, and these steps cannot be parallelized in the standard formulation. Prior work either reduces cost uniformly for all inputs (distillation, sparsification, quantization, architecture changes) or uses adaptive computation (early exits, attending to subsets of inputs). These usually require architecture changes, retraining, and they change the model's output distribution.

The paper's key observations:
1. Hard language-modeling tasks often contain easier subtasks that a more efficient model can approximate well.
2. Inference from large models is often bottlenecked not on arithmetic operations but on **memory bandwidth and communication**, so extra compute resources may be available.
3. Therefore, increasing **concurrency** (via speculative execution) is a complementary approach to adaptive computation.

The result: acceleration **without** changing model architectures, training procedures, or the output distribution. Demonstrated on T5-XXL with a 2X–3X walltime speedup versus the T5X implementation, with identical outputs.

## 2. Speculative decoding: the mechanism

### 2.1 Setup and overview

Let **Mp** be the target model (the one we want to accelerate), with distribution `p(x_t | x_<t)` for a prefix `x_<t`. Let **Mq** be a more efficient approximation model for the same task, with distribution `q(x_t | x_<t)`. (The paper writes `p(x)` and `q(x)` when the prefix is clear from context.)

The core idea has three steps:
1. Use the efficient model **Mq** to generate **γ ∈ Z+** completions (guesses) autoregressively.
2. Use the target model **Mp** to evaluate all guesses and their probabilities **in parallel**, accepting those that can lead to an identical distribution.
3. Sample an additional token from an **adjusted distribution** to fix the first rejected guess, or to add one more token if all guesses were accepted.

Each parallel run of Mp therefore produces **at least one** new token (so the number of serial runs of Mp can never exceed that of standard autoregressive decoding, even in the worst case), but can produce up to **γ + 1** tokens depending on how well Mq approximates Mp.

### 2.2 Standardized sampling

Sampling methods (argmax, top-k, nucleus, temperature) are treated differently at the logits level in popular implementations, but all can be cast as standard sampling from an adjusted probability distribution. For example, argmax sampling is equivalent to zeroing out non-max elements and normalizing. The paper therefore assumes `p(x)` and `q(x)` are the distributions from Mp and Mq respectively, **adjusted for the sampling method**.

### 2.3 Speculative sampling (the novel sampling method)

To sample `x ∼ p(x)`, the method instead samples `x ∼ q(x)` and:
- **keeps** the sample if `q(x) ≤ p(x)`;
- if `q(x) > p(x)`, **rejects** the sample with probability `1 − p(x)/q(x)` and re-samples `x` from the adjusted distribution

  `p'(x) = norm(max(0, p(x) − q(x)))`.

It can be shown (Appendix A.1) that for any distributions `p(x)` and `q(x)`, a token `x` sampled this way indeed satisfies `x ∼ p(x)` — i.e. the output distribution is exactly that of the target model.

**Concrete two-token illustration:** given `q(x)` from running Mq on a prefix, sample `x1 ∼ q(x)`. Then compute `p(x)` by running Mp on the prefix, while in parallel speculatively computing the distribution of the next token `x2` by running Mp on `prefix + [x1]`. Once both complete: if `x1` is rejected, discard the computation of `x2` and re-sample `x1` from the adjusted distribution; if `x1` is accepted, keep both tokens.

**Algorithm 1 (SpeculativeDecodingStep)** generalizes this to sample between 1 and γ + 1 tokens at once:
- Inputs: `Mp, Mq, prefix`.
- Sample γ guesses `x1, …, xγ` from Mq autoregressively: for `i = 1..γ`, `qi(x) ← Mq(prefix + [x1,…,x_{i−1}])`, `xi ∼ qi(x)`.
- Run Mp in parallel: `p1(x), …, p_{γ+1}(x) ← Mp(prefix), …, Mp(prefix + [x1,…,xγ])`.
- Determine the number of accepted guesses `n`: draw `r1,…,rγ ∼ U(0,1)` and set

  `n ← min({i − 1 | 1 ≤ i ≤ γ, ri > pi(x)/qi(x)} ∪ {γ})`.
- Adjust the distribution from Mp if needed: `p'(x) ← p_{n+1}(x)`; if `n < γ`, then `p'(x) ← norm(max(0, p_{n+1}(x) − q_{n+1}(x)))`.
- Return one token from Mp and n tokens from Mq: sample `t ∼ p'(x)` and return `prefix + [x1,…,xn, t]`.

## 3. Analysis

### 3.1 Number of generated tokens

**Definition 3.1:** The **acceptance rate** `β_{x<t}` given prefix `x_<t` is the probability of accepting `xt ∼ q(xt | x_<t)` by speculative sampling. `E(β)` measures how well Mq approximates Mp.

Assuming the βs are i.i.d. with `α = E(β)`, the number of tokens produced by one run of Algorithm 1 is a **capped geometric variable** with success probability `1 − α` and cap `γ + 1`, giving:

**Equation (1):** `E(# generated tokens) = (1 − α^{γ+1}) / (1 − α)`

(Figure 2 plots this expected token count as a function of α for various γ.)

### 3.2 Calculating α

**Definition 3.2:** `D_LK(p, q) = Σ_x |p(x) − M(x)| = Σ_x |q(x) − M(x)|`, where `M(x) = (p(x) + q(x))/2`.

**Lemma 3.3:** `D_LK(p, q) = 1 − Σ_x min(p(x), q(x))`.

**Corollary 3.4:** `D_LK(p, q)` is a symmetric divergence in [0,1]; it is 0 iff `p = q`, and 1 iff `p` and `q` have disjoint support.

**Theorem 3.5:** `β = 1 − D_LK(p, q)`.

**Corollary 3.6:** `α = 1 − E(D_LK(p, q)) = E(min(p, q))`.

### 3.3 Walltime improvement

**Definition 3.7:** The **cost coefficient** `c` is the ratio between the time for a single run of Mq and a single run of Mp. Unlike α (an intrinsic property of models and task), `c` depends on hardware and implementation. In the experiments, with Mq typically a couple of orders of magnitude smaller than Mp, `c` was always less than 0.05 and often negligibly close to 0.

**Theorem 3.8:** The expected improvement factor in total walltime by Algorithm 1 is

`(1 − α^{γ+1}) / ((1 − α)(γc + 1))`.

*Proof sketch:* a single step of Mp costs `T`; each run of Algorithm 1 costs `Tcγ + T` (γ runs of Mq plus one run of Mp) and produces `(1 − α^{γ+1})/(1 − α)` tokens on average, so the expected cost per token is `((cγ + 1)(1 − α) / (1 − α^{γ+1})) T`, versus `T` for standard decoding.

**Corollary 3.9:** If `α > c`, there exists a γ giving an improvement, and the improvement factor is at least `(1 + α)/(1 + c)` (evaluating Theorem 3.8 at γ = 1).

### 3.4 Number of arithmetic operations

Algorithm 1 does γ + 1 runs of Mp in parallel, so concurrent arithmetic operations grow by a factor of γ + 1. When a guess is accepted, the increased concurrency is "free" and total operations are not increased; when a guess is rejected, computation is wasted.

**Definition 3.10:** `ĉ` is the ratio of arithmetic operations per token of Mq to that of Mp.

**Theorem 3.11:** The expected factor of increase in total operations of Algorithm 1 is

`((1 − α)(γĉ + γ + 1)) / (1 − α^{γ+1})`.

If α is low, the increase in arithmetic operations is high, and vice versa. For Transformer decoders, the total arithmetic operations of Algorithm 1 (excluding Mq runs) can be bounded above by a single run of a same-size Transformer **encoder**. Importantly, unlike arithmetic operations, the total number of **memory accesses** can go **down**: the target model's weights and KV cache can be read once per execution of Algorithm 1, so memory accesses for reading them shrink by a factor of `(1 − α^{γ+1})/(1 − α)`.

### 3.5 Choosing γ

Given `c` and `α` (and enough compute resources), the optimal γ maximizes the walltime improvement of Theorem 3.8; since γ is an integer it can be found numerically (Figure 3). Table 1 illustrates the trade-off between speed and total arithmetic operations for various α and γ (assuming `c = ĉ = 0`), e.g. α = 0.9, γ = 10 gives 1.60X operations and 6.86X speed; α = 0.8, γ = 5 gives 1.63X operations and 3.69X speed.

Because the βs are not constant, further improvement could come from predicting β and varying γ during the run. With an oracle for γ, `E(# generated tokens) = 1/(1 − α)`; for typical `c` and `α` and unbounded compute, the enhanced walltime improvement could be up to ~60% higher than with a fixed γ. This is left for future work.

### 3.6 Approximation models

Speculative sampling (and hence speculative decoding) guarantees an identical output distribution for **any** choice of Mq without restriction (Appendix A.1). In experiments, mostly existing off-the-shelf smaller Transformers were used, of the same architecture as Mp and with the same probability standardization. Choosing Mq around two orders of magnitude smaller than Mp usually performed best, balancing α and c.

**Negligible-cost models** (`c ≈ 0`) give an expected walltime improvement of `(1 − α^{γ+1})/(1 − α)`, bounded above by `1/(1 − α)` (approached for large γ). Examples:
- **n-gram models** (evaluation is a table lookup). Empirically these yield non-zero α: for EnDe translation with Mp = T5-XXL 11B and Mq = a trivial bigram model, α ≈ 0.2, giving a 1.25X speed improvement with γ = 3.
- **Copy-from-context heuristics** for tasks where long sequences repeat (summarization, chat-like interfaces), which may yield high α and are simple to deploy.
- **Non-autoregressive models** (e.g. Stern et al., 2018): instead of the autoregressive loop, call the non-autoregressive model once.
- A **random-token** approximation model, which guarantees some (very small) improvement for all Mp (mostly of theoretical interest).

## 4. Experiments

### 4.1 Empirical walltime improvement

**Setup:** standard encoder-decoder T5 version 1.1 on two tasks: (1) English→German translation fine-tuned on WMT EnDe, and (2) text summarization fine-tuned on CNN/DM. Mp = T5-XXL (11B); Mq tested as T5-large (800M), T5-base (250M), T5-small (77M), using existing checkpoints. Walltime measured with batch size 1 on a single TPU-v4, for argmax sampling (temp = 0) and standard sampling (temp = 1).

**Results (Table 2):** T5-small (77M) gives the best balance of c and α and the highest speedup. α increases with approximation-model size. α and speedup are higher for argmax sampling. Speedups: 2.6X (temp=1) and 3.4X (temp=0) on translation; 2.3X (temp=1) and 3.1X (temp=0) on summarization. These match theoretical predictions well, with variance from implementation details (Appendix A.3).

### 4.2 Empirical α values

Measured by evaluating the expectation from Corollary 3.6 on 10K tokens generated by Mp (Table 3):
- **GPT-like (97M)** decoder-only model on unconditional generation (lm1b, Bert tokenization, 8k tokens); Mq = a 6M-parameter Transformer decoder (dim 256, FF 1024, 2 layers, 4 heads), plus unigram and bigram models. α ≈ 0.88–0.89 for the 6M model; 0.03–0.05 for unigram/bigram.
- **LaMDA (137B)** on a dialog task; Mq = LaMDA 8B, 2B, 100M. α ≈ 0.57–0.75.
- **T5-XXL (11B)** on EnDe and CNN/DM; α ≈ 0.53–0.82 for T5-small/base/large, 0.07–0.23 for unigram/bigram.

Observations: approximation models a couple of orders of magnitude smaller tend to produce α between 0.5 and 0.9; the sharper the adjusted distribution, the higher the α; even trivial unigram/bigram approximations yield non-negligible α (e.g. bigram on EnDe: α = 0.2, and since c = 0, a 1.25X speedup).

## 5. Related work

Efficiency of inference from large models has been studied extensively. General approaches: distillation (Hinton et al., 2015), sparsification (Jaszczur et al., 2021), quantization (Hubara et al., 2016), architecture modification (So et al., 2021; Shazeer, 2019). Closer are adaptive computation methods (Han et al., 2021), e.g. attending to subsets of inputs (Sukhbaatar et al., 2019) and early exits (Schuster et al., 2021; Scardapane et al., 2020; Bapna et al., 2020; Elbayad et al., 2019; Schwartz et al., 2020). Notably, "Wisdom of Committees" (Schwartz et al., 2020) leverages off-the-shelf smaller models but uses a heuristic to decide when to stop, losing the guarantee of identical outputs. Adaptive computation methods usually require architecture/training changes and change outputs.

Two prior methods use speculative execution for decoding: **Blockwise Parallel Decoding** (Stern et al., 2018) decodes several tokens in parallel but only supports greedy decoding, requires training a custom model, and focuses on downstream quality rather than identical outputs. **Shallow Aggressive Decoding (SAD)** (Sun et al., 2021) also decodes several tokens in parallel but only supports copying the input to the output, making it suitable only when inputs and outputs are very similar (e.g. grammatical error correction), and it does not support general stochastic sampling. After initial publication, an independent implementation (Chen et al., 2023) showed similar 2X–2.5X improvements on Chinchilla 70B.

## 6. Discussion and limitations

The paper presents speculative sampling, enabling efficient stochastic speculative execution, and analyzes its impact via speculative decoding. Given enough compute resources, it yields meaningful 2X–3X speedups in practice versus T5X.

**Limitation:** latency is improved through increased concurrency at the cost of an increased number of arithmetic operations, so the method is not helpful when additional compute resources are unavailable. But when extra compute is available (e.g. memory bandwidth is the bottleneck), it provides speedup with significant benefits: no architecture change, no retraining, and a guaranteed identical output distribution.

**Future directions:** compatibility with beam search (Appendix A.4); custom approximation models (custom architectures, non-autoregressive models, heuristics; custom training such as distillation with soft targets from Mp or optimizing Mq directly for α); a hierarchical version where the approximation model is itself accelerated; varying the approximation model and γ during inference; applying different distribution transformations; testing in other modalities (e.g. images). More broadly, stochastic speculative execution and speculative sampling may help outside autoregressive decoding — e.g. running two slow functions f(x) and g(y) in parallel where f generates a distribution from which g's input is sampled (physics simulations, reinforcement learning with a world simulator).

## Appendix highlights

- **A.1 Correctness of speculative sampling:** proves `P(x = x') = min(p(x'), q(x')) + p(x') − min(p(x'), q(x')) = p(x')`, using that the normalizing constant for `p'(x)` is `1 − β` (from Lemma 3.3 and Theorem 3.5).
- **A.2 Speculative sampling vs. rejection sampling:** a non-iterative rejection-sampling variant would have expected accept probability `Σ_x p(x) min_{x'} q(x')/p(x') ≤ Σ_x min(p(x), q(x)) = α`, potentially much lower than the method's α.
- **A.3 Theoretical predictions vs. empirical runtimes (Table 4):** expected (EXP) vs. measured (EMP) improvement factors mostly match; larger differences come from implementation/optimization differences and the i.i.d. β approximation.
- **A.4 Application to beam search:** with original beam width w, run beam search with Mq and beam width `u ≥ w` for γ steps, then check all candidates in parallel with Mp (cost `(w + uγ)` runs of Mp); accept Mq's guesses as long as `top_w(Mp) ⊆ top_u(Mq)` to get identical results to regular beam search.
- **A.5 Lenience:** a lenience parameter `l ∈ [0,1]` multiplies `q(x)` by `l` before comparison, guaranteeing no token is sampled with probability greater than `p(x)/l`. Then `α = Σ_x min(p(x)/l, q(x))`. With Mp = T5-XXL and Mq = T5-small and `c = 0.015`, lenience values 1, 0.5, 0.3, 0.1 give improvement factors of 2.5X, 3.1X, 3.6X, and 5X respectively (Table 5). For temperature = 0, lenience can instead be applied before standardizing (e.g. accept x if `p(x) ≤ l · max(p)`), giving similar α increases.
