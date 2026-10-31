# Root-cause attribution on the natural corpus, dev split

28 failed runs (14 cases in the split were left out: no recorded fault, an incomplete pipeline, or a correct final answer). Agent and step are exact matches; ±1 and ±2 count a step within that many calls of the labelled one; top-3 counts the labelled step among the first three a method ranks (one for methods that rank nothing); 'answered' is how often a method named a step at all; calls, tokens and seconds are per case.

## All cases

| method | agent | step | step 95% | ±1 | ±2 | top-3 | answered | step if answered | calls | tokens | seconds | errors |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| sdk-structural | 0/28 | 0/28 | [0.00, 0.12] | 0/28 | 0/28 | 0/28 | 28/28 | 0/28 | 0.0 | 0 | 0 | 0 |
| sdk-structural+trace | 0/28 | 0/28 | [0.00, 0.12] | 0/28 | 0/28 | 28/28 | 28/28 | 0/28 | 0.0 | 0 | 0 | 0 |
| sdk-integrity | 0/28 | 0/28 | [0.00, 0.12] | 0/28 | 0/28 | 0/28 | 28/28 | 0/28 | 0.0 | 0 | 0 | 0 |
| sdk-oracle | 28/28 | 28/28 | [0.88, 1.00] | 28/28 | 28/28 | 28/28 | 28/28 | 28/28 | 0.0 | 0 | 0 | 0 |
| sdk-llmcheck:glm-5p3-flash | 28/28 | 28/28 | [0.88, 1.00] | 28/28 | 28/28 | 28/28 | 28/28 | 28/28 | 21.5 | 41,291 | 234 | 0 |
| sdk-llmcheck+integrity:glm-5p3-flash | 28/28 | 28/28 | [0.88, 1.00] | 28/28 | 28/28 | 28/28 | 28/28 | 28/28 | 21.5 | 41,291 | 234 | 0 |

## Differences from sdk-structural

Step-level accuracy of each method minus that of sdk-structural, on the same cases.

| method | reference only right | method only right | difference [95% bootstrap] | McNemar p |
|---|---|---|---|---|
| sdk-structural+trace | 0 | 0 | +0.00 [+0.00, +0.00] | 1.000 |
| sdk-integrity | 0 | 0 | +0.00 [+0.00, +0.00] | 1.000 |
| sdk-oracle | 0 | 28 | +1.00 [+1.00, +1.00] | 0.000 |
| sdk-llmcheck:glm-5p3-flash | 0 | 28 | +1.00 [+1.00, +1.00] | 0.000 |
| sdk-llmcheck+integrity:glm-5p3-flash | 0 | 28 | +1.00 [+1.00, +1.00] | 0.000 |

## By fault kind (step correct / cases)

| group | sdk-structural | sdk-structural+trace | sdk-integrity | sdk-oracle | sdk-llmcheck:glm-5p3-flash | sdk-llmcheck+integrity:glm-5p3-flash |
|---|---|---|---|---|---|---|
| natural | 0/28 | 0/28 | 0/28 | 28/28 | 28/28 | 28/28 |

## By node of the fault (step correct / cases)

| group | sdk-structural | sdk-structural+trace | sdk-integrity | sdk-oracle | sdk-llmcheck:glm-5p3-flash | sdk-llmcheck+integrity:glm-5p3-flash |
|---|---|---|---|---|---|---|
| - | 0/28 | 0/28 | 0/28 | 28/28 | 28/28 | 28/28 |

## By model that ran the pipeline (step correct / cases)

| group | sdk-structural | sdk-structural+trace | sdk-integrity | sdk-oracle | sdk-llmcheck:glm-5p3-flash | sdk-llmcheck+integrity:glm-5p3-flash |
|---|---|---|---|---|---|---|
| deepseek-v4p1-flash | 0/11 | 0/11 | 0/11 | 11/11 | 11/11 | 11/11 |
| glm-5p3-flash | 0/13 | 0/13 | 0/13 | 13/13 | 13/13 | 13/13 |
| minimax-m3p1-flash | 0/4 | 0/4 | 0/4 | 4/4 | 4/4 | 4/4 |

## By length of the trace (step correct / cases)

| group | sdk-structural | sdk-structural+trace | sdk-integrity | sdk-oracle | sdk-llmcheck:glm-5p3-flash | sdk-llmcheck+integrity:glm-5p3-flash |
|---|---|---|---|---|---|---|
| >12 steps | 0/1 | 0/1 | 0/1 | 1/1 | 1/1 | 1/1 |
| ≤8 steps | 0/27 | 0/27 | 0/27 | 27/27 | 27/27 | 27/27 |

## Fault-free controls

Runs whose final answer is correct, given to the methods that can decline to name a step. The prompts and the SDK call still say the output is wrong (`assume_fault`), as for the failed runs, so a step named here is a false attribution. All-at-once, binary search and FALAT always name a step and are not listed.

| method | fault-free runs | a step named | rate | 95% | errors | tokens per run |
|---|---|---|---|---|---|---|
| sdk-structural | 14 | 0 | 0.00 | [0.00, 0.22] | 0 | 0 |
| sdk-structural+trace | 14 | 0 | 0.00 | [0.00, 0.22] | 0 | 0 |
| sdk-integrity | 14 | 0 | 0.00 | [0.00, 0.22] | 0 | 0 |
| sdk-oracle | 14 | 0 | 0.00 | [0.00, 0.22] | 0 | 0 |
| sdk-llmcheck:glm-5p3-flash | 14 | 0 | 0.00 | [0.00, 0.22] | 0 | 24,110 |
| sdk-llmcheck+integrity:glm-5p3-flash | 14 | 0 | 0.00 | [0.00, 0.22] | 0 | 31,489 |

Model calls made: 1698 (1698 answered from the cache); new tokens sent 0 and received 0; 0 reply(ies) ran out of the token budget and were asked again with a larger one.
