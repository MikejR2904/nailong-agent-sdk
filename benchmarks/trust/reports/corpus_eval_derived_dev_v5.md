# Root-cause attribution on the derived corpus, dev split

18 failed runs (0 cases in the split were left out: no recorded fault, an incomplete pipeline, or a correct final answer). Agent and step are exact matches; ±1 and ±2 count a step within that many calls of the labelled one; top-3 counts the labelled step among the first three a method ranks (one for methods that rank nothing); 'answered' is how often a method named a step at all; calls, tokens and seconds are per case.

## All cases

| method | agent | step | step 95% | ±1 | ±2 | top-3 | answered | step if answered | calls | tokens | seconds | errors |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| sdk-llmcheck:glm-5p3-flash | 18/18 | 18/18 | [0.82, 1.00] | 18/18 | 18/18 | 18/18 | 18/18 | 18/18 | 21.2 | 47,652 | 250 | 0 |
| sdk-llmcheck+integrity:glm-5p3-flash | 18/18 | 18/18 | [0.82, 1.00] | 18/18 | 18/18 | 18/18 | 18/18 | 18/18 | 21.2 | 47,685 | 243 | 0 |

## Differences from sdk-structural

Step-level accuracy of each method minus that of sdk-structural, on the same cases.

| method | reference only right | method only right | difference [95% bootstrap] | McNemar p |
|---|---|---|---|---|

## By fault kind (step correct / cases)

| group | sdk-llmcheck:glm-5p3-flash | sdk-llmcheck+integrity:glm-5p3-flash |
|---|---|---|
| natural | 18/18 | 18/18 |

## By node of the fault (step correct / cases)

| group | sdk-llmcheck:glm-5p3-flash | sdk-llmcheck+integrity:glm-5p3-flash |
|---|---|---|
| - | 18/18 | 18/18 |

## By model that ran the pipeline (step correct / cases)

| group | sdk-llmcheck:glm-5p3-flash | sdk-llmcheck+integrity:glm-5p3-flash |
|---|---|---|
| deepseek-v4p1-flash | 6/6 | 6/6 |
| glm-5p3-flash | 6/6 | 6/6 |
| minimax-m3p1-flash | 6/6 | 6/6 |

## By length of the trace (step correct / cases)

| group | sdk-llmcheck:glm-5p3-flash | sdk-llmcheck+integrity:glm-5p3-flash |
|---|---|---|
| ≤8 steps | 18/18 | 18/18 |

Model calls made: 762 (548 answered from the cache); new tokens sent 130,082 and received 360,451; 0 reply(ies) ran out of the token budget and were asked again with a larger one.
