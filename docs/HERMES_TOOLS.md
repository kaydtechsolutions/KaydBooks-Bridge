# Hermes tools and document intake

Install the optional adapter with `uv sync --frozen --extra hermes`. The independent
CLI and core do not require Hermes or MCP. Start the stdio server with
`python -m kaydbooks_bridge.hermes_tools` or `kaydbooks-bridge-tools`.

The trusted server process needs `KAYDBOOKS_CONFIG`, an environment credential named
by `KAYDBOOKS_TOOL_TOKEN_ENV` (default `KAYDBOOKS_TOKEN`), and optionally
`KAYDBOOKS_TOOL_SECRET_FILE`. Private config/state/secret files must remain outside Git.
New principals receive all supported permissions within their explicitly assigned
companies by default. Operators may restrict them with an explicit grant list.
Preparation needs prepare/read/validate; queueing needs submit and recovery needs
recover. The tool server exposes no posting or approval tool even when its principal
has those permissions; required review and posting gates still apply.
Optional local workflows require manage-workflows; receipt reports require report/read.
All calls require an explicit company. Config and grants are rechecked per operation.

| Tool v1 | Operation |
| --- | --- |
| capture_document_v1 | Decode bounded base64 and retain original bytes with a server-calculated SHA-256 |
| prepare_invoice_v1 | Prepare an invoice from an owned captured document, payload, field confidence and durable master reference |
| lookup_invoice_masters_v1 | Fixed read-only SDK account/customer/item/currency/commercial checks |
| prepare_bill_v1 | Prepare an expense bill from an owned captured document with field confidence and bill master evidence |
| lookup_bill_masters_v1 | Fixed read-only SDK supplier, payable/expense account and single-currency checks |
| validate_v1 | Validate a draft against current policy and source/master evidence |
| submit_v1 | Queue a validated transaction; never dispatch |
| status_v1 | Read canonical job state |
| preview_v1 | Produce a deterministic invoice or expense-bill review |
| verify_receipt_v1 | Verify fresh durable receipt evidence for a completed invoice or expense bill |
| recover_v1 | Hold expired attempts; never resend accounting |
| board_v1 | Read canonical job-board cards and counts |
| memory_v1 | Read expiring display/report preferences; never permissions |
| receipt_register_v1 | Historical receipt report with source hashes and derived totals |
| workflow_v1 | Bounded local schedule/cancel/tick/remember/delegate; no external delivery |

No tool accepts shell, SQL, raw qbXML, credentials, a desired job state or a posting
operation. Source content and tool-returned original values remain inert data.
Capture permits PDF, PNG, JPEG, plain text, CSV and JSON up to 4 MiB per document.
It does not claim OCR or independent semantic extraction quality: extraction comes
from the caller. Every payload leaf requires explicit finite confidence in [0,1].
Confidence below 1 blocks validation until explicit review. Confidence is evidence
from the extractor, not proof that a value is correct.

An operator with read/review-source permission can confirm the exact uncertain values:
`kaydbooks-bridge --company company-a review-source JOB_ID REVIEW_JSON`.
The private JSON contains `fingerprint` and `confirmed_values` (field paths to exact
extracted values). Review appends immutable evidence, preserves original uncertainty,
and is rechecked against current reviewer grants at validation/dispatch. It does not
approve an accounting transaction. Wrong extracted values cannot be silently rewritten;
draft correction/revision remains a separate, unavailable workflow.

Hermes supports stdio entries in `mcp_servers`, using command/args/env and a tool
allowlist. Disable resources/prompts and parallel tool calls for this adapter.
See the [official MCP configuration reference](https://hermes-agent.nousresearch.com/docs/reference/mcp-config-reference/).
Use an isolated Hermes home/profile; do not clone unrelated credentials, channels or
agent memories. `hermes mcp test kaydbooks` performs connection/tool discovery without
a model request. Bridge authentication is process-side even when Hermes labels the
stdio transport as having no transport authentication.

Actual qualification: the installed Hermes build discovered all thirteen tools in an
isolated private profile. An MCP client exercised source capture, a fresh real sample
SDK lookup, prepare/validate/preview/submit, duplicate prevention and cross-company
denial. MCP board/preferences/report and bounded local scheduling also passed, including
duplicate ticks and refusal of a posting action. No LLM calls, OCR tests, external channels or accounting writes were part of
this tool qualification. Automated stdio tests use synthetic company state.
