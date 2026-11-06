---
name: analyze-spreadsheet
description: Read an Excel or CSV file, state what is in it, and answer a question about it with numbers you checked by running code
argument-hint: <path to the xlsx or csv> <question>
allowed-tools: read_document search_document read_file run_command notes_write
---
Spreadsheet and question: $ARGUMENTS

1. `read_document` the file. Note each sheet's name, its header row and the shape of the data. The
   reader cuts a sheet at 200 rows, so for anything longer do not estimate from the preview.
2. For counts, sums, averages, filters and joins write a short Python script that loads the whole
   file (`openpyxl` or `csv`), run it with `run_command`, and use its printed numbers. State the
   script's key assumptions (which column, how blanks were treated).
3. Answer the question first, then show the figures behind it as a small table. Name the sheet and
   columns you used. Say if blanks, text in a number column or duplicate rows affected the result.
4. Do not edit the source file. Save any derived file under a new name and tell the user where.
