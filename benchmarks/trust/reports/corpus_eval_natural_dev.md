# Root-cause attribution on the natural corpus, dev split

28 failed runs (14 cases in the split were left out: no recorded fault, an incomplete pipeline, or a correct final answer). Agent and step are exact matches; ±1 and ±2 count a step within that many calls of the labelled one; top-3 counts the labelled step among the first three a method ranks (one for methods that rank nothing); 'answered' is how often a method named a step at all; calls, tokens and seconds are per case.

## All cases

| method | agent | step | step 95% | ±1 | ±2 | top-3 | answered | step if answered | calls | tokens | seconds | errors |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| last-step | 0/28 | 0/28 | [0.00, 0.12] | 0/28 | 0/28 | 0/28 | 28/28 | 0/28 | 0.0 | 0 | 0 | 0 |
| first-step | 28/28 | 0/28 | [0.00, 0.12] | 27/28 | 27/28 | 0/28 | 28/28 | 0/28 | 0.0 | 0 | 0 | 0 |
| ww-all:glm-5p3-flash | 28/28 | 28/28 | [0.88, 1.00] | 28/28 | 28/28 | 28/28 | 28/28 | 28/28 | 1.0 | 2,331 | 4 | 0 |
| ww-binary:glm-5p3-flash | 28/28 | 28/28 | [0.88, 1.00] | 28/28 | 28/28 | 28/28 | 28/28 | 28/28 | 3.0 | 5,243 | 9 | 0 |
| falat:glm-5p3-flash | 25/28 | 24/28 | [0.69, 0.94] | 24/28 | 24/28 | 24/28 | 28/28 | 24/28 | 10.6 | 68,696 | 304 | 0 |
| ww-all+gt:glm-5p3-flash | 28/28 | 28/28 | [0.88, 1.00] | 28/28 | 28/28 | 28/28 | 28/28 | 28/28 | 1.0 | 2,359 | 4 | 0 |
| ww-binary+gt:glm-5p3-flash | 28/28 | 28/28 | [0.88, 1.00] | 28/28 | 28/28 | 28/28 | 28/28 | 28/28 | 3.0 | 5,901 | 11 | 0 |
| ww-all:deepseek-v4p1-flash | 28/28 | 28/28 | [0.88, 1.00] | 28/28 | 28/28 | 28/28 | 28/28 | 28/28 | 1.0 | 2,013 | 4 | 0 |
| ww-binary:deepseek-v4p1-flash | 28/28 | 28/28 | [0.88, 1.00] | 28/28 | 28/28 | 28/28 | 28/28 | 28/28 | 3.0 | 4,721 | 9 | 0 |
| falat:deepseek-v4p1-flash | 27/28 | 27/28 | [0.82, 0.99] | 27/28 | 27/28 | 27/28 | 27/28 | 27/27 | 9.5 | 48,077 | 259 | 1 |
| ww-all+gt:deepseek-v4p1-flash | 28/28 | 28/28 | [0.88, 1.00] | 28/28 | 28/28 | 28/28 | 28/28 | 28/28 | 1.0 | 2,113 | 5 | 0 |
| ww-binary+gt:deepseek-v4p1-flash | 28/28 | 28/28 | [0.88, 1.00] | 28/28 | 28/28 | 28/28 | 28/28 | 28/28 | 3.0 | 5,013 | 11 | 0 |
| ww-step:glm-5p3-flash | 28/28 | 27/28 | [0.82, 0.99] | 27/28 | 27/28 | 27/28 | 28/28 | 27/28 | 2.0 | 2,804 | 6 | 0 |
| ww-step+gt:glm-5p3-flash | 28/28 | 27/28 | [0.82, 0.99] | 27/28 | 27/28 | 27/28 | 28/28 | 27/28 | 2.0 | 3,730 | 10 | 0 |
| sdk-structural | 0/28 | 0/28 | [0.00, 0.12] | 0/28 | 0/28 | 0/28 | 28/28 | 0/28 | 0.0 | 0 | 0 | 0 |
| sdk-integrity | 0/28 | 0/28 | [0.00, 0.12] | 0/28 | 0/28 | 0/28 | 28/28 | 0/28 | 0.0 | 0 | 0 | 0 |
| sdk-oracle | 27/28 | 27/28 | [0.82, 0.99] | 27/28 | 27/28 | 28/28 | 28/28 | 27/28 | 0.0 | 0 | 0 | 0 |
| sdk-llmcheck:glm-5p3-flash | 22/28 | 22/28 | [0.60, 0.90] | 22/28 | 27/28 | 24/28 | 28/28 | 22/28 | 6.8 | 9,679 | 35 | 0 |
| sdk-llmcheck+integrity:glm-5p3-flash | 24/28 | 24/28 | [0.69, 0.94] | 25/28 | 28/28 | 24/28 | 28/28 | 24/28 | 7.0 | 9,066 | 31 | 0 |

