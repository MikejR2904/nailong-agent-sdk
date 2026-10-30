# Root-cause attribution on the derived corpus, dev split

18 failed runs (0 cases in the split were left out: no recorded fault, an incomplete pipeline, or a correct final answer). Agent and step are exact matches; ±1 and ±2 count a step within that many calls of the labelled one; top-3 counts the labelled step among the first three a method ranks (one for methods that rank nothing); 'answered' is how often a method named a step at all; calls, tokens and seconds are per case.

## All cases

| method | agent | step | step 95% | ±1 | ±2 | top-3 | answered | step if answered | calls | tokens | seconds | errors |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| last-step | 0/18 | 0/18 | [0.00, 0.18] | 0/18 | 4/18 | 0/18 | 18/18 | 0/18 | 0.0 | 0 | 0 | 0 |
| first-step | 13/18 | 0/18 | [0.00, 0.18] | 13/18 | 13/18 | 0/18 | 18/18 | 0/18 | 0.0 | 0 | 0 | 0 |
| ww-all:glm-5p3-flash | 18/18 | 18/18 | [0.82, 1.00] | 18/18 | 18/18 | 18/18 | 18/18 | 18/18 | 1.0 | 3,082 | 7 | 0 |
| ww-binary:glm-5p3-flash | 18/18 | 18/18 | [0.82, 1.00] | 18/18 | 18/18 | 18/18 | 18/18 | 18/18 | 3.0 | 7,232 | 15 | 0 |
| falat:glm-5p3-flash | 17/18 | 17/18 | [0.74, 0.99] | 17/18 | 18/18 | 17/18 | 18/18 | 17/18 | 10.4 | 67,489 | 287 | 0 |
| ww-all+gt:glm-5p3-flash | 18/18 | 18/18 | [0.82, 1.00] | 18/18 | 18/18 | 18/18 | 18/18 | 18/18 | 1.0 | 3,361 | 8 | 0 |
| ww-binary+gt:glm-5p3-flash | 18/18 | 18/18 | [0.82, 1.00] | 18/18 | 18/18 | 18/18 | 18/18 | 18/18 | 3.0 | 8,451 | 20 | 0 |
| ww-all:deepseek-v4p1-flash | 16/18 | 16/18 | [0.67, 0.97] | 16/18 | 16/18 | 16/18 | 18/18 | 16/18 | 1.3 | 10,069 | 48 | 0 |
| ww-binary:deepseek-v4p1-flash | 18/18 | 18/18 | [0.82, 1.00] | 18/18 | 18/18 | 18/18 | 18/18 | 18/18 | 3.1 | 9,076 | 28 | 0 |
| falat:deepseek-v4p1-flash | 18/18 | 18/18 | [0.82, 1.00] | 18/18 | 18/18 | 18/18 | 18/18 | 18/18 | 9.8 | 57,250 | 270 | 0 |
| ww-all+gt:deepseek-v4p1-flash | 18/18 | 18/18 | [0.82, 1.00] | 18/18 | 18/18 | 18/18 | 18/18 | 18/18 | 1.1 | 7,704 | 36 | 0 |
| ww-binary+gt:deepseek-v4p1-flash | 17/18 | 17/18 | [0.74, 0.99] | 18/18 | 18/18 | 17/18 | 18/18 | 17/18 | 3.1 | 8,245 | 23 | 0 |
| ww-step:glm-5p3-flash | 18/18 | 18/18 | [0.82, 1.00] | 18/18 | 18/18 | 18/18 | 18/18 | 18/18 | 2.6 | 4,490 | 11 | 0 |
| ww-step+gt:glm-5p3-flash | 18/18 | 18/18 | [0.82, 1.00] | 18/18 | 18/18 | 18/18 | 18/18 | 18/18 | 2.6 | 6,059 | 16 | 0 |
| sdk-structural | 0/18 | 0/18 | [0.00, 0.18] | 0/18 | 4/18 | 0/18 | 18/18 | 0/18 | 0.0 | 0 | 0 | 0 |
| sdk-structural+trace | 0/18 | 0/18 | [0.00, 0.18] | 0/18 | 4/18 | 18/18 | 18/18 | 0/18 | 0.0 | 0 | 0 | 0 |
| sdk-integrity | 0/18 | 0/18 | [0.00, 0.18] | 0/18 | 4/18 | 0/18 | 18/18 | 0/18 | 0.0 | 0 | 0 | 0 |
| sdk-oracle | 18/18 | 18/18 | [0.82, 1.00] | 18/18 | 18/18 | 18/18 | 18/18 | 18/18 | 0.0 | 0 | 0 | 0 |
| sdk-llmcheck:glm-5p3-flash | 13/18 | 13/18 | [0.49, 0.88] | 13/18 | 16/18 | 13/18 | 16/18 | 13/16 | 19.7 | 62,056 | 241 | 0 |
| sdk-llmcheck+integrity:glm-5p3-flash | 13/18 | 13/18 | [0.49, 0.88] | 13/18 | 14/18 | 13/18 | 14/18 | 13/14 | 20.5 | 63,170 | 243 | 0 |

