# sg-job-agent

A Singapore job-hunting agent built on `nailong-agent-sdk`. You give it your base
LaTeX resume. It works out which roles you fit, finds real full-time openings,
writes a tailored and proofread resume plus cover letter for each one, and helps
you apply.

```
resume.tex ──profiler──▶ target roles ──scout × role──▶ jobs.json ledger
           ──tailor + polisher × job──▶ tailored resume.tex/.pdf + cover letter
           ──applier──▶ application (browser-assisted or hand-off)
```

| Stage | What it does | SDK pieces |
|---|---|---|
| `profile` | Reads your resume and proposes target roles (e.g. AI Engineer, Research Engineer, Quant Dev, SWE, Solutions Architect), each with title keywords and search queries. | `BaseAgent` + `read_file` |
| `discover` | One scout run per role. Searches MyCareersFuture and configured Greenhouse/Lever/Ashby boards, and runs `site:` web searches over LinkedIn, eFinancialCareers, JobStreet and ATS boards. Scores fit (0-100) and saves only real Singapore full-time postings. | `web_search`, `web_fetch`, custom tools, budgeted serialized web calls |
| `tailor` | Rewrites the resume for one JD (reorders, rephrases, cuts) and writes a cover letter. A second **polisher** agent then proofreads both. | `write_draft`/`edit_draft`, `check_tailored_resume`, `resume-integrity` verification gate |
| `apply` | Fills Greenhouse and Lever forms in a real browser (Playwright): contact details, resume PDF, cover letter, and screening answers from your config. For other sites it opens the posting and hands off to you. | `Applier` |

Everything is persisted under `--workspace` (default `./workspace`): `profile.json`,
`jobs.json` (the ledger), `jobs/<id>/{job_description.md,resume.tex,resume.pdf,cover_letter.txt}`,
plus the SDK's audit logs and telemetry. Each step can be re-run on its own, and
re-runs never apply to the same job twice.

## Truthfulness guard

Tailoring may reorder, cut, and reword; it may **not** invent anything. Each tailored
resume is checked deterministically against your base resume:

- **errors** (the run is rejected and retried once, then marked `tailor_failed`):
  any number, metric, or month-year date not in the base resume; leftover
  placeholders (TODO, [company]); broken LaTeX; failed compilation; going over
  `max_pages`; any line that runs past the right margin.
- **warnings** (shown to you before applying): skill terms copied from the JD
  that your base resume never mentions, e.g. adding "Kubernetes" because the JD
  asked for it.

The agents run this check themselves while they work (`check_tailored_resume`).
The same check runs again as an SDK verification gate the model cannot bypass.
If the polish pass breaks something, the pre-polish version is restored.

## Setup

```bash
cd apps/sg_job_agent
uv sync                                   # installs this app + the SDK from ../..
uv sync --extra browser && uv run playwright install chromium   # for browser-assisted apply
cp config.example.yaml config.yaml        # fill in your details (git-ignored)
export FIREWORKS_API_KEY=...              # or whatever model.api_key_env names
```

For PDFs, install a LaTeX engine: [tectonic](https://tectonic-typesetting.github.io/)
(`brew install tectonic`, or `cargo install tectonic`) or TeX Live
(`latexmk`/`pdflatex`). Without one you still get tailored `.tex` files, but the
applier can only hand off to you, because it needs a PDF to upload.

Any OpenAI-compatible Chat Completions endpoint with tool calling works. Set
`model.base_url`, `model.model`, and `model.api_key_env` in `config.yaml`. Use a
strong model for tailoring; it is the stage where quality shows.

## Usage

```bash
uv run sg-job-agent profile --resume ~/cv/resume.tex   # review the proposed roles in profile.json
uv run sg-job-agent discover                           # or: --role "Quant Developer"
uv run sg-job-agent list                               # ranked by fit score
uv run sg-job-agent tailor --top 5                     # or: --job <id> [--job <id> ...]
uv run sg-job-agent list --status tailored             # review jobs/<id>/ before applying!
uv run sg-job-agent overleaf                           # one-click "Open in Overleaf" page
uv run sg-job-agent apply --top 5 --mode review

uv run sg-job-agent run --resume ~/cv/resume.tex --top 5   # all steps in one go
```

To steer the search, set `search.roles` (preferred roles, listed first),
`search.exclude_companies`, `search.min_fit_score`, and
`search.sources.{greenhouse_boards,lever_companies,ashby_boards}`. Those take
company slugs, e.g. `jobs.lever.co/<slug>`.

## Overleaf

Every tailored resume gets an "Open in Overleaf" launcher at
`jobs/<id>/open_in_overleaf.html`. `workspace/overleaf.html` lists all of them;
regenerate it any time with `sg-job-agent overleaf`. Open the page in a browser
where you are logged in to Overleaf and click a button. The page uses Overleaf's
public [developer API](https://www.overleaf.com/devs): a form POST of the LaTeX
(`encoded_snip`, `snip_name`, `engine=pdflatex`) to `https://www.overleaf.com/docs`,
which creates a project you can edit and compile. Overleaf returns no PDF to the
caller, so the page-limit and margin checks still need a local LaTeX engine.

## Apply modes

| Mode | Behaviour |
|---|---|
| `manual` | Opens the posting and prints the packet paths. Asks whether you submitted. |
| `review` *(default)* | Opens a real browser with the form pre-filled. You check it, finish anything left, and click Submit. |
| `auto` | Fills and submits by itself, but only when every required field is answered and there is no CAPTCHA. Otherwise it falls back to review. Asks once before a batch, unless you pass `--yes`. |

A job is marked `applied` only after a confirmed submission: either the
confirmation page is detected or you say yes.

**What is and isn't automated, and why:**

- **Greenhouse and Lever** company career pages are browser-assisted. Some
  employers enable a CAPTCHA (common on Greenhouse), and that always needs you.
- **LinkedIn, MyCareersFuture (Singpass login), eFinancialCareers, and JobStreet**
  are always hand-off. They need your logged-in account, and their terms prohibit
  automated applications. Bots risk getting your account restricted. The agent
  still finds these jobs and prepares a tailored packet for each.
- Screening questions are answered only from `candidate.answers` and your profile
  fields (matched by label). Unknown required questions are never guessed: they
  block auto-submit and are left for you.

## Notes and limits

- Job-source endpoints are public but undocumented in places. MyCareersFuture in
  particular may change shape. Source failures become warnings, not crashes.
- Web search uses the SDK's DuckDuckGo client, which rate-limits bursts. Calls are
  serialized, spaced 2 s apart, and capped by `search.max_web_calls_per_role`.
- The fabrication check is a heuristic. It catches invented numbers reliably and
  surfaces copied skills, but still read every tailored resume before you send it.

## Tests

```bash
uv run --group dev pytest        # offline: ScriptedModel + mocked job APIs
```

The browser tests drive a local Lever-style form and are skipped when Playwright
isn't installed. Set `SG_JOB_AGENT_TEST_BROWSER=/path/to/chrome` to use a specific
Chromium.
