# Token economy: prompt-prefix stability and the cost model

What the SDK does to keep repeated model calls cheap, how to price them, and what is **not** yet
measured. No model was called to build or test any of this.

## 1. Prompt-prefix stability (`agent/openai_compatible/chat.py`)

Provider prompt caches reuse the longest identical *prefix* of consecutive requests. The request is
laid out so that what is the same every turn comes first and what changes every turn comes last:

| Order | Content | Changes |
|---|---|---|
| 1 | system message | never (it is the same with or without semantic summaries) |
| 2 | stable message: task id and input, prompt, output schema | never within a task |
| 3 | volatile message, in this key order: `semantic_summaries`, `omitted_summary_count`, `compacted_episodes`, `omitted_compacted_count` | only when memory is compacted |
| 4 | `recent_observations`, then `episode_summaries` | append-only between compactions |
| 5 | `project_state`, `iteration` | every turn |

Also changed:
- The system message used to gain a sentence the first time a semantic summary appeared, which changed the
  start of the prompt mid-run. It now always contains that sentence.
- Tool definitions were sent twice, inside the stable prompt and as native function tools. When tools are
  sent natively the prompt now carries only their names.
- `ContextProjectionPolicy.observation_block` (default 0, which keeps the sliding window) starts the
  observation window on a multiple of `block`, so its front moves once per `block` turns instead of every turn,
  at the cost of up to `block - 1` extra old observations being left out. It is opt-in because the trade
  depends on the provider's cache discount.

**Offline check.** Every request records an estimate of its size and of the prefix it shares with the task's
previous request (`OpenAICompatibleAgentModel.request_diagnostics`, metrics `model.estimated_request_tokens`
and `model.estimated_shared_prefix_tokens`). In the unit test with growing observations the new layout shares
more than 90% of the previous request from the sixth turn, and more than twice as many tokens as the previous
layout (`tests/agent/test_prompt_prefix_stability.py`). These are character-based estimates, an upper bound on
what a provider can reuse.

## 2. Cost model (`agent/cost_model.py`)

The host supplies prices; the SDK ships none.

```python
price = TokenPrice(input_per_million=Decimal("2.50"),
                   cached_input_per_million=Decimal("1.25"),
                   output_per_million=Decimal("10.00"))
agent = BaseAgent(definition, model, price_table=PriceTable({("provider", "model"): price}))
```

With a price table, each call is priced from provider-reported usage (fresh input, cached input, output) and
recorded as `model.cost`, `model.cache_savings` and `model.cache_hit_ratio`; `agent.cost_ledger` holds the run's
totals. An unknown model, or usage without input or output counts, records the metrics as unavailable with the
error code instead of failing the run. A provider that reports no cached counter is priced as uncached and
flagged, so savings are never invented.

`forecast_run(...)` predicts a run's cost from the stable prefix size, per-turn growth, the volatile tail and
output size, with `CacheRules` for the provider's minimum prefix and block size (`OPENAI_AUTOMATIC` is
1,024 and 128 tokens; confirm it against the provider you use). Illustrative arithmetic only, with made-up prices:
for 3,000 stable tokens, 1,200 tokens of growth per turn, a 300-token changing tail and 8 turns, a layout whose
history precedes the changing tail costs 34% less than the uncached cost, against 16% for a layout that
puts the changing fields first.

## 3. Read-only tool-result cache (`tools/result_cache.py`)

Opt-in: nothing is cached unless the host lists the tool and a time-to-live.

```python
cache = ToolResultCache(ToolCachePolicy(cacheable={"read_file": CacheableTool(ttl_seconds=60)}))
executor = HarnessToolExecutor(registry, context, cache)
```

- Only successful results of tools classified `READ_ONLY` are stored, keyed by tool name and canonical arguments.
- Capability policy is evaluated before the cache is consulted, so a hit never bypasses it.
- Running any `MUTATING`, `PROCESS` or `DESTRUCTIVE` tool clears the whole cache, so a read is never served from
  before a write the same run made. Changes made outside the run are invisible; the time-to-live bounds the staleness.
- With `reference_window > 0`, a repeat within that many iterations of the first call is answered with a short
  `unchanged_since` reference (call id, iteration, token count) instead of the full text, which is where context
  tokens are saved. It assumes the first result is still in the model's context; set it to a number of iterations
  you know is safe, or leave it at 0 to replay the text (saves execution time, not context tokens).
- `cache.stats()` reports hits, references, misses, expirations, invalidations (with reasons) and replayed tokens.

## 4. Cache-aware compaction (`PaskCompactionPolicy.cache_tolerance`)

A prefix cache reuses everything before the first change to the context, so evicting an old episode forces the
episodes after it to be read at the fresh price. With `cache_tolerance = e > 0` the exact PCKP selection is
replaced by the best selection that keeps the longest chronological prefix of live episodes whose retained
utility is still at least `(1 - e)` of the unconstrained optimum (found by bisection; the solver stays exact for
each constrained problem). `dossier["cache_aware"]` reports the forced prefix, the utility loss, and the tokens that
would be re-read with and without the constraint. `0` (the default) changes nothing. The tolerance is a utility
trade, not a price: `cost_model.TokenPrice` tells you what a re-read costs, and choosing `e` from it is a host decision.

