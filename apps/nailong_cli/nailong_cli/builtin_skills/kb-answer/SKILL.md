---
name: kb-answer
description: Answer a question only from the project's knowledge base and wiki, citing the file and location of every claim, and saying so when the answer is not there
argument-hint: <question>
allowed-tools: knowledge_search wiki_search wiki_read read_file read_document search_document
---
Question: $ARGUMENTS

1. Search with `knowledge_search` using two or three different phrasings (the key nouns, then a
   likely sentence from the answer). Also `wiki_search` the same words.
2. Open the best hits fully (`read_file`, or `read_document` for the page) before relying on a
   snippet; a snippet can cut off the condition that changes the answer.
3. Answer in a few sentences. After each claim cite the source as `path (location)`. If sources
   disagree, show both and say which is newer when the files show it.
4. If nothing relevant comes back, say "The knowledge base does not cover this" and name what you
   searched for. Do not answer from memory, and do not stretch a loosely related passage.
5. If a source is marked as changed since it was added, mention that the answer may be out of date.
