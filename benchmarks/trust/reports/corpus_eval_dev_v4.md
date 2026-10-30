# Root-cause attribution on the corpus, dev split

34 failed runs with a planted tool fault (8 cases in the split were left out: no fault, an incomplete pipeline, or a correct final answer). Each cell is agent-correct / cases · step-correct / cases.

## All cases

| method | agent | step | agent 95% | step 95% | errors |
|---|---|---|---|---|---|
| last-step | 12/34 | 6/34 | [0.21, 0.52] | [0.08, 0.34] | 0 |
| first-step | 10/34 | 6/34 | [0.17, 0.46] | [0.08, 0.34] | 0 |
| llm-all:glm-5p3-flash | 11/34 | 11/34 | [0.19, 0.49] | [0.19, 0.49] | 0 |
| llm-all:deepseek-v4p1-flash | 17/34 | 16/34 | [0.34, 0.66] | [0.31, 0.63] | 0 |
| llm-step:glm-5p3-flash | 18/34 | 18/34 | [0.37, 0.69] | [0.37, 0.69] | 0 |
| sdk-structural | 12/34 | 6/34 | [0.21, 0.52] | [0.08, 0.34] | 0 |
| sdk-integrity | 34/34 | 34/34 | [0.90, 1.00] | [0.90, 1.00] | 0 |
| sdk-oracle | 34/34 | 28/34 | [0.90, 1.00] | [0.66, 0.92] | 0 |
| sdk-llmcheck:glm-5p3-flash | 34/34 | 28/34 | [0.90, 1.00] | [0.66, 0.92] | 0 |
| sdk-llmcheck+integrity:glm-5p3-flash | 34/34 | 34/34 | [0.90, 1.00] | [0.90, 1.00] | 0 |

## By fault kind

| group | last-step | first-step | llm-all:glm-5p3-flash | llm-all:deepseek-v4p1-flash | llm-step:glm-5p3-flash | sdk-structural | sdk-integrity | sdk-oracle | sdk-llmcheck:glm-5p3-flash | sdk-llmcheck+integrity:glm-5p3-flash |
|---|---|---|---|---|---|---|---|---|---|---|
| read | 6/18 · 0/18 | 6/18 · 6/18 | 11/18 · 11/18 | 13/18 · 12/18 | 12/18 · 12/18 | 6/18 · 0/18 | 18/18 · 18/18 | 18/18 · 12/18 | 18/18 · 12/18 | 18/18 · 18/18 |
| write | 6/16 · 6/16 | 4/16 · 0/16 | 0/16 · 0/16 | 4/16 · 4/16 | 6/16 · 6/16 | 6/16 · 6/16 | 16/16 · 16/16 | 16/16 · 16/16 | 16/16 · 16/16 | 16/16 · 16/16 |

## By node of the fault

| group | last-step | first-step | llm-all:glm-5p3-flash | llm-all:deepseek-v4p1-flash | llm-step:glm-5p3-flash | sdk-structural | sdk-integrity | sdk-oracle | sdk-llmcheck:glm-5p3-flash | sdk-llmcheck+integrity:glm-5p3-flash |
|---|---|---|---|---|---|---|---|---|---|---|
| A | 0/10 · 0/10 | 10/10 · 6/10 | 0/10 · 0/10 | 1/10 · 0/10 | 0/10 · 0/10 | 0/10 · 0/10 | 10/10 · 10/10 | 10/10 · 4/10 | 10/10 · 4/10 | 10/10 · 10/10 |
| B | 0/12 · 0/12 | 0/12 · 0/12 | 6/12 · 6/12 | 7/12 · 7/12 | 6/12 · 6/12 | 0/12 · 0/12 | 12/12 · 12/12 | 12/12 · 12/12 | 12/12 · 12/12 | 12/12 · 12/12 |
| C | 12/12 · 6/12 | 0/12 · 0/12 | 5/12 · 5/12 | 9/12 · 9/12 | 12/12 · 12/12 | 12/12 · 6/12 | 12/12 · 12/12 | 12/12 · 12/12 | 12/12 · 12/12 | 12/12 · 12/12 |

## By model that ran the pipeline

| group | last-step | first-step | llm-all:glm-5p3-flash | llm-all:deepseek-v4p1-flash | llm-step:glm-5p3-flash | sdk-structural | sdk-integrity | sdk-oracle | sdk-llmcheck:glm-5p3-flash | sdk-llmcheck+integrity:glm-5p3-flash |
|---|---|---|---|---|---|---|---|---|---|---|
| deepseek-v4p1-flash | 4/12 · 2/12 | 4/12 · 2/12 | 3/12 · 3/12 | 6/12 · 6/12 | 6/12 · 6/12 | 4/12 · 2/12 | 12/12 · 12/12 | 12/12 · 10/12 | 12/12 · 10/12 | 12/12 · 12/12 |
| glm-5p3-flash | 4/12 · 2/12 | 4/12 · 2/12 | 4/12 · 4/12 | 5/12 · 5/12 | 6/12 · 6/12 | 4/12 · 2/12 | 12/12 · 12/12 | 12/12 · 10/12 | 12/12 · 10/12 | 12/12 · 12/12 |
| minimax-m3p1-flash | 4/10 · 2/10 | 2/10 · 2/10 | 4/10 · 4/10 | 6/10 · 5/10 | 6/10 · 6/10 | 4/10 · 2/10 | 10/10 · 10/10 | 10/10 · 8/10 | 10/10 · 8/10 | 10/10 · 10/10 |

LLM calls made: 690 (535 answered from the cache); tokens sent 153,535 and received 367,591; 19 reply(ies) ran out of the token budget and were asked again with a larger one.
