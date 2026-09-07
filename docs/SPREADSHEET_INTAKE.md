# CSV and Excel intake

The shared intake contract reads UTF-8 CSV and `.xlsx` tables, previews every row,
and prepares only explicitly selected rows as drafts. It never approves, submits,
posts, initiates payments or sends messages. All operation-specific source, master,
permission, approval and dispatch checks continue to apply.

Capture the original file with `capture_document_v1`. CSV uses `text/csv`; XLSX uses
`application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`. Original bytes
and SHA-256 remain immutable in the company. Use a new upload reference for each
changed file; use the same dataset name and stable row identities across imports.

Call `table_intake_v1` with action `preview` and parameters `document_id` and
`specification`. The specification supplies:

- `dataset`: stable private dataset identifier, independent of filename or upload date.
- `operation`: one supported operation for this table.
- `key_column`: an exact text column containing a unique stable transaction identity.
- `template`: the operation's explicit payload shape and configured constant values.
- `columns`: payload field paths mapped to exact header names and conversion types.
- `delimiter`: explicitly comma, semicolon, tab or pipe for CSV; or `sheet`: an exact
  worksheet name for XLSX. Neither format guesses its layout.

Example mapping: `"lines.0.amount": {"column": "Amount", "type": "money"}`.
Paths must name existing scalar template fields. Indexed paths support multiple
lines and allocations within one transaction row; one row always means one document.
Different operation shapes use separate tables/templates. Long-form rows containing
only part of a transaction must first be arranged into one transaction per row;
the importer does not infer grouping, missing lines or document totals.

Conversions are `text`, `date`, `money` and `decimal`. Dates use ISO `YYYY-MM-DD`;
Excel calendar-date cells are accepted only without a time. Identifiers must be text
to preserve leading zeros. Money accepts exact cents and never rounds silently.
Currency symbols, grouping separators, exponent notation, formulas, cell errors and
ambiguous values are held for correction. Constants are visible in each payload preview.
Captured document text cannot select a company, operation, permission or execution mode.

Preview returns its immutable ID, source hash, company, complete row payloads and
errors. `prepare_rows` requires that preview ID and selected `row_keys`, plus current
`master_evidence` keyed by row identity wherever the operation requires it. Configuration
changes invalidate old previews; permissions and master checks repeat at preparation.
Bad rows return errors while independent selected good rows can become drafts.

Stable dataset/row identity is also the canonical idempotency key. Reordering or
re-uploading unchanged rows returns existing jobs, including after a partial batch or
interruption between job creation and intake-link recording. Conflicting changed rows
cannot create another transaction even if their business reference also changes; use
the explicit draft-revision workflow. Original files, canonical row documents, mapping,
preview and per-row job links remain retained and audited.

Resource limits are 4 MB uploaded source, 1,000 data rows, 64 columns, 8,192 characters
per cell and 16 MB expanded XLSX data. ZIP members and worksheet coordinates are checked
before read-only parsing. XML entities, encrypted packages, macros, embedded objects,
external workbook links and merged cells are rejected. A selected worksheet is read
completely within those limits, including hidden rows; formatting never silently filters
accounting rows. Formula values are never taken from a workbook's cached result.

The XLSX reader follows the official [read-only workbook guidance](https://openpyxl.readthedocs.io/en/stable/optimized.html)
and loads formulas explicitly rather than cached values using the documented
[load_workbook options](https://openpyxl.readthedocs.io/en/stable/api/openpyxl.reader.excel.html).
Legacy `.xls` files must be exported to CSV or XLSX first. Unsupported layouts produce
explicit errors. Browser upload/mapping forms are tracked separately in M4-02.

CLI: `table-intake preview REQUEST.json` or `table-intake prepare_rows REQUEST.json`
with the normal private configuration, credential and explicit company arguments.
These commands use the same contract as the MCP tool.
