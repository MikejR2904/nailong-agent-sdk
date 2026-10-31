# Root-cause attribution on the planted-derived corpus, dev split

36 failed runs (6 cases in the split were left out: no recorded fault, an incomplete pipeline, or a correct final answer). Agent and step are exact matches; ±1 and ±2 count a step within that many calls of the labelled one; top-3 counts the labelled step among the first three a method ranks (one for methods that rank nothing); 'answered' is how often a method named a step at all; calls, tokens and seconds are per case.

## All cases

| method | agent | step | step 95% | ±1 | ±2 | top-3 | answered | step if answered | calls | tokens | seconds | errors |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| last-step | 12/36 | 6/36 | [0.08, 0.32] | 12/36 | 18/36 | 6/36 | 36/36 | 6/36 | 0.0 | 0 | 0 | 0 |
| first-step | 12/36 | 6/36 | [0.08, 0.32] | 12/36 | 18/36 | 6/36 | 36/36 | 6/36 | 0.0 | 0 | 0 | 0 |
| ww-all:glm-5p3-flash | 10/36 | 4/36 | [0.04, 0.25] | 16/36 | 20/36 | 4/36 | 30/36 | 4/30 | 0.9 | 5,094 | 24 | 6 |
| ww-binary:glm-5p3-flash | 11/36 | 7/36 | [0.10, 0.35] | 15/36 | 19/36 | 7/36 | 29/36 | 7/29 | 2.6 | 11,031 | 57 | 6 |
| falat:glm-5p3-flash | 11/36 | 5/36 | [0.06, 0.29] | 16/36 | 19/36 | 19/36 | 28/36 | 5/28 | 9.3 | 73,229 | 402 | 8 |
| ww-all+gt:glm-5p3-flash | 12/36 | 5/36 | [0.06, 0.29] | 17/36 | 21/36 | 5/36 | 29/36 | 5/29 | 0.9 | 5,826 | 27 | 6 |
| ww-binary+gt:glm-5p3-flash | 12/36 | 8/36 | [0.12, 0.38] | 15/36 | 19/36 | 8/36 | 27/36 | 8/27 | 2.4 | 11,042 | 51 | 6 |
| ww-all:deepseek-v4p1-flash | 9/36 | 3/36 | [0.03, 0.22] | 15/36 | 17/36 | 3/36 | 26/36 | 3/26 | 1.3 | 14,755 | 67 | 6 |
| ww-binary:deepseek-v4p1-flash | 9/36 | 3/36 | [0.03, 0.22] | 15/36 | 20/36 | 3/36 | 30/36 | 3/30 | 2.9 | 13,824 | 56 | 6 |
| falat:deepseek-v4p1-flash | 13/36 | 5/36 | [0.06, 0.29] | 16/36 | 20/36 | 16/36 | 28/36 | 5/28 | 8.8 | 61,017 | 288 | 8 |
| ww-all+gt:deepseek-v4p1-flash | 9/36 | 4/36 | [0.04, 0.25] | 14/36 | 17/36 | 4/36 | 25/36 | 4/25 | 1.4 | 14,684 | 64 | 6 |
| ww-binary+gt:deepseek-v4p1-flash | 9/36 | 5/36 | [0.06, 0.29] | 13/36 | 17/36 | 5/36 | 26/36 | 5/26 | 2.6 | 13,490 | 49 | 7 |
| ww-step:glm-5p3-flash | 10/36 | 6/36 | [0.08, 0.32] | 15/36 | 19/36 | 6/36 | 29/36 | 6/29 | 2.1 | 3,481 | 10 | 7 |
| ww-step+gt:glm-5p3-flash | 12/36 | 9/36 | [0.14, 0.41] | 17/36 | 21/36 | 9/36 | 29/36 | 9/29 | 2.0 | 5,546 | 22 | 7 |
| sdk-structural | 12/36 | 6/36 | [0.08, 0.32] | 12/36 | 18/36 | 6/36 | 36/36 | 6/36 | 0.0 | 0 | 0 | 0 |
| sdk-structural+trace | 12/36 | 6/36 | [0.08, 0.32] | 12/36 | 18/36 | 18/36 | 36/36 | 6/36 | 0.0 | 0 | 0 | 0 |
| sdk-integrity | 36/36 | 36/36 | [0.90, 1.00] | 36/36 | 36/36 | 36/36 | 36/36 | 36/36 | 0.0 | 0 | 0 | 0 |
| sdk-oracle | 16/36 | 10/36 | [0.16, 0.44] | 22/36 | 27/36 | 22/36 | 36/36 | 10/36 | 0.0 | 0 | 0 | 0 |
| sdk-llmcheck:glm-5p3-flash | 12/36 | 6/36 | [0.08, 0.32] | 17/36 | 21/36 | 15/36 | 29/36 | 6/29 | 17.0 | 40,085 | 217 | 7 |
| sdk-llmcheck+integrity:glm-5p3-flash | 29/36 | 29/36 | [0.65, 0.90] | 29/36 | 29/36 | 29/36 | 29/36 | 29/29 | 17.0 | 40,066 | 218 | 7 |

