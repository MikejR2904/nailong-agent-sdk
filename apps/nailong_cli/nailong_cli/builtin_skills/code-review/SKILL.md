---
name: code-review
description: Review the uncommitted changes (or a branch against its base) for bugs, missing tests and risky edits, read-only, with file and line for every finding
argument-hint: "[base branch, default: the uncommitted changes]"
allowed-tools: read_file grep glob run_command(git diff:*) run_command(git status:*) run_command(git log:*)
---
Review: $ARGUMENTS (if empty, the uncommitted changes).

1. `git status`, then `git diff` (or `git diff <base>...HEAD`) to see exactly what changed. Do not
   run anything that writes.
2. Read the changed files around each hunk with `read_file`; a hunk alone hides the callers.
3. Look for, in this order: wrong behaviour on the unusual input (empty, large, concurrent);
   error paths that swallow or hide the cause; security (input reaching a shell, a path or a query;
   secrets; permissions); a change with no test; names or docs that now lie.
4. Report each finding as `file:line`, one sentence on what is wrong, one on the failing input or
   scenario, and the smallest fix. Order by severity. Put things you are unsure about in a
   separate "questions" list rather than the findings.
5. If you find nothing, say what you checked. Do not pad the review with style remarks.
