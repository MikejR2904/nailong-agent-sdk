# Security

## Reporting a problem

Please do not open a public issue for a security problem. Use GitHub's private vulnerability
reporting on this repository (Security tab, "Report a vulnerability"). Include the version
(`nailong --version`), what you did, what you expected and what happened. You should get an answer
within a week; this is a one-person project, so please be patient.

## What is in scope

- The SDK in `src/nailong_agent_sdk` and the CLI in `apps/nailong_cli`.
- Ways to make the agent run a command, read a file or reach a host that the permission mode, the
  rules, the denied-path list or the sandbox should have refused.
- Ways for a project file to change a setting that is meant to be user-only.

`docs/threat-model.md` lists what the design protects against and what it does not. Prompt
injection is reduced, not eliminated: fetched pages and files are labelled as data and risky calls
need approval, but nothing detects an injected instruction.

## Supported versions

Only the latest commit on the default branch is supported until a release is published.
