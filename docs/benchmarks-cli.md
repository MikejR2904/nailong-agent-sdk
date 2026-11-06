# CLI benchmarks

Measured 2026-11-06 on Windows-11-10.0.26300-SP0, Python 3.13.1. No model is called; times are wall-clock milliseconds on a local disk, with every durable write synced (`fsync`).

| Measurement | Runs | Median ms | p95 ms | Note |
|---|---|---|---|---|
| process start (`--version`) | 5 | 1253.2 | 1285.3 | includes interpreter and imports |
| `--health` on an empty project | 5 | 1274.4 | 1353.8 |  |
| wiki write (new page, 1..300 pages) | 300 | 47.7 | 58.1 | fsync of page, history, journal, index |
| wiki write, first 50 | 50 | 44.0 | 50.9 |  |
| wiki write, last 50 | 50 | 53.4 | 60.0 | stays flat: the index and log are updated, not rebuilt |
| wiki read | 100 | 30.8 | 52.6 | includes backlinks scan |
| wiki search (300 pages) | 50 | 28.5 | 32.4 |  |
| wiki update of one page (50 revisions) | 50 | 33.8 | 40.1 |  |
| discovery publish (1..200) | 200 | 19.5 | 25.0 | re-reads the journal each time |
| discovery list (200) | 20 | 48.5 | 70.1 |  |
| knowledge add 100 files | 1 | 1516.4 | 1516.4 | parse, split, hash, atomic save |
| knowledge search (100 files) | 15 | 16.0 | 20.4 | index rebuilt per search |
| knowledge add 400 files | 1 | 4909.1 | 4909.1 | parse, split, hash, atomic save |
| knowledge search (400 files) | 15 | 63.5 | 90.6 | index rebuilt per search |
| event emit (durable, fsync each) | 2000 | 1.63 | - | 614 events/s |
| event log read + verify (2000 events) | 5 | 34.2 | 49.0 |  |
| request size before the prompt, default tools | 1 | - | - | instructions 1,004 24%; tool definitions (34) 3,129 75%; total 4,159 (estimated tokens) |
| request size before the prompt, with web | 1 | - | - | instructions 1,004 21%; tool definitions (36) 3,641 77%; total 4,671 (estimated tokens) |

## What these numbers say

- Starting the program takes about 1.25 s (it was about 2.0 s until the SDK stopped importing the `mcp` package unless an MCP server is used). What is left is the SDK importing its pydantic model modules, spread over many files, with no single large item; further cuts need those modules loaded on demand.
- A durable wiki write costs tens of milliseconds because every write syncs the page, its history copy, the journal and the index to disk. It no longer grows with the number of pages (an earlier version rebuilt the whole index and log on each write: 225 ms at 300 pages).
- Knowledge-base search rebuilds the lexical index on every call; it scales with the number of passages and is cheap at hundreds of files, not at hundreds of thousands.
- The fixed part of every model request (instructions plus tool definitions) is about 4,000 to 4,600 estimated tokens before the user's words; the tool definitions are three quarters of it. The cost of a request is that figure times your model's input price (cached input after the first request where the provider caches), times the number of model steps in a turn.
