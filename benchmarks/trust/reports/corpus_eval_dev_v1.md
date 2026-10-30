# Root-cause attribution on the corpus, dev split

34 failed runs with a planted tool fault (8 cases in the split were left out: no fault, an incomplete pipeline, or a correct final answer). Each cell is agent-correct / cases · step-correct / cases.

## All cases

| method | agent | step | agent 95% | step 95% | errors |
|---|---|---|---|---|---|
| last-step | 12/34 | 6/34 | [0.21, 0.52] | [0.08, 0.34] | 0 |
| first-step | 10/34 | 6/34 | [0.17, 0.46] | [0.08, 0.34] | 0 |
| llm-all:glm-5p3-flash | 12/34 | 12/34 | [0.21, 0.52] | [0.21, 0.52] | 0 |
| llm-all:deepseek-v4p1-flash | 2/34 | 2/34 | [0.02, 0.19] | [0.02, 0.19] | 0 |
| llm-step:glm-5p3-flash | 18/34 | 18/34 | [0.37, 0.69] | [0.37, 0.69] | 0 |
| sdk-structural | 12/34 | 6/34 | [0.21, 0.52] | [0.08, 0.34] | 0 |
| sdk-oracle | 34/34 | 16/34 | [0.90, 1.00] | [0.31, 0.63] | 0 |
| sdk-llmcheck:glm-5p3-flash | 34/34 | 16/34 | [0.90, 1.00] | [0.31, 0.63] | 0 |

## By fault kind

| group | last-step | first-step | llm-all:glm-5p3-flash | llm-all:deepseek-v4p1-flash | llm-step:glm-5p3-flash | sdk-structural | sdk-oracle | sdk-llmcheck:glm-5p3-flash |
|---|---|---|---|---|---|---|---|---|
| read | 6/18 · 0/18 | 6/18 · 6/18 | 11/18 · 11/18 | 2/18 · 2/18 | 12/18 · 12/18 | 6/18 · 0/18 | 18/18 · 0/18 | 18/18 · 0/18 |
| write | 6/16 · 6/16 | 4/16 · 0/16 | 1/16 · 1/16 | 0/16 · 0/16 | 6/16 · 6/16 | 6/16 · 6/16 | 16/16 · 16/16 | 16/16 · 16/16 |

## By node of the fault

| group | last-step | first-step | llm-all:glm-5p3-flash | llm-all:deepseek-v4p1-flash | llm-step:glm-5p3-flash | sdk-structural | sdk-oracle | sdk-llmcheck:glm-5p3-flash |
|---|---|---|---|---|---|---|---|---|
| A | 0/10 · 0/10 | 10/10 · 6/10 | 0/10 · 0/10 | 0/10 · 0/10 | 0/10 · 0/10 | 0/10 · 0/10 | 10/10 · 4/10 | 10/10 · 4/10 |
| B | 0/12 · 0/12 | 0/12 · 0/12 | 7/12 · 7/12 | 2/12 · 2/12 | 6/12 · 6/12 | 0/12 · 0/12 | 12/12 · 6/12 | 12/12 · 6/12 |
| C | 12/12 · 6/12 | 0/12 · 0/12 | 5/12 · 5/12 | 0/12 · 0/12 | 12/12 · 12/12 | 12/12 · 6/12 | 12/12 · 6/12 | 12/12 · 6/12 |

## By model that ran the pipeline

| group | last-step | first-step | llm-all:glm-5p3-flash | llm-all:deepseek-v4p1-flash | llm-step:glm-5p3-flash | sdk-structural | sdk-oracle | sdk-llmcheck:glm-5p3-flash |
|---|---|---|---|---|---|---|---|---|
| deepseek-v4p1-flash | 4/12 · 2/12 | 4/12 · 2/12 | 4/12 · 4/12 | 0/12 · 0/12 | 6/12 · 6/12 | 4/12 · 2/12 | 12/12 · 6/12 | 12/12 · 6/12 |
| glm-5p3-flash | 4/12 · 2/12 | 4/12 · 2/12 | 4/12 · 4/12 | 0/12 · 0/12 | 6/12 · 6/12 | 4/12 · 2/12 | 12/12 · 6/12 | 12/12 · 6/12 |
| minimax-m3p1-flash | 4/10 · 2/10 | 2/10 · 2/10 | 4/10 · 4/10 | 2/10 · 2/10 | 6/10 · 6/10 | 4/10 · 2/10 | 10/10 · 4/10 | 10/10 · 4/10 |

LLM calls made: 455 (313 answered from the cache); tokens sent 145,539 and received 104,014.
