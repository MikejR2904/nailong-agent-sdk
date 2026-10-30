# Root-cause attribution on the corpus, dev split

34 failed runs with a planted tool fault (8 cases in the split were left out: no fault, an incomplete pipeline, or a correct final answer). Each cell is agent-correct / cases · step-correct / cases.

## All cases

| method | agent | step | agent 95% | step 95% | errors |
|---|---|---|---|---|---|
| sdk-structural | 12/34 | 6/34 | [0.21, 0.52] | [0.08, 0.34] | 0 |
| sdk-oracle | 34/34 | 28/34 | [0.90, 1.00] | [0.66, 0.92] | 0 |
| sdk-llmcheck:glm-5p3-flash | 34/34 | 28/34 | [0.90, 1.00] | [0.66, 0.92] | 0 |

## By fault kind

| group | sdk-structural | sdk-oracle | sdk-llmcheck:glm-5p3-flash |
|---|---|---|---|
| read | 6/18 · 0/18 | 18/18 · 12/18 | 18/18 · 12/18 |
| write | 6/16 · 6/16 | 16/16 · 16/16 | 16/16 · 16/16 |

## By node of the fault

| group | sdk-structural | sdk-oracle | sdk-llmcheck:glm-5p3-flash |
|---|---|---|---|
| A | 0/10 · 0/10 | 10/10 · 4/10 | 10/10 · 4/10 |
| B | 0/12 · 0/12 | 12/12 · 12/12 | 12/12 · 12/12 |
| C | 12/12 · 6/12 | 12/12 · 12/12 | 12/12 · 12/12 |

## By model that ran the pipeline

| group | sdk-structural | sdk-oracle | sdk-llmcheck:glm-5p3-flash |
|---|---|---|---|
| deepseek-v4p1-flash | 4/12 · 2/12 | 12/12 · 10/12 | 12/12 · 10/12 |
| glm-5p3-flash | 4/12 · 2/12 | 12/12 · 10/12 | 12/12 · 10/12 |
| minimax-m3p1-flash | 4/10 · 2/10 | 10/10 · 8/10 | 10/10 · 8/10 |

LLM calls made: 218 (218 answered from the cache); tokens sent 0 and received 0.