## Differences from sdk-structural

Step-level accuracy of each method minus that of sdk-structural, on the same cases.

| method | reference only right | method only right | difference [95% bootstrap] | McNemar p |
|---|---|---|---|---|
| last-step | 0 | 0 | +0.00 [+0.00, +0.00] | 1.000 |
| first-step | 0 | 0 | +0.00 [+0.00, +0.00] | 1.000 |
| ww-all:glm-5p3-flash | 0 | 18 | +1.00 [+1.00, +1.00] | 0.000 |
| ww-binary:glm-5p3-flash | 0 | 18 | +1.00 [+1.00, +1.00] | 0.000 |
| falat:glm-5p3-flash | 0 | 17 | +0.94 [+0.83, +1.00] | 0.000 |
| ww-all+gt:glm-5p3-flash | 0 | 18 | +1.00 [+1.00, +1.00] | 0.000 |
| ww-binary+gt:glm-5p3-flash | 0 | 18 | +1.00 [+1.00, +1.00] | 0.000 |
| ww-all:deepseek-v4p1-flash | 0 | 16 | +0.89 [+0.72, +1.00] | 0.000 |
| ww-binary:deepseek-v4p1-flash | 0 | 18 | +1.00 [+1.00, +1.00] | 0.000 |
| falat:deepseek-v4p1-flash | 0 | 18 | +1.00 [+1.00, +1.00] | 0.000 |
| ww-all+gt:deepseek-v4p1-flash | 0 | 18 | +1.00 [+1.00, +1.00] | 0.000 |
| ww-binary+gt:deepseek-v4p1-flash | 0 | 17 | +0.94 [+0.83, +1.00] | 0.000 |
| ww-step:glm-5p3-flash | 0 | 18 | +1.00 [+1.00, +1.00] | 0.000 |
| ww-step+gt:glm-5p3-flash | 0 | 18 | +1.00 [+1.00, +1.00] | 0.000 |
| sdk-structural+trace | 0 | 0 | +0.00 [+0.00, +0.00] | 1.000 |
| sdk-integrity | 0 | 0 | +0.00 [+0.00, +0.00] | 1.000 |
| sdk-oracle | 0 | 18 | +1.00 [+1.00, +1.00] | 0.000 |
| sdk-llmcheck:glm-5p3-flash | 0 | 13 | +0.72 [+0.50, +0.89] | 0.000 |
| sdk-llmcheck+integrity:glm-5p3-flash | 0 | 13 | +0.72 [+0.50, +0.89] | 0.000 |

## By fault kind (step correct / cases)

| group | last-step | first-step | ww-all:glm-5p3-flash | ww-binary:glm-5p3-flash | falat:glm-5p3-flash | ww-all+gt:glm-5p3-flash | ww-binary+gt:glm-5p3-flash | ww-all:deepseek-v4p1-flash | ww-binary:deepseek-v4p1-flash | falat:deepseek-v4p1-flash | ww-all+gt:deepseek-v4p1-flash | ww-binary+gt:deepseek-v4p1-flash | ww-step:glm-5p3-flash | ww-step+gt:glm-5p3-flash | sdk-structural | sdk-structural+trace | sdk-integrity | sdk-oracle | sdk-llmcheck:glm-5p3-flash | sdk-llmcheck+integrity:glm-5p3-flash |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| natural | 0/18 | 0/18 | 18/18 | 18/18 | 17/18 | 18/18 | 18/18 | 16/18 | 18/18 | 18/18 | 18/18 | 17/18 | 18/18 | 18/18 | 0/18 | 0/18 | 0/18 | 18/18 | 13/18 | 13/18 |

