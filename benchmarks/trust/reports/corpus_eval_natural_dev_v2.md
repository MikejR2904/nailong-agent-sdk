# Root-cause attribution on the natural corpus, dev split

28 failed runs (14 cases in the split were left out: no recorded fault, an incomplete pipeline, or a correct final answer). Agent and step are exact matches; ±1 and ±2 count a step within that many calls of the labelled one; top-3 counts the labelled step among the first three a method ranks (one for methods that rank nothing); 'answered' is how often a method named a step at all; calls, tokens and seconds are per case.

## All cases

| method | agent | step | step 95% | ±1 | ±2 | top-3 | answered | step if answered | calls | tokens | seconds | errors |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| sdk-llmcheck:glm-5p3-flash | 27/28 | 27/28 | [0.82, 0.99] | 27/28 | 27/28 | 28/28 | 28/28 | 27/28 | 21.1 | 48,827 | 189 | 0 |

## Differences from sdk-structural

Step-level accuracy of each method minus that of sdk-structural, on the same cases.

| method | reference only right | method only right | difference [95% bootstrap] | McNemar p |
|---|---|---|---|---|

## By fault kind (step correct / cases)

| group | sdk-llmcheck:glm-5p3-flash |
|---|---|
| natural | 27/28 |

## By node of the fault (step correct / cases)

| group | sdk-llmcheck:glm-5p3-flash |
|---|---|
| - | 27/28 |

## By model that ran the pipeline (step correct / cases)

| group | sdk-llmcheck:glm-5p3-flash |
|---|---|
| deepseek-v4p1-flash | 11/11 |
| glm-5p3-flash | 13/13 |
| minimax-m3p1-flash | 3/4 |

## By length of the trace (step correct / cases)

| group | sdk-llmcheck:glm-5p3-flash |
|---|---|
| >12 steps | 1/1 |
| ≤8 steps | 26/27 |

Model calls made: 592 (436 answered from the cache); new tokens sent 63,730 and received 267,261; 1 reply(ies) ran out of the token budget and were asked again with a larger one.
