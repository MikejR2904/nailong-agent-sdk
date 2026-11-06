---
name: release-notes
description: Draft release notes from the commits since the last tag, grouped by what matters to users, without changing anything
argument-hint: "[since: a tag or commit, default: the last tag]"
allowed-tools: read_file grep run_command(git log:*) run_command(git describe:*) run_command(git tag:*) run_command(git diff:*) run_command(git show:*)
---
Draft release notes since: $ARGUMENTS (if empty, the last tag).

1. Find the starting point with `git describe --tags --abbrev=0` (or the argument), then list the
   commits with `git log <start>..HEAD --pretty=format:"%h %s"`.
2. For commits whose subject does not say what changed for a user, look at `git show --stat <hash>`.
3. Group under: **New**, **Changed**, **Fixed**, **Removed**, **Internal**. Write for someone who
   uses the software, not someone who wrote it: say what they can now do or what stopped
   happening. Merge commits that are one change. Mention breaking changes first, in bold, with the
   migration step.
4. Add the commit hashes in brackets so each line can be checked. Do not invent changes the
   commits do not show. Print the notes in the answer; do not write a file unless asked.
