# Documents, knowledge and the I/O architecture

Written 2026-11-06 from the code in this repository. It says what the SDK and the CLI do with
files, documents and knowledge, where the layers meet, and what is still missing.

## 1. The layers, from the bottom

**Governed file tools (SDK, `tools/core`).** `read_file` (bounded UTF-8), `glob`, `grep`,
`write_draft`/`edit_draft`, `web_fetch`, `web_search`, `render_pdf_page`, `notebook_edit` and
`ask_human_question`. One `CoreToolServices` object holds the run root, byte and time limits, the
caches and the list of paths a run may write; every path is resolved against the root, and
credential-looking paths (`.env`, keys) are refused for every tool. Writes go to declared output
paths only. Nothing here understands a binary document except `web_fetch`, which extracts text from
a PDF **at a public URL** a page at a time and keeps the PDF bytes in a cache, and
`render_pdf_page`, which draws one page of such a PDF to a PNG and returns an `image_ref`.

**Artifact store (SDK, `.agent-artifacts`).** Content-addressed JSON (`sha256_<hash>.json`) with an
occurrences log, so the same content written twice is one object and every write can be traced.
Drafts become artifacts when committed; the audit log and the run-head checkpoints cover them.

**Specification pipeline (SDK, `specifications/`).** The structured path for documents a design
depends on. A `SpecificationManifest` lists documents; `SpecificationPreprocessor` turns each into a
`DocumentTree` of typed nodes (text, table, image, structured). Every node carries a `SourceRef`:
document id, relative path, SHA-256 of the source, format and an exact location. It reads 19 formats
(txt, md, tex, docx, pdf, csv, xlsx, yaml, json, xml, dot, plantuml, mermaid, tikz, svg, drawio,
vsdx, png, jpeg). Image nodes go through a `VisionAdapter` that must reach a confidence threshold or
the node is marked review-required; it never silently guesses. `GroundedRetrievalService` indexes
trees under a snapshot id with a deterministic lexical index (no embeddings, no service), restricts
a query to allowed categories, never stores the query text, and **resolves** every candidate back
against the trees so only nodes whose source still matches are admitted. The MCP harness uses this
path; the starter and the CLI did not.

**Memory (SDK, `memory/`).** Episodes, context projection, summaries and retention. This is what the
agent *remembers of a run*, not a library of documents.

**Evidence (SDK, observability).** Audit chain, telemetry, tool-result journal and signed
checkpoints record what was read and written, whatever the tool.

## 2. What the CLI could do before this change

The CLI offered `read_file` and `grep` (text only), `@file` mentions (text, and PNG/JPEG sent to
the model with the message), and `web_fetch` when `web = true`. A **local** PDF, Word or Excel file
could not be read at all: `read_file` refuses binary data. The run in `example_runs/pdf_document_test`
(two arXiv papers) was therefore not reproducible from the CLI: it used `web_fetch` on a PDF URL,
`render_pdf_page` and a tool called `interpret_pdf_image` (50 renders, 70 interpretations, 49
fetches, 8 draft writes in its audit log). `interpret_pdf_image` is not defined anywhere in this
repository; that run supplied it itself.

## 3. What the CLI does now

| Need | Tool or command | How it works |
|---|---|---|
| Read a local PDF, Word, PowerPoint, Excel or CSV | `read_document(path, start, max_chars)` | `pypdf`, `python-docx`, `openpyxl` and `csv`. Returns text with `[page N]` markers, the total, the SHA-256 and `next`; pages with no text layer are listed. |
| Find the right pages first | `search_document(path, query)` | Lexical scan of each page or sheet; returns page numbers with snippets, best first. |
| Look at a diagram, chart or scan | `interpret_page(path, question, page)` | Renders the page with `pypdfium2` to a PNG in the workspace and asks the **session's own model** with the image attached; fails with a named reason if the model takes no images. |
| Search many files and cite them | `knowledge_search` and `/kb add/list/search/refresh/remove` | Files become `DocumentTree`s (the SDK type above) of ~1,000-character passages; search goes through `GroundedRetrievalService`, so every hit is verified against its stored tree and says file, location (page or lines) and source hash. `/kb` shows which sources changed since they were added. |
| Keep what was learned | `wiki_read`, `wiki_search`, `wiki_write` and `/wiki` | Linked Markdown pages in `.nailong/wiki`; each write needs a reason, rebuilds `index.md`, appends to `log.md` and reports broken `[[links]]`. Plan mode gets the read tools only. |
| Repeatable procedures | 8 built-in skills | `paper-summary`, `wiki-ingest`, `kb-answer`, `code-review`, `release-notes`, `analyze-spreadsheet`, `server-health-check`, `incident-notes`; each names the exact tools above and what to cite. |

The `paper-summary` skill is the CLI form of the `pdf_document_test` run: `search_document` to find
the structure, `read_document` page by page with notes after each batch, `interpret_page` for
figures and tables, a summary with page numbers, and a wiki page.

## 4. Where data flows

```
file on disk ──read_document──► text with page markers ──► model ──► answer (cites pages)
      │                                                       │
      ├──interpret_page──► PNG in workspace ──► model (image) ┘
      │
      └──/kb add──► DocumentTree (passages + SourceRef + hash) ──knowledge_search──► verified, cited hits
model ──wiki_write──► .nailong/wiki/*.md ──► index.md, log.md      (all tool calls are audited)
```

Every read goes through a tool call, so the audit chain and telemetry record which document and
which pages the agent saw. Documents are untrusted content: their text reaches the model as tool
output, and a skill's `allowed-tools` limits what the agent may do while following one.

## 5. Limits, stated plainly

- Search is lexical. It finds the words you ask for, not their synonyms; there is no embedding
  index. For a large corpus, phrase several queries (the `kb-answer` skill does).
- `read_document` does not extract images inside Word or Excel files, only text and tables, and
  A `.pptx` is read as text, tables and speaker notes per slide (no pictures; use `interpret_page` on a
  PDF export for those). A scanned PDF has no text; use `interpret_page` page by page, which costs a
  model call per page.
- A PDF's reading order is whatever the file stores; multi-column papers and tables can come out
  jumbled, which is why the skill looks at the page when numbers matter.
- `interpret_page` needs a model that accepts images; the answer is that model's reading of the
  picture, not verified data.
- Remote PDFs still go through `web_fetch` (text) only; there is no CLI tool that renders a page
  of a remote PDF.
- The knowledge base is rebuilt in memory for each search, which is fine for hundreds of files and
  not for hundreds of thousands. It is per project.
- The wiki has no locking: two agents writing the same page at once keep the last write. The log
  shows both.
- The SDK's richer path (manifests, categories, vision confidence, review-required nodes) is not
  yet exposed to the CLI; `/kb` uses only its node, retrieval and verification types.