## Differences from sdk-structural

Step-level accuracy of each method minus that of sdk-structural, on the same cases.

| method | reference only right | method only right | difference [95% bootstrap] | McNemar p |
|---|---|---|---|---|
| last-step | 0 | 0 | +0.00 [+0.00, +0.00] | 1.000 |
| first-step | 6 | 6 | +0.00 [-0.19, +0.19] | 1.000 |
| ww-all:glm-5p3-flash | 6 | 4 | -0.06 [-0.22, +0.11] | 0.754 |
| ww-binary:glm-5p3-flash | 6 | 7 | +0.03 [-0.17, +0.22] | 1.000 |
| falat:glm-5p3-flash | 6 | 5 | -0.03 [-0.22, +0.14] | 1.000 |
| ww-all+gt:glm-5p3-flash | 6 | 5 | -0.03 [-0.22, +0.14] | 1.000 |
| ww-binary+gt:glm-5p3-flash | 6 | 8 | +0.06 [-0.14, +0.25] | 0.791 |
| ww-all:deepseek-v4p1-flash | 5 | 2 | -0.08 [-0.22, +0.06] | 0.453 |
| ww-binary:deepseek-v4p1-flash | 6 | 3 | -0.08 [-0.25, +0.08] | 0.508 |
| falat:deepseek-v4p1-flash | 6 | 5 | -0.03 [-0.19, +0.14] | 1.000 |
| ww-all+gt:deepseek-v4p1-flash | 6 | 4 | -0.06 [-0.22, +0.11] | 0.754 |
| ww-binary+gt:deepseek-v4p1-flash | 6 | 5 | -0.03 [-0.22, +0.14] | 1.000 |
| ww-step:glm-5p3-flash | 6 | 6 | +0.00 [-0.19, +0.19] | 1.000 |
| ww-step+gt:glm-5p3-flash | 6 | 9 | +0.08 [-0.14, +0.31] | 0.607 |
| sdk-structural+trace | 0 | 0 | +0.00 [+0.00, +0.00] | 1.000 |
| sdk-integrity | 0 | 30 | +0.83 [+0.69, +0.94] | 0.000 |
| sdk-oracle | 6 | 10 | +0.11 [-0.11, +0.31] | 0.454 |
| sdk-llmcheck:glm-5p3-flash | 6 | 6 | +0.00 [-0.19, +0.19] | 1.000 |
| sdk-llmcheck+integrity:glm-5p3-flash | 2 | 25 | +0.64 [+0.44, +0.83] | 0.000 |

## By fault kind (step correct / cases)

| group | last-step | first-step | ww-all:glm-5p3-flash | ww-binary:glm-5p3-flash | falat:glm-5p3-flash | ww-all+gt:glm-5p3-flash | ww-binary+gt:glm-5p3-flash | ww-all:deepseek-v4p1-flash | ww-binary:deepseek-v4p1-flash | falat:deepseek-v4p1-flash | ww-all+gt:deepseek-v4p1-flash | ww-binary+gt:deepseek-v4p1-flash | ww-step:glm-5p3-flash | ww-step+gt:glm-5p3-flash | sdk-structural | sdk-structural+trace | sdk-integrity | sdk-oracle | sdk-llmcheck:glm-5p3-flash | sdk-llmcheck+integrity:glm-5p3-flash |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| read | 0/18 | 6/18 | 0/18 | 4/18 | 1/18 | 1/18 | 4/18 | 0/18 | 0/18 | 1/18 | 0/18 | 2/18 | 2/18 | 5/18 | 0/18 | 0/18 | 18/18 | 2/18 | 2/18 | 17/18 |
| write | 6/18 | 0/18 | 4/18 | 3/18 | 4/18 | 4/18 | 4/18 | 3/18 | 3/18 | 4/18 | 4/18 | 3/18 | 4/18 | 4/18 | 6/18 | 6/18 | 18/18 | 8/18 | 4/18 | 12/18 |

## By node of the fault (step correct / cases)