## By node of the fault (step correct / cases)

| group | last-step | first-step | ww-all:glm-5p3-flash | ww-binary:glm-5p3-flash | falat:glm-5p3-flash | ww-all+gt:glm-5p3-flash | ww-binary+gt:glm-5p3-flash | ww-all:deepseek-v4p1-flash | ww-binary:deepseek-v4p1-flash | falat:deepseek-v4p1-flash | ww-all+gt:deepseek-v4p1-flash | ww-binary+gt:deepseek-v4p1-flash | ww-step:glm-5p3-flash | ww-step+gt:glm-5p3-flash | sdk-structural | sdk-structural+trace | sdk-integrity | sdk-oracle | sdk-llmcheck:glm-5p3-flash | sdk-llmcheck+integrity:glm-5p3-flash |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| - | 0/18 | 0/18 | 18/18 | 18/18 | 17/18 | 18/18 | 18/18 | 16/18 | 18/18 | 18/18 | 18/18 | 17/18 | 18/18 | 18/18 | 0/18 | 0/18 | 0/18 | 18/18 | 13/18 | 13/18 |

## By model that ran the pipeline (step correct / cases)

| group | last-step | first-step | ww-all:glm-5p3-flash | ww-binary:glm-5p3-flash | falat:glm-5p3-flash | ww-all+gt:glm-5p3-flash | ww-binary+gt:glm-5p3-flash | ww-all:deepseek-v4p1-flash | ww-binary:deepseek-v4p1-flash | falat:deepseek-v4p1-flash | ww-all+gt:deepseek-v4p1-flash | ww-binary+gt:deepseek-v4p1-flash | ww-step:glm-5p3-flash | ww-step+gt:glm-5p3-flash | sdk-structural | sdk-structural+trace | sdk-integrity | sdk-oracle | sdk-llmcheck:glm-5p3-flash | sdk-llmcheck+integrity:glm-5p3-flash |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| deepseek-v4p1-flash | 0/6 | 0/6 | 6/6 | 6/6 | 6/6 | 6/6 | 6/6 | 5/6 | 6/6 | 6/6 | 6/6 | 6/6 | 6/6 | 6/6 | 0/6 | 0/6 | 0/6 | 6/6 | 6/6 | 6/6 |
| glm-5p3-flash | 0/6 | 0/6 | 6/6 | 6/6 | 5/6 | 6/6 | 6/6 | 5/6 | 6/6 | 6/6 | 6/6 | 6/6 | 6/6 | 6/6 | 0/6 | 0/6 | 0/6 | 6/6 | 6/6 | 6/6 |
| minimax-m3p1-flash | 0/6 | 0/6 | 6/6 | 6/6 | 6/6 | 6/6 | 6/6 | 6/6 | 6/6 | 6/6 | 6/6 | 5/6 | 6/6 | 6/6 | 0/6 | 0/6 | 0/6 | 6/6 | 1/6 | 1/6 |

## By length of the trace (step correct / cases)

| group | last-step | first-step | ww-all:glm-5p3-flash | ww-binary:glm-5p3-flash | falat:glm-5p3-flash | ww-all+gt:glm-5p3-flash | ww-binary+gt:glm-5p3-flash | ww-all:deepseek-v4p1-flash | ww-binary:deepseek-v4p1-flash | falat:deepseek-v4p1-flash | ww-all+gt:deepseek-v4p1-flash | ww-binary+gt:deepseek-v4p1-flash | ww-step:glm-5p3-flash | ww-step+gt:glm-5p3-flash | sdk-structural | sdk-structural+trace | sdk-integrity | sdk-oracle | sdk-llmcheck:glm-5p3-flash | sdk-llmcheck+integrity:glm-5p3-flash |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| ≤8 steps | 0/18 | 0/18 | 18/18 | 18/18 | 17/18 | 18/18 | 18/18 | 16/18 | 18/18 | 18/18 | 18/18 | 17/18 | 18/18 | 18/18 | 0/18 | 0/18 | 0/18 | 18/18 | 13/18 | 13/18 |

Model calls made: 1476 (1049 answered from the cache); new tokens sent 556,744 and received 1,455,843; 50 reply(ies) ran out of the token budget and were asked again with a larger one.
