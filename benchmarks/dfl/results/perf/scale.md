### Solver scale check (descriptive)

General DAGs (actions depend on 1-3 exploratory episodes), budget 45-65% of total tokens, default solver limits (`exact_max_branch_nodes = 50000`). Learned coefficients here are perturbed static utilities, because the point is the solver, not the model.

| Episodes | Method | Status counts | Solver | Max branch nodes | Median ms | Max ms | Max relative gap |
|---|---|---|---|---|---|---|---|
| ~12 | static-pckp | {'optimal': 8} | branch-and-bound | 295 | 2.5 | 4.5 | 0.0000 |
| ~12 | learned-pckp | {'optimal': 8} | branch-and-bound | 235 | 2.3 | 3.8 | 0.0000 |
| ~24 | static-pckp | {'optimal': 8} | branch-and-bound | 1821 | 21.8 | 30.3 | 0.0000 |
| ~24 | learned-pckp | {'optimal': 8} | branch-and-bound | 2693 | 23.1 | 37.1 | 0.0000 |
| ~48 | static-pckp | {'optimal': 8} | branch-and-bound | 22491 | 390.7 | 486.9 | 0.0000 |
| ~48 | learned-pckp | {'optimal': 8} | branch-and-bound | 23195 | 319.7 | 647.5 | 0.0000 |
| ~96 | static-pckp | {'best-effort': 8} | branch-and-bound | 50000 | 2255.0 | 2558.3 | 0.1559 |
| ~96 | learned-pckp | {'best-effort': 8} | branch-and-bound | 50000 | 2310.7 | 2454.1 | 0.3569 |
| ~160 | static-pckp | {'best-effort': 8} | branch-and-bound | 50000 | 4657.0 | 6055.6 | 0.1185 |
| ~160 | learned-pckp | {'best-effort': 8} | branch-and-bound | 50000 | 4666.2 | 4979.8 | 0.5205 |
