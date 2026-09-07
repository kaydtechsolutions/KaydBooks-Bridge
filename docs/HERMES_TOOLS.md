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
recover. `qbwc_entry_v1` can enqueue a controlled sample write through Web Connector
only after existing independent approval and posting gates pass. The tool server
exposes no approval action. Legacy native tools remain outside the pilot allowlist.
Optional local workflows require manage-workflows; receipt reports require report/read.
All calls require an explicit company. Config and grants are rechecked per operation.

| Tool v1 | Operation |
| --- | --- |
| company_catalog_v1 | Read authorized company mappings and supported entry forms |
| qbwc_entry_v1 | Eight selected QBWC check/prepare/validate/preview/submit/dispatch/recover/status actions |
| batch_preview_v1 | Freeze exact source-bound validated jobs for separate operator review |
| batch_status_v1 | Read immutable batch progress and provider acknowledgments |
| native_report_v1 | Fixed native reports with company/date/basis evidence |
| table_intake_v1 | Explicit CSV/XLSX mapping, preview and row preparation |
| company_access_v1 | Assigned-company users, combinable roles and exact restrictions |
| revise_document_v1 | Immutable draft successor with new evidence and invalidated approval |
| extract_document_v1 | Offline bounded PDF/image observations held for review |
| prepare_extraction_v1 | Source-bound draft from retained extraction observations |
| master_lookup_v1 | Read one exact customer, supplier or item with original revision |
| check_master_change_v1 | Check a typed master proposal against fresh native evidence |
| prepare_master_change_v1 | Prepare a source-bound master proposal; no native write |
| capture_document_v1 | Decode bounded base64 and retain original bytes with a server-calculated SHA-256 |
| prepare_invoice_v1 | Prepare an invoice from an owned captured document, payload, field confidence and durable master reference |
| lookup_invoice_masters_v1 | Fixed read-only SDK account/customer/item/currency/commercial checks |
| prepare_bill_v1 | Prepare an expense bill from an owned captured document with field confidence and bill master evidence |
| lookup_bill_masters_v1 | Fixed read-only SDK supplier, payable/expense account and single-currency checks |
| prepare_customer_payment_v1 | Prepare a captured customer receipt with exact allocation evidence |
| check_customer_payment_v1 | Fixed read-only customer, AR, deposit, method and invoice-balance checks |
| prepare_supplier_payment_v1 | Prepare a captured supplier payment with exact bill-allocation evidence |
| check_supplier_payment_v1 | Fixed read-only vendor, AP/bank and complete bill-payable checks |
| prepare_customer_credit_v1 | Prepare a captured unapplied service credit tied to an original invoice |
| check_customer_credit_v1 | Fixed read-only source-invoice, prior-credit and customer-balance checks |
| prepare_credit_application_v1 | Prepare an existing credit-to-invoice application with exact read evidence |
| check_credit_application_v1 | Fixed read-only invoice/credit balances and reciprocal links |
| prepare_customer_refund_v1 | Prepare a recorded refund against existing unused credits |
| check_customer_refund_v1 | Read exact credit, customer, bank and payment-method evidence |
| prepare_supplier_credit_v1 | Prepare an unapplied credit tied to an original supplier bill |
| check_supplier_credit_v1 | Read bill limits, credit history and independent payable evidence |
| prepare_supplier_application_v1 | Prepare an existing supplier-credit application |
| check_supplier_application_v1 | Verify bill/credit balances and reciprocal links |
| validate_v1 | Validate a draft against current policy and source/master evidence |
| submit_v1 | Queue a validated transaction; never dispatch |
| status_v1 | Read canonical job state |
| preview_v1 | Produce a deterministic review of the selected supported operation |
| verify_receipt_v1 | Verify fresh durable receipt evidence for a completed invoice or expense bill |
| recover_v1 | Hold expired attempts; never resend accounting |
| board_v1 | Read canonical job-board cards and counts |
| memory_v1 | Read expiring display/report preferences; never permissions |
| receipt_register_v1 | Historical receipt report with source hashes and derived totals |
| workflow_v1 | Bounded local schedule/cancel/tick/remember/delegate; no external delivery |

No tool accepts shell, SQL, raw qbXML, credentials or a desired job state. The only
dispatch action uses the fixed QBWC contract and existing sample gates. Source
content and tool-returned original values remain inert data. There are 42 raw tools;
the seven-tool pilot allowlist and trusted confirmation adapter are documented in
[Hermes channel integration](HERMES_CHANNEL.md).

