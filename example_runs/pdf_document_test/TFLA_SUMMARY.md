# Tiled Flash Linear Attention (TFLA) — Paper Summary

Paper: *Tiled Flash Linear Attention: More Efficient Linear RNN and xLSTM Kernels*
(Beck, Pöppel, Lippe, Hochreiter; arXiv:2503.14376v3, NeurIPS 2025). 54 pages, read in full
(pages 1–54) via `web_fetch`, with diagrams examined via `render_pdf_page` + `interpret_pdf_image`.

## 1. What the paper proposes

Linear RNNs with gating (e.g. the xLSTM's mLSTM) scale linearly in sequence length, but to
actually beat Transformers they need custom GPU kernels as good as FlashAttention. Flash Linear
Attention (FLA) does this by using the **chunkwise-parallel formulation**: split the sequence into
chunks, run a *recurrent* kernel that materializes the memory state `C_{k-1}` at each chunk
boundary, then a *parallel* kernel that computes all chunk outputs at once. The problem: FLA's
chunk size is capped by GPU SRAM (typically L = 64), so for long sequences many intermediate
states must be written to and read from HBM — low arithmetic intensity, high memory cost.

The paper's core contribution is **Tiled Flash Linear Attention (TFLA)**, a kernel algorithm that
adds a **second level of sequence parallelism** by *tiling the intra-chunk attention matrix along
the sequence dimension*. This decouples the chunk size from the SRAM tile size, allowing
**arbitrarily large chunk sizes** and a tunable trade-off between GPU memory and runtime. TFLA
parallelizes over five dimensions (batch, head, chunk, outer sequence block `L_hq`, outer embedding
block `d_hv`) and loops over the inner dimensions (`L_kv`, `d_qk`).

Three contributions:
1. **TFLA** — a chunkwise-parallel kernel algorithm with two levels of sequence parallelism,
   applied to the mLSTM.
2. **mLSTMsig** — an mLSTM variant with a *sigmoid* input gate that drops the normalizer and max
   states, giving >30% faster forward kernels with no language-modeling loss up to 1.4B params.
3. **Gate-initialization study** — a control-theory-inspired transfer-behavior analysis showing
   input-gate biases should be initialized at large negative values (e.g. −10) for stability.

## 2. How it works

**mLSTM formulations.** The recurrent mLSTM keeps a matrix cell state `C_t`, a normalizer `n_t`,
and a max state `m_t` (Eqs. 1–5). The chunkwise-parallel form splits into an *inter-chunk recurrent*
part (Eqs. 6–7: `C_k = ḡ_k C_{k-1} + (a_k ⊙ K^(k))ᵀ V^(k)`) and an *intra-chunk parallel* part
(Eqs. 8–11), where the quadratic `L×L` gate matrix `D^(k)` and score matrix `S^(k)` are small
because L ≪ T. Outputs combine `H_inter = Q^(k) C_{k-1}` and `H_intra = S^(k) V^(k)`.

**TFLA forward pass.** The intra-chunk computation is three fused matmuls (Eq. 12):
`H^(k) = (Q^(k) K^(k)ᵀ) V^(k) + Q^(k) C_{k-1}`. TFLA tiles these: it parallelizes over `L_hq`
and `d_hv` blocks and loops over `L_kv` and `d_qk` blocks. Because the exponential input gate is
stabilized by the max state `m_t` (safe-softmax style), block results must be **rescaled during
accumulation** exactly like FlashAttention; the backward pass needs no rescaling because max states
are stored in the forward pass. The backward pass computes δQ, δK, δV, each with intra- and
inter-chunk parts, using the same work partitioning with loop/parallel dims swapped (Table 1).

**mLSTMsig.** Because input-gate pre-activations stay negative in practice, the sigmoid
`σ(x) = exp(x)/(exp(x)+1)` behaves like `exp(x)` in the negative range but is bounded above, so no
max state or normalizer is needed (Eqs. 13–15). This removes summations and lets the kernel fuse
loops that the max-state tracking previously blocked.

**Transfer analysis.** Gain `G = ‖h̃_t‖_max / ‖v_t‖_max` before/after the norm layer is measured
over a grid of input/forget gate pre-activations. Both variants show the same gate-dependent
transition from suppressing (G=0) to passing (G=1) the signal after normalization, and the norm
epsilon shifts the gain curve — evidence the norm layer participates in gating.

**Experiments.** Language modeling at 160M/400M/1.4B params (context 4096/8192) shows mLSTMsig ≈
mLSTMexp. Kernel benchmarks on H100 show TFLA kernels beat FlashAttention 3 for long sequences and
are >2× faster than Mamba 2 in training. Theoretical analysis (App. F/G) derives FLOP-optimal and
runtime-optimal chunk sizes, both scaling as O(√d_hv), with the runtime optimum also scaling as
O(√I_acc) — so newer GPUs favor larger chunks.

## 3. Diagrams interpreted (grounded in `interpret_pdf_image` output)

- **Figure 1 (page 2) — TFLA overview.** Shows the two-kernel structure: input chunks QKV^(1..3)
  feed a *recurrent kernel* that materializes memory states C0, C1, C2, then a *parallel kernel*
  with "inter chunk" and "intra chunk" parts producing outputs H^(1..3). Confirms the two-level
  sequence parallelism and that tiling prevents materializing many states.
- **Figure 2 (page 3) — chunkwise gates.** Illustration of the summed forget gate `g_k`, cumulative
  forget gate `b_k`, and cumulative input gate `a_k` for chunk size L=4, each arrow an element of
  the gate vectors.
- **Figure 3 (page 5) — TFLA intra-chunk tiling.** Three matmul diagrams showing Q, K, V, C, H
  blocks; arrows mark the looped dims `B_Lkv`/`B_dqk`, dashed lines mark the parallelized dims
  `B_Lhq`/`B_dhv`, with ⊕ for block-wise accumulation. This is the key tiling strategy.
- **Figure 4 (page 6) — transfer behavior.** Two-panel heatmaps (mLSTMexp, mLSTMsig) with axes
  Input-Gate Preactivation × Forget-Gate Preactivation, panels "Gain before Norm" and "Gain after
  Norm"; color = gain. After the norm layer both variants show identical gate-dependent transfer.
- **Figure 5 (page 7) — runtime benchmark.** Two line charts (Inference / Training) of Time [ms]
  vs Sequence Length, with 10 legend series (Torch/cuDNN/FlashAttn 3, GLA, Simple GLA, Mamba,
  Mamba 2, mLSTMexp limit/TFLA-XL, mLSTMsig TFLA-XL).
- **Figure 6 (page 9) — memory vs runtime trade-off.** Bar chart of Time [ms] and GPU Memory [GB]
  over chunk sizes 64…4096, showing the runtime minimum around chunk size 128–256.
- **Figure 7 (page 10) — TFLA = FLA + FlashAttention 2.** Three side-by-side tiling schemes:
  TFLA (left, combines inter+intra chunk tiling, tunable chunk size and head dim), Flash Linear
  Attention (middle, limited chunk size), Flash Attention 2 (right, full sequence in tiles, limited
  head dim, softmax re-weighting).
- **Figure 19 (page 46) — FLOP counts.** Three panels (different head dims) plotting FLOPs vs chunk
  size L on log scale, with curves for Recurrent, Parallel, Chunkwise and causal factors
  0.5/0.66/1.0; the chunkwise curve transitions between recurrent (L=1) and parallel (L=T) counts.

## 4. Image refs interpreted

| image_ref | What it showed |
|---|---|
| `pdf:f2caa942004db5f0:p2:i1` | Blank/black embedded raster — no content (confidence 0.05). |
| `pdf:f2caa942004db5f0:p2:page` | Page 2 with Figure 1: TFLA recurrent + parallel kernel overview (C0–C2 states, H^(1..3) outputs). |
| `pdf:f2caa942004db5f0:p3:page` | Page 3 with Figure 2: chunkwise gates a_k, b_k, g_k for L=4, plus Eqs. 1–5. |
| `pdf:f2caa942004db5f0:p5:page` | Page 5 with Figure 3 (TFLA intra-chunk tiling) and Table 1 (parallel/loop dims per kernel). |
| `pdf:f2caa942004db5f0:p6:page` | Page 6 with Figure 4: transfer-behavior heatmaps before/after norm for mLSTMexp vs mLSTMsig. |
| `pdf:f2caa942004db5f0:p7:page` | Page 7 with Figure 5: inference/training runtime line charts vs sequence length. |
| `pdf:f2caa942004db5f0:p9:page` | Page 9 with Figure 6: memory-vs-runtime bar chart over chunk sizes. |
| `pdf:f2caa942004db5f0:p10:page` | Page 10 with Figure 7: TFLA vs FLA vs FlashAttention-2 tiling comparison. |
| `pdf:f2caa942004db5f0:p21:i1` | Small embedded raster (green square) — no reviewable content (confidence 0.3). |
| `pdf:f2caa942004db5f0:p21:i2` | Small embedded raster (orange square) — no reviewable content (confidence 0.12). |
| `pdf:f2caa942004db5f0:p46:page` | Page 46 with Figure 19: FLOP counts vs chunk size for recurrent/parallel/chunkwise mLSTMsig. |

Note: page 21's rendered page image (`pdf:f2caa942004db5f0:p21:page`) was attempted twice but the
vision provider returned empty responses; its embedded rasters (i1, i2) are the small gate-illustration
graphics of Figure 9 and were not machine-readable. Figure 9's content is described in the paper text
(chunkwise gate computation illustration) and is consistent with Figure 2.
