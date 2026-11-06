# CLI benchmarks

Measured 2026-11-06 on Windows-11-10.0.26300-SP0, Python 3.13.1. No model is called; times are wall-clock milliseconds on a local disk, with every durable write synced (`fsync`).

| Measurement | Runs | Median ms | p95 ms | Note |
|---|---|---|---|---|
| process start (`--version`) | 5 | 2070.4 | 2162.3 | includes interpreter and imports |
| `--health` on an empty project | 5 | 2067.1 | 2173.3 |  |
| wiki write (new page, 1..300 pages) | 300 | 46.7 | 58.2 | fsync of page, history, journal, index |
| wiki write, first 50 | 50 | 44.2 | 48.8 |  |
| wiki write, last 50 | 50 | 39.1 | 57.6 | index rebuild grows with pages |
| wiki read | 100 | 27.4 | 35.4 | includes backlinks scan |
| wiki search (300 pages) | 50 | 28.3 | 45.1 |  |
| wiki update of one page (50 revisions) | 50 | 37.7 | 47.0 |  |
| discovery publish (1..200) | 200 | 19.6 | 24.0 | re-reads the journal each time |
| discovery list (200) | 20 | 63.2 | 100.7 |  |
| knowledge add 100 files | 1 | 501.0 | 501.0 | parse, split, hash, atomic save |
| knowledge search (100 files) | 15 | 18.5 | 27.0 | index rebuilt per search |
| knowledge add 400 files | 1 | 1977.2 | 1977.2 | parse, split, hash, atomic save |
| knowledge search (400 files) | 15 | 61.8 | 67.7 | index rebuilt per search |
| event emit (durable, fsync each) | 2000 | 1.56 | - | 642 events/s |
| event log read + verify (2000 events) | 5 | 27.3 | 76.2 |  |
| request size before the prompt, default tools | 1 | - | - | instructions 1,004 24%; tool definitions (33) 3,026 74%; total 4,056 (estimated tokens) |
| request size before the prompt, with web | 1 | - | - | instructions 1,004 21%; tool definitions (35) 3,538 77%; total 4,568 (estimated tokens) |
