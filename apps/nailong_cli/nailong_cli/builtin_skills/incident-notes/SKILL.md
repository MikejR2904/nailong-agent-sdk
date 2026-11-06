---
name: incident-notes
description: Keep a running incident timeline in a note while debugging, then turn it into a wiki post-mortem with the cause, the fix and the follow-ups
argument-hint: <one line describing the incident>
allowed-tools: notes_write notes_read read_file grep glob run_command wiki_search wiki_read wiki_write
---
Incident: $ARGUMENTS

While investigating, append to the note titled "incident" with `notes_write` (append true) every
time you learn something: a UTC-less local time is fine, then the observation, then the command or
file that showed it. Record dead ends as well as findings. Keep "what we know", "what we ruled
out" and "next step" at the top of the note, rewritten as you go, so the job survives compaction.

When the cause is found and the fix is confirmed:
1. `wiki_search` for earlier incidents with the same symptom and link them.
2. `wiki_write` a page named `incidents/<short-date-and-topic>` with: summary, impact, timeline
   (from the note), root cause (the mechanism, not "human error"), what fixed it, what would have
   caught it sooner, and follow-ups as a checklist with owners left blank.
3. Do not claim a cause you did not demonstrate; mark it "suspected" and say what would confirm it.
