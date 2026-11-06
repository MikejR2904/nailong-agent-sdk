---
name: paper-summary
description: Read a long PDF paper or report from the project and write a cited summary, looking at figures and tables on the page when the text is not enough
argument-hint: <path or URL of the PDF>
allowed-tools: fetch_document read_document search_document interpret_page read_file wiki_search wiki_read wiki_write notes_write notes_read todo_write
---
Summarise the document at: $ARGUMENTS

1. If it is a URL (for example an arXiv PDF link), call `fetch_document` with it and use the
   `path` it returns from here on; if that tool is not offered, tell the user to start with `--web`
   (or set `web = true`) instead of trying to download it any other way.
2. Call `search_document` once with the title words and once with "abstract conclusion" to learn
   where the front matter and the conclusions are. Note the total page count from the result.
3. Read it in order with `read_document`, following `next`, a few pages at a time. After each batch
   write two or three lines of notes with `notes_write` (title: the file name), always with page
   numbers, so a long paper survives compaction.
4. When a page lists `no_text_layer`, or the text refers to a figure, chart, table or equation
   that the text layer lost, call `interpret_page` for that page with a precise question (for
   example "What are the axes and what does each curve show?"). Never describe a figure you have
   not looked at.
5. Write the summary: what problem it addresses, the method, the main results with their numbers,
   limits the authors admit, and anything you could not verify. Every claim carries "(p. N)".
6. Check `wiki_search` for an existing page on the topic. Record the summary as a wiki page with
   `wiki_write` (name it after the paper, lowercase with hyphens), linking related pages, and give
   the file name and page count in the `why`.
7. Reply with the summary and the wiki page name. Do not invent page numbers.
