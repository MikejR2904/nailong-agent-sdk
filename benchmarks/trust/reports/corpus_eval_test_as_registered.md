# Root-cause attribution on the corpus, test split

68 failed runs with a planted tool fault (16 cases in the split were left out: no fault, an incomplete pipeline, or a correct final answer). Each cell is agent-correct / cases · step-correct / cases.

## All cases

| method | agent | step | agent 95% | step 95% | errors |
|---|---|---|---|---|---|
| last-step | 24/68 | 12/68 | [0.25, 0.47] | [0.10, 0.28] | 0 |
| first-step | 20/68 | 12/68 | [0.20, 0.41] | [0.10, 0.28] | 0 |
| llm-all:glm-5p3-flash | 15/68 | 15/68 | [0.14, 0.33] | [0.14, 0.33] | 0 |
| llm-all:deepseek-v4p1-flash | 1/68 | 1/68 | [0.00, 0.08] | [0.00, 0.08] | 0 |
| llm-step:glm-5p3-flash | 36/68 | 36/68 | [0.41, 0.64] | [0.41, 0.64] | 0 |
| sdk-structural | 24/68 | 12/68 | [0.25, 0.47] | [0.10, 0.28] | 0 |
| sdk-integrity | 68/68 | 68/68 | [0.95, 1.00] | [0.95, 1.00] | 0 |
| sdk-oracle | 68/68 | 56/68 | [0.95, 1.00] | [0.72, 0.90] | 0 |
| sdk-llmcheck:glm-5p3-flash | 68/68 | 56/68 | [0.95, 1.00] | [0.72, 0.90] | 0 |
| sdk-llmcheck+integrity:glm-5p3-flash | 68/68 | 68/68 | [0.95, 1.00] | [0.95, 1.00] | 0 |

## By fault kind

| group | last-step | first-step | llm-all:glm-5p3-flash | llm-all:deepseek-v4p1-flash | llm-step:glm-5p3-flash | sdk-structural | sdk-integrity | sdk-oracle | sdk-llmcheck:glm-5p3-flash | sdk-llmcheck+integrity:glm-5p3-flash |
|---|---|---|---|---|---|---|---|---|---|---|
| read | 12/36 · 0/36 | 12/36 · 12/36 | 15/36 · 15/36 | 1/36 · 1/36 | 24/36 · 24/36 | 12/36 · 0/36 | 36/36 · 36/36 | 36/36 · 24/36 | 36/36 · 24/36 | 36/36 · 36/36 |
| write | 12/32 · 12/32 | 8/32 · 0/32 | 0/32 · 0/32 | 0/32 · 0/32 | 12/32 · 12/32 | 12/32 · 12/32 | 32/32 · 32/32 | 32/32 · 32/32 | 32/32 · 32/32 | 32/32 · 32/32 |

## By node of the fault

| group | last-step | first-step | llm-all:glm-5p3-flash | llm-all:deepseek-v4p1-flash | llm-step:glm-5p3-flash | sdk-structural | sdk-integrity | sdk-oracle | sdk-llmcheck:glm-5p3-flash | sdk-llmcheck+integrity:glm-5p3-flash |
|---|---|---|---|---|---|---|---|---|---|---|
| A | 0/20 · 0/20 | 20/20 · 12/20 | 0/20 · 0/20 | 0/20 · 0/20 | 0/20 · 0/20 | 0/20 · 0/20 | 20/20 · 20/20 | 20/20 · 8/20 | 20/20 · 8/20 | 20/20 · 20/20 |
| B | 0/24 · 0/24 | 0/24 · 0/24 | 10/24 · 10/24 | 1/24 · 1/24 | 12/24 · 12/24 | 0/24 · 0/24 | 24/24 · 24/24 | 24/24 · 24/24 | 24/24 · 24/24 | 24/24 · 24/24 |
| C | 24/24 · 12/24 | 0/24 · 0/24 | 5/24 · 5/24 | 0/24 · 0/24 | 24/24 · 24/24 | 24/24 · 12/24 | 24/24 · 24/24 | 24/24 · 24/24 | 24/24 · 24/24 | 24/24 · 24/24 |

## By model that ran the pipeline

| group | last-step | first-step | llm-all:glm-5p3-flash | llm-all:deepseek-v4p1-flash | llm-step:glm-5p3-flash | sdk-structural | sdk-integrity | sdk-oracle | sdk-llmcheck:glm-5p3-flash | sdk-llmcheck+integrity:glm-5p3-flash |
|---|---|---|---|---|---|---|---|---|---|---|
| deepseek-v4p1-flash | 8/24 · 4/24 | 8/24 · 4/24 | 5/24 · 5/24 | 0/24 · 0/24 | 12/24 · 12/24 | 8/24 · 4/24 | 24/24 · 24/24 | 24/24 · 20/24 | 24/24 · 20/24 | 24/24 · 24/24 |
| glm-5p3-flash | 8/24 · 4/24 | 8/24 · 4/24 | 6/24 · 6/24 | 0/24 · 0/24 | 12/24 · 12/24 | 8/24 · 4/24 | 24/24 · 24/24 | 24/24 · 20/24 | 24/24 · 20/24 | 24/24 · 24/24 |
| minimax-m3p1-flash | 8/20 · 4/20 | 4/20 · 4/20 | 4/20 · 4/20 | 1/20 · 1/20 | 12/20 · 12/20 | 8/20 · 4/20 | 20/20 · 20/20 | 20/20 · 16/20 | 20/20 · 16/20 | 20/20 · 20/20 |

LLM calls made: 1372 (1034 answered from the cache); tokens sent 308,599 and received 226,288.