## Differences from sdk-structural

Step-level accuracy of each method minus that of sdk-structural, on the same cases.

| method | reference only right | method only right | difference [95% bootstrap] | McNemar p |
|---|---|---|---|---|
| last-step | 0 | 0 | +0.00 [+0.00, +0.00] | 1.000 |
| first-step | 0 | 0 | +0.00 [+0.00, +0.00] | 1.000 |
| ww-all:glm-5p3-flash | 0 | 28 | +1.00 [+1.00, +1.00] | 0.000 |
| ww-binary:glm-5p3-flash | 0 | 28 | +1.00 [+1.00, +1.00] | 0.000 |
| falat:glm-5p3-flash | 0 | 24 | +0.86 [+0.71, +0.96] | 0.000 |
| ww-all+gt:glm-5p3-flash | 0 | 28 | +1.00 [+1.00, +1.00] | 0.000 |
| ww-binary+gt:glm-5p3-flash | 0 | 28 | +1.00 [+1.00, +1.00] | 0.000 |
| ww-all:deepseek-v4p1-flash | 0 | 28 | +1.00 [+1.00, +1.00] | 0.000 |
| ww-binary:deepseek-v4p1-flash | 0 | 28 | +1.00 [+1.00, +1.00] | 0.000 |
| falat:deepseek-v4p1-flash | 0 | 27 | +0.96 [+0.89, +1.00] | 0.000 |
| ww-all+gt:deepseek-v4p1-flash | 0 | 28 | +1.00 [+1.00, +1.00] | 0.000 |
| ww-binary+gt:deepseek-v4p1-flash | 0 | 28 | +1.00 [+1.00, +1.00] | 0.000 |
| ww-step:glm-5p3-flash | 0 | 27 | +0.96 [+0.89, +1.00] | 0.000 |
| ww-step+gt:glm-5p3-flash | 0 | 27 | +0.96 [+0.89, +1.00] | 0.000 |
| sdk-integrity | 0 | 0 | +0.00 [+0.00, +0.00] | 1.000 |
| sdk-oracle | 0 | 27 | +0.96 [+0.89, +1.00] | 0.000 |
| sdk-llmcheck:glm-5p3-flash | 0 | 22 | +0.79 [+0.64, +0.93] | 0.000 |
| sdk-llmcheck+integrity:glm-5p3-flash | 0 | 24 | +0.86 [+0.71, +0.96] | 0.000 |

## By fault kind (step correct / cases)

| group | last-step | first-step | ww-all:glm-5p3-flash | ww-binary:glm-5p3-flash | falat:glm-5p3-flash | ww-all+gt:glm-5p3-flash | ww-binary+gt:glm-5p3-flash | ww-all:deepseek-v4p1-flash | ww-binary:deepseek-v4p1-flash | falat:deepseek-v4p1-flash | ww-all+gt:deepseek-v4p1-flash | ww-binary+gt:deepseek-v4p1-flash | ww-step:glm-5p3-flash | ww-step+gt:glm-5p3-flash | sdk-structural | sdk-integrity | sdk-oracle | sdk-llmcheck:glm-5p3-flash | sdk-llmcheck+integrity:glm-5p3-flash |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| natural | 0/28 | 0/28 | 28/28 | 28/28 | 24/28 | 28/28 | 28/28 | 28/28 | 28/28 | 27/28 | 28/28 | 28/28 | 27/28 | 27/28 | 0/28 | 0/28 | 27/28 | 22/28 | 24/28 |

## By node of the fault (step correct / cases)

