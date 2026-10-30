# Root-cause attribution on the natural corpus, dev split

28 failed runs (14 cases in the split were left out: no recorded fault, an incomplete pipeline, or a correct final answer). Agent and step are exact matches; ±1 and ±2 count a step within that many calls of the labelled one; top-3 counts the labelled step among the first three a method ranks (one for methods that rank nothing); 'answered' is how often a method named a step at all; calls, tokens and seconds are per case.

## All cases

| method | agent | step | step 95% | ±1 | ±2 | top-3 | answered | step if answered | calls | tokens | seconds | errors |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| ww-step:glm-5p3-flash | 28/28 | 27/28 | [0.82, 0.99] | 27/28 | 27/28 | 27/28 | 28/28 | 27/28 | 2.0 | 2,809 | 6 | 0 |
| sdk-structural | 0/28 | 0/28 | [0.00, 0.12] | 0/28 | 0/28 | 0/28 | 28/28 | 0/28 | 0.0 | 0 | 0 | 0 |
| sdk-structural+trace | 0/28 | 0/28 | [0.00, 0.12] | 0/28 | 0/28 | 27/28 | 28/28 | 0/28 | 0.0 | 0 | 0 | 0 |
| sdk-integrity | 0/28 | 0/28 | [0.00, 0.12] | 0/28 | 0/28 | 0/28 | 28/28 | 0/28 | 0.0 | 0 | 0 | 0 |
| sdk-oracle | 27/28 | 27/28 | [0.82, 0.99] | 27/28 | 27/28 | 28/28 | 28/28 | 27/28 | 0.0 | 0 | 0 | 0 |
| sdk-llmcheck:glm-5p3-flash | 27/28 | 27/28 | [0.82, 0.99] | 27/28 | 27/28 | 28/28 | 28/28 | 27/28 | 21.1 | 49,265 | 192 | 0 |
| sdk-llmcheck+integrity:glm-5p3-flash | 27/28 | 27/28 | [0.82, 0.99] | 28/28 | 28/28 | 28/28 | 28/28 | 27/28 | 21.2 | 49,541 | 193 | 0 |

## Differences from sdk-structural

Step-level accuracy of each method minus that of sdk-structural, on the same cases.

| method | reference only right | method only right | difference [95% bootstrap] | McNemar p |
|---|---|---|---|---|
| ww-step:glm-5p3-flash | 0 | 27 | +0.96 [+0.89, +1.00] | 0.000 |
| sdk-structural+trace | 0 | 0 | +0.00 [+0.00, +0.00] | 1.000 |
| sdk-integrity | 0 | 0 | +0.00 [+0.00, +0.00] | 1.000 |
| sdk-oracle | 0 | 27 | +0.96 [+0.89, +1.00] | 0.000 |
| sdk-llmcheck:glm-5p3-flash | 0 | 27 | +0.96 [+0.89, +1.00] | 0.000 |
| sdk-llmcheck+integrity:glm-5p3-flash | 0 | 27 | +0.96 [+0.89, +1.00] | 0.000 |

## By fault kind (step correct / cases)

| group | ww-step:glm-5p3-flash | sdk-structural | sdk-structural+trace | sdk-integrity | sdk-oracle | sdk-llmcheck:glm-5p3-flash | sdk-llmcheck+integrity:glm-5p3-flash |
|---|---|---|---|---|---|---|---|
| natural | 27/28 | 0/28 | 0/28 | 0/28 | 27/28 | 27/28 | 27/28 |

## By node of the fault (step correct / cases)

| group | ww-step:glm-5p3-flash | sdk-structural | sdk-structural+trace | sdk-integrity | sdk-oracle | sdk-llmcheck:glm-5p3-flash | sdk-llmcheck+integrity:glm-5p3-flash |
|---|---|---|---|---|---|---|---|
| - | 27/28 | 0/28 | 0/28 | 0/28 | 27/28 | 27/28 | 27/28 |

## By model that ran the pipeline (step correct / cases)

| group | ww-step:glm-5p3-flash | sdk-structural | sdk-structural+trace | sdk-integrity | sdk-oracle | sdk-llmcheck:glm-5p3-flash | sdk-llmcheck+integrity:glm-5p3-flash |
|---|---|---|---|---|---|---|---|
| deepseek-v4p1-flash | 11/11 | 0/11 | 0/11 | 0/11 | 11/11 | 11/11 | 11/11 |
| glm-5p3-flash | 13/13 | 0/13 | 0/13 | 0/13 | 13/13 | 13/13 | 13/13 |
| minimax-m3p1-flash | 3/4 | 0/4 | 0/4 | 0/4 | 3/4 | 3/4 | 3/4 |

## By length of the trace (step correct / cases)

| group | ww-step:glm-5p3-flash | sdk-structural | sdk-structural+trace | sdk-integrity | sdk-oracle | sdk-llmcheck:glm-5p3-flash | sdk-llmcheck+integrity:glm-5p3-flash |
|---|---|---|---|---|---|---|---|
| >12 steps | 0/1 | 0/1 | 0/1 | 0/1 | 1/1 | 1/1 | 1/1 |
| ≤8 steps | 27/27 | 0/27 | 0/27 | 0/27 | 26/27 | 26/27 | 26/27 |

## Fault-free controls

Runs whose final answer is correct, given to the methods that can decline to name a step. The prompts and the SDK call still say the output is wrong (`assume_fault`), as for the failed runs, so a step named here is a false attribution. All-at-once, binary search and FALAT always name a step and are not listed.

| method | fault-free runs | a step named | rate | 95% | errors | tokens per run |
|---|---|---|---|---|---|---|
| ww-step:glm-5p3-flash | 14 | 12 | 0.86 | [0.60, 0.96] | 0 | 7,705 |
| sdk-structural | 14 | 0 | 0.00 | [0.00, 0.22] | 0 | 0 |
| sdk-structural+trace | 14 | 0 | 0.00 | [0.00, 0.22] | 0 | 0 |
| sdk-integrity | 14 | 0 | 0.00 | [0.00, 0.22] | 0 | 0 |
| sdk-oracle | 14 | 0 | 0.00 | [0.00, 0.22] | 0 | 0 |
| sdk-llmcheck:glm-5p3-flash | 14 | 0 | 0.00 | [0.00, 0.22] | 0 | 24,571 |
| sdk-llmcheck+integrity:glm-5p3-flash | 14 | 0 | 0.00 | [0.00, 0.22] | 0 | 33,514 |

Model calls made: 1783 (1526 answered from the cache); new tokens sent 136,857 and received 312,443; 0 reply(ies) ran out of the token budget and were asked again with a larger one.