`qbwc_entry_v1` publishes action-specific parameter schemas. Use these exact fields:

| Action | Required parameters | Optional parameters |
| --- | --- | --- |
| check | operation, connector_id, payload | None |
| prepare | operation, document_id, idempotency_key, payload, confidence | master_evidence |
| revise | parent_id, parent_fingerprint, reason, document_id, idempotency_key, payload, confidence | master_evidence |
| validate, preview, status, submit, dispatch, recover | job_id | None |

Choose `connector_id` from the company catalog. `check` does not accept upload
metadata. Repeat the same read-only check after Web Connector runs until `pending`
is false; pass its `evidence` object unchanged as preparation's `master_evidence`.
Expected Bridge rejections return `ok: false` and a specific `error` in a normal
tool response. They are not success, transport failure or permission to bypass
approval. This prevents ordinary field mistakes from parking Hermes's MCP connection.

Every successful `check` response also includes `confidence_schema`, a JSON Schema
with the exact required **payload leaf paths**, numeric scores from 0 through 1 and
no additional keys. For example, a single invoice line uses `lines.0.item_id`,
`lines.0.quantity`, `lines.0.unit_price` and `lines.0.amount`; never `lines`, nested
arrays or `lines[0].amount`. Top-level scalar fields such as `currency` and
`txn_date` have their own scores. Optional payload fields require scores only when
present. Scores below 1 retain uncertainty and require source review; a matched
master check does not establish extraction confidence. The server does not fill
in scores or grant approval. Invalid confidence errors list missing, unexpected
and invalid keys for the submitted payload.

Capture permits PDF, PNG, JPEG, plain text, CSV and JSON up to 4 MiB per document.
Call `company_catalog_v1(company)` before capture. The `namespace` must be one of
the returned `sources`, not the company alias. The `reference` is a stable internal
upload ID matching `[a-z][a-z0-9_-]{0,63}` (for example `upload-001`), not a filename
or uppercase transaction number. Keep the original bytes and invoice `ref_number`
unchanged. Retry with the same upload ID; reusing it for changed content is refused.
The optional `extract_document_v1` and `prepare_extraction_v1` tools now provide
[qualified offline OCR observations](DOCUMENT_EXTRACTION.md) and source-bound drafts.
Caller-supplied structured extraction remains available. Every payload leaf requires
explicit finite confidence in [0,1].
Confidence below 1 blocks validation until explicit review. Confidence is evidence
from the extractor, not proof that a value is correct.

An operator with read/review-source permission can confirm the exact uncertain values:
`kaydbooks-bridge --company company-a review-source JOB_ID REVIEW_JSON`.
The private JSON contains `fingerprint` and `confirmed_values` (field paths to exact
extracted values). Review appends immutable evidence, preserves original uncertainty,
and is rechecked against current reviewer grants at validation/dispatch. It does not
approve an accounting transaction. Wrong extracted values cannot be silently rewritten;
`revise_document_v1` retains the original and creates a reviewed successor.

Hermes supports stdio entries in `mcp_servers`, using command/args/env and a tool
allowlist. Disable resources/prompts and parallel tool calls for this adapter.
See the [official MCP configuration reference](https://hermes-agent.nousresearch.com/docs/reference/mcp-config-reference/).
Use an isolated Hermes home/profile; do not clone unrelated credentials, channels or
agent memories. `hermes mcp test kaydbooks` performs connection/tool discovery without
a model request. Bridge authentication is process-side even when Hermes labels the
stdio transport as having no transport authentication.

Initial qualification: the installed Hermes build discovered the then-current thirteen tools in an
isolated private profile. An MCP client exercised source capture, a fresh real sample
SDK lookup, prepare/validate/preview/submit, duplicate prevention and cross-company
denial. MCP board/preferences/report and bounded local scheduling also passed, including
duplicate ticks and refusal of a posting action. No LLM calls, OCR tests, external channels or accounting writes were part of
this tool qualification. Automated stdio tests use synthetic company state.

The current adapter exposes 38 tools. Stdio discovery tests cover that full inventory;
the expanded tools do not turn the earlier connection test into a full conversational
or external-message qualification. [Master changes](MASTER_RECORDS.md) share the same
source and preparation contracts.