## 5. Closure-aware on-demand retrieval

The store could already restore archived episodes with their archived prerequisites when an action consumes them
(`rehydration_token_budget` > 0). Now:
- `InMemoryEpisodeStore.rehydration_quote(ids)` states each episode's closure and token cost without restoring it,
  and compacted stubs carry `restore_tokens` when retrieval is enabled, so the model and the host can see the price.
- `ContextProjectionPolicy.rehydration_run_budget` caps the tokens restored over a whole run (the per-call budget
  alone could be spent again on every call); the run total appears in the `episodes-rehydrated` event.
- `rehydration_order="cheapest-first"` serves requests by the tokens their closure adds beyond what is already
  scheduled, so requests that share a prerequisite pay for it once and a cheap request is not starved by an
  expensive one. The default `in-order` is unchanged.

## 6. Limits

- The cached-input counter is read from `prompt_tokens_details.cached_tokens`; other providers' fields are not
  parsed, and cache-write charges are not modelled.
- The effect on real provider cache hits is unmeasured. It needs live calls to compare `cached_input_tokens`
  before and after, which has not been done.
- Reordering the volatile keys and removing the duplicate tool text change what the model reads, not what it is
  told; whether answer quality is unaffected is also unmeasured.
- Cache-aware compaction trades utility for prefix reuse using a tolerance the host chooses; its real effect on
  provider cache hits is unmeasured. The tool-result cache and the retrieval changes are tested offline only.
- Nothing here has been run against a live model.

## 7. Measured on a real provider

Fireworks `glm-5p3-flash`, 674 calls, 3.92M provider-reported prompt tokens, about $0.35 at its list price
($0.15 input, $0.03 cached input, $0.50 output per million tokens, from its pricing page). Method and
safeguards: `benchmarks/token_economy/PLAN.md`; full tables: `results/live/live_report.md`. Content is identical
across layouts (the same generated tool-call histories replayed through the SDK's store and projector, one model
call per tool call, 3 histories x 2 repeats per arm, unique cache salt per unit, output capped at 32 tokens). Only
provider usage counters are scored, and prices are applied to them: billing itself was not checked.

| Regime | Arm | Prompt tokens / run | Billed at full price / run | Cached / run | Hit ratio | Cost / run | vs legacy (95% CI) |
|---|---|---|---|---|---|---|---|
| long, 22 turns | legacy (before) | 118,146 | 60,972 | 57,173 | 0.484 | $0.01117 | baseline |
| long | current | 113,779 | 52,936 | 60,842 | 0.535 | $0.01007 | 0.902 (0.890-0.910) |
| long | current + block-aligned window | 110,837 | 49,653 | 61,184 | 0.552 | $0.00959 | 0.859 (0.854-0.863) |
| long | current + cache tolerance 0.2 | 113,935 | 53,327 | 60,608 | 0.532 | $0.01012 | 0.907 (0.895-0.915) |
| roomy, 7-10 turns | legacy (before) | 43,417 | 19,673 | 23,744 | 0.547 | $0.00380 | baseline |
| roomy | current | 41,417 | 17,631 | 23,786 | 0.574 | $0.00350 | 0.920 (0.852-1.003) |

- **Real cache reuse is smaller than the layout alone predicts.** Prompt-prefix stability cut cost about 10%
  (long) and 8% (roomy, interval includes zero), and billed full-price input tokens by 13% and 10%. A simulated
  provider with a clean prefix rule predicted 23%. Provider-reported cached tokens were 78-92% of the SDK's
  estimated shared prefix, and per-turn hit ratios are noisy (replica routing: Fireworks caches per replica).
- **The gain is in the stretch before compaction.** Until the SDK first compacts, the current layout costs 17% less
  per call than the old one (hit ratio 0.62 vs 0.51). Once compaction is active it costs the same (ratio 1.012,
  interval 0.995-1.033; hit ratio 0.41 vs 0.44), because every eviction changes the prompt in the middle.
- **Block-aligned observation window** (`observation_block=4`) was the best arm: 14% cheaper than legacy overall and
  7% cheaper than legacy after compaction starts (0.928, 0.909-0.948). It moves the window front once per four
  turns instead of every turn.
- **Cache-aware compaction (`cache_tolerance=0.2`) did nothing measurable** on these histories (0.907 vs 0.902 for
  the same layout without it). Episodes that later steps consume are protected, which leaves little room to
  force a prefix. The option remains untested on workloads without such dependencies.
- Not measured: whether the new layout changes answer quality (the model reads reordered text and sees tool
  definitions once); the read-only tool-result cache and closure-aware retrieval (they do not change this request
  stream); provider billing; other providers.
