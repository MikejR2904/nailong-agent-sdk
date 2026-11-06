---
name: wiki-ingest
description: Fold a file or document into the project wiki as short linked pages, merging with what is already there and flagging contradictions
argument-hint: <path to a file or document>
allowed-tools: read_file read_document search_document interpret_page wiki_search wiki_read wiki_write grep glob
---
Add this source to the wiki: $ARGUMENTS

1. Read the source completely (`read_file` for text, `read_document` for PDF, Word, Excel, CSV).
2. List the distinct topics it covers. For each, `wiki_search` for an existing page.
3. Existing page: `wiki_read` it, then `wiki_write` the merged page. Keep what was right, correct
   what the source contradicts and say so in the page ("Earlier note said X; <source> says Y"),
   and add the new facts.
4. No page: write a new one, a short title line first, then facts as bullets, each ending with the
   source path and page or line: `(docs/auth.md, lines 12-30)`.
5. Link pages to each other with `[[page-name]]`. Fix the broken links the result lists by
   creating the missing page or removing the link.
6. Use a `why` that names the source and what changed. Finish with a list of pages created and
   updated. Do not put guesses in the wiki; if something is unclear, say so in the page.