| group | last-step | first-step | ww-all:glm-5p3-flash | ww-binary:glm-5p3-flash | falat:glm-5p3-flash | ww-all+gt:glm-5p3-flash | ww-binary+gt:glm-5p3-flash | ww-all:deepseek-v4p1-flash | ww-binary:deepseek-v4p1-flash | falat:deepseek-v4p1-flash | ww-all+gt:deepseek-v4p1-flash | ww-binary+gt:deepseek-v4p1-flash | ww-step:glm-5p3-flash | ww-step+gt:glm-5p3-flash | sdk-structural | sdk-structural+trace | sdk-integrity | sdk-oracle | sdk-llmcheck:glm-5p3-flash | sdk-llmcheck+integrity:glm-5p3-flash |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A | 0/12 | 6/12 | 4/12 | 3/12 | 4/12 | 4/12 | 4/12 | 2/12 | 3/12 | 5/12 | 4/12 | 4/12 | 4/12 | 7/12 | 0/12 | 0/12 | 12/12 | 6/12 | 4/12 | 10/12 |
| B | 0/12 | 0/12 | 0/12 | 2/12 | 1/12 | 1/12 | 2/12 | 0/12 | 0/12 | 0/12 | 0/12 | 1/12 | 2/12 | 2/12 | 0/12 | 0/12 | 12/12 | 4/12 | 2/12 | 10/12 |
| C | 6/12 | 0/12 | 0/12 | 2/12 | 0/12 | 0/12 | 2/12 | 1/12 | 0/12 | 0/12 | 0/12 | 0/12 | 0/12 | 0/12 | 6/12 | 6/12 | 12/12 | 0/12 | 0/12 | 9/12 |

## By model that ran the pipeline (step correct / cases)

| group | last-step | first-step | ww-all:glm-5p3-flash | ww-binary:glm-5p3-flash | falat:glm-5p3-flash | ww-all+gt:glm-5p3-flash | ww-binary+gt:glm-5p3-flash | ww-all:deepseek-v4p1-flash | ww-binary:deepseek-v4p1-flash | falat:deepseek-v4p1-flash | ww-all+gt:deepseek-v4p1-flash | ww-binary+gt:deepseek-v4p1-flash | ww-step:glm-5p3-flash | ww-step+gt:glm-5p3-flash | sdk-structural | sdk-structural+trace | sdk-integrity | sdk-oracle | sdk-llmcheck:glm-5p3-flash | sdk-llmcheck+integrity:glm-5p3-flash |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| deepseek-v4p1-flash | 2/12 | 2/12 | 2/12 | 2/12 | 2/12 | 2/12 | 2/12 | 2/12 | 2/12 | 2/12 | 2/12 | 1/12 | 2/12 | 3/12 | 2/12 | 2/12 | 12/12 | 2/12 | 2/12 | 12/12 |
| glm-5p3-flash | 2/12 | 2/12 | 2/12 | 1/12 | 2/12 | 2/12 | 2/12 | 1/12 | 1/12 | 2/12 | 2/12 | 2/12 | 2/12 | 3/12 | 2/12 | 2/12 | 12/12 | 2/12 | 2/12 | 12/12 |
| minimax-m3p1-flash | 2/12 | 2/12 | 0/12 | 4/12 | 1/12 | 1/12 | 4/12 | 0/12 | 0/12 | 1/12 | 0/12 | 2/12 | 2/12 | 3/12 | 2/12 | 2/12 | 12/12 | 6/12 | 2/12 | 5/12 |

## By length of the trace (step correct / cases)

| group | last-step | first-step | ww-all:glm-5p3-flash | ww-binary:glm-5p3-flash | falat:glm-5p3-flash | ww-all+gt:glm-5p3-flash | ww-binary+gt:glm-5p3-flash | ww-all:deepseek-v4p1-flash | ww-binary:deepseek-v4p1-flash | falat:deepseek-v4p1-flash | ww-all+gt:deepseek-v4p1-flash | ww-binary+gt:deepseek-v4p1-flash | ww-step:glm-5p3-flash | ww-step+gt:glm-5p3-flash | sdk-structural | sdk-structural+trace | sdk-integrity | sdk-oracle | sdk-llmcheck:glm-5p3-flash | sdk-llmcheck+integrity:glm-5p3-flash |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 9-12 steps | 0/1 | 0/1 | 0/1 | 0/1 | 0/1 | 0/1 | 0/1 | 0/1 | 0/1 | 0/1 | 0/1 | 0/1 | 0/1 | 0/1 | 0/1 | 0/1 | 1/1 | 1/1 | 0/1 | 0/1 |
| ≤8 steps | 6/35 | 6/35 | 4/35 | 7/35 | 5/35 | 5/35 | 8/35 | 3/35 | 3/35 | 5/35 | 4/35 | 5/35 | 6/35 | 9/35 | 6/35 | 6/35 | 35/35 | 9/35 | 6/35 | 29/35 |

Model calls made: 2568 (1721 answered from the cache); new tokens sent 1,143,510 and received 5,018,565; 206 reply(ies) ran out of the token budget and were asked again with a larger one.