| group | last-step | first-step | ww-all:glm-5p3-flash | ww-binary:glm-5p3-flash | falat:glm-5p3-flash | ww-all+gt:glm-5p3-flash | ww-binary+gt:glm-5p3-flash | ww-all:deepseek-v4p1-flash | ww-binary:deepseek-v4p1-flash | falat:deepseek-v4p1-flash | ww-all+gt:deepseek-v4p1-flash | ww-binary+gt:deepseek-v4p1-flash | ww-step:glm-5p3-flash | ww-step+gt:glm-5p3-flash | sdk-structural | sdk-integrity | sdk-oracle | sdk-llmcheck:glm-5p3-flash | sdk-llmcheck+integrity:glm-5p3-flash |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| - | 0/28 | 0/28 | 28/28 | 28/28 | 24/28 | 28/28 | 28/28 | 28/28 | 28/28 | 27/28 | 28/28 | 28/28 | 27/28 | 27/28 | 0/28 | 0/28 | 27/28 | 22/28 | 24/28 |

## By model that ran the pipeline (step correct / cases)

| group | last-step | first-step | ww-all:glm-5p3-flash | ww-binary:glm-5p3-flash | falat:glm-5p3-flash | ww-all+gt:glm-5p3-flash | ww-binary+gt:glm-5p3-flash | ww-all:deepseek-v4p1-flash | ww-binary:deepseek-v4p1-flash | falat:deepseek-v4p1-flash | ww-all+gt:deepseek-v4p1-flash | ww-binary+gt:deepseek-v4p1-flash | ww-step:glm-5p3-flash | ww-step+gt:glm-5p3-flash | sdk-structural | sdk-integrity | sdk-oracle | sdk-llmcheck:glm-5p3-flash | sdk-llmcheck+integrity:glm-5p3-flash |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| deepseek-v4p1-flash | 0/11 | 0/11 | 11/11 | 11/11 | 10/11 | 11/11 | 11/11 | 11/11 | 11/11 | 11/11 | 11/11 | 11/11 | 11/11 | 11/11 | 0/11 | 0/11 | 11/11 | 8/11 | 10/11 |
| glm-5p3-flash | 0/13 | 0/13 | 13/13 | 13/13 | 11/13 | 13/13 | 13/13 | 13/13 | 13/13 | 13/13 | 13/13 | 13/13 | 13/13 | 13/13 | 0/13 | 0/13 | 13/13 | 11/13 | 11/13 |
| minimax-m3p1-flash | 0/4 | 0/4 | 4/4 | 4/4 | 3/4 | 4/4 | 4/4 | 4/4 | 4/4 | 3/4 | 4/4 | 4/4 | 3/4 | 3/4 | 0/4 | 0/4 | 3/4 | 3/4 | 3/4 |

## By length of the trace (step correct / cases)

| group | last-step | first-step | ww-all:glm-5p3-flash | ww-binary:glm-5p3-flash | falat:glm-5p3-flash | ww-all+gt:glm-5p3-flash | ww-binary+gt:glm-5p3-flash | ww-all:deepseek-v4p1-flash | ww-binary:deepseek-v4p1-flash | falat:deepseek-v4p1-flash | ww-all+gt:deepseek-v4p1-flash | ww-binary+gt:deepseek-v4p1-flash | ww-step:glm-5p3-flash | ww-step+gt:glm-5p3-flash | sdk-structural | sdk-integrity | sdk-oracle | sdk-llmcheck:glm-5p3-flash | sdk-llmcheck+integrity:glm-5p3-flash |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| >12 steps | 0/1 | 0/1 | 1/1 | 1/1 | 0/1 | 1/1 | 1/1 | 1/1 | 1/1 | 1/1 | 1/1 | 1/1 | 0/1 | 0/1 | 0/1 | 0/1 | 1/1 | 1/1 | 1/1 |
| ≤8 steps | 0/27 | 0/27 | 27/27 | 27/27 | 24/27 | 27/27 | 27/27 | 27/27 | 27/27 | 26/27 | 27/27 | 27/27 | 27/27 | 27/27 | 0/27 | 0/27 | 26/27 | 21/27 | 23/27 |

Model calls made: 1513 (927 answered from the cache); new tokens sent 727,257 and received 1,948,060; 63 reply(ies) ran out of the token budget and were asked again with a larger one.
