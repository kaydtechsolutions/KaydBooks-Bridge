# Architecture and durable implementation plan

## Decision record: application layer around qbwc-kit

The inherited library provides SOAP dispatch, qbXML builders/parsers, in-memory
generator sessions, WSDL/QWC generation, an optional FastAPI wrapper and test doubles.
It does not provide durable posting jobs, company authorization or accounting policy.
Its request-name vocabulary is not an SDK compatibility certificate. Its generic
builder accepts unknown requests and operator-provided raw fields.

Bridge owns authorization, input validation, approvals, execution state and evidence.
Interfaces must call `Bridge`; they must not construct qbXML or query private databases.
The first worker is a local synthetic ledger with separate storage. There is no
production worker, live enable flag, arbitrary transport injection or public HTTP API.
Future production code must keep using the same service controls and add a separately
reviewed production gate. A configuration change cannot enable live posting today.

## Data flow and trust boundaries

1. An interface authenticates a principal with a secret outside Git and supplies an
   explicit company. A chat, document, memory hint, card or schedule cannot choose
   credentials or expand the principal's company grants.
2. The service checks the operation, source namespace, exact synthetic master IDs,
   decimal amounts, dates, currency and company total limit. Unknown fields fail.
3. Preparation persists immutable payload/source evidence and a canonical fingerprint.
   Validation refuses uncertain fields. Approval is bound to that fingerprint and
   comes from a different principal. The current milestone has no draft edit API.
4. Submission queues work. Dispatch checks current initiating-principal, approver and
   worker grants and reloads private policy. The simulator checks again immediately
   before writing, including the persisted pause control and attempt fence.
5. An atomic transaction persists dispatch intent and an attempt token before the
   synthetic ledger call. One unresolved write per company is allowed; companies use
   separate databases. A local filesystem and a single service security boundary are
   assumed. SQLite is not a distributed queue or an authorization boundary for OS users.
6. The adapter checks connected identity, masters and external duplicate candidates.
   It saves the returned record ID durably before reading the saved record separately.
   Exact normalized payload comparison is required for simulation verification.
7. All successful operations and authenticated rejected requests use the same company
   audit. CLI authentication errors return a generic error without attributing a
   company. A network deployment will require a separate rate-limited security log.

Original source values are inert evidence, never prompts or executable instructions.
The source SHA-256 is supplied by the trusted intake caller in this milestone; the
CLI does not ingest or hash source documents. The all-`a` digest in the example is
explicitly synthetic. A document adapter must retain and hash original bytes itself.

Secrets are environment references in private configuration. Company permissions
belong to the service, not to a tool's description or a model's memory. Local operators
with access to the process, environment or database are trusted administrators;
this milestone does not sandbox malicious Python code or arbitrary shell access.

## Durability and recovery

`jobs.sqlite3` uses explicit `BEGIN IMMEDIATE` transactions, FULL synchronous writes,
unique idempotency/source/business keys, and a partial unique index across unresolved
write states. Configuration schema and database schema are version 1; unknown versions
or a copied database with a different company binding fail closed.

SQLite triggers enforce queue invariants after restart. Jobs and idempotency aliases
cannot be deleted; prepared identity, payload and source fields cannot change;
transaction receipts cannot be rewritten; an owner may refresh master evidence before
dispatch, returning a draft/validated/queued invoice to draft and clearing approval.
The evidence history is append-only and the canonical invoice identity is retained.
Dispatch attempts are created
only while claiming queued work; and state changes follow the explicit graph. Initial
inserts must be clean drafts. Metadata is immutable and pause control remains boolean
and durable. These constraints protect against application defects. An OS-level
database administrator can still replace the file or drop schema objects.

The ordinary path is draft → validated → queued → in-flight → posted-unverified →
verified. Proven pre-write identity/master/duplicate conflicts become blocked.
A thrown exception before a receipt is persisted becomes unknown; with a receipt it
remains posted-unverified. Even an exception that probably occurred before a write is
held conservatively. Expired in-flight attempts become unknown after explicit recovery.

Recovery fences late worker completions. Reconciliation is read-only, requires the
same company binding, exactly one matching duplicate candidate and an independent
saved-record read. Missing, ambiguous or mismatched evidence stays held. No command
requeues an unknown write, marks arbitrary states, deletes evidence or releases a hold
merely because a lease elapsed. No exactly-once guarantee is claimed.

The synthetic write boundary holds a short local database transaction during the
separate local ledger write to serialize pause/recovery. This is not a design for a
network round trip. M2 now adds durable QBWC tickets, callback records, response
correlation and exact request/response persistence for a fixed read-only discovery
batch. Restart may return the exact persisted discovery request because it cannot write.
No production transaction worker exists. Never restore a generator and blindly replay
its last write, and never extend read-only replay behavior to an uncertain write.

The audit is hash chained and protected from ordinary UPDATE/DELETE by SQLite
triggers. An administrator can rewrite a database or truncate its tail; independent
signed checkpoints/immutable audit export are future production requirements.

## Milestones and acceptance gates

| Milestone | Deliverable and acceptance evidence | Status |
| --- | --- | --- |
| M0: prerequisites and inherited baseline | Isolated branch, incorporate unmerged PR #1, Windows TTL regression, prevent inherited publishing | Complete locally |
| M1: foundation | Company isolation, strict private config, authenticated CLI, durable queue/audit, duplicate controls, simulated saved-record comparison and recovery; concurrent/crash/permission tests | Implemented; synthetic tests |
| M2: SDK discovery and read-only transport | Bind authenticated QBWC connector to configured company through HCP preflight plus correlated Host/Company queries; negotiate qbXML version; persist requests/callbacks and reject cross-session replay | Implemented; SDK/QBWC real discovery and account/master reads qualified |
| M3: first real transaction adapter | Pick one verified operation in one authorized sample company; fresh master resolution, tax/account/currency validation, duplicate query, response identifiers and independent complete read-back; timeout/restart reconciliation | Controlled non-tax service sample path qualified, including actual abrupt-parent-exit recovery; broader transactions unqualified |
| M4: Hermes tools and document intake | Versioned narrow tools for prepare/validate/submit/status/lookup/verify/recover, immutable source bytes, field confidence and review, no raw qbXML/SQL; isolated Hermes profile contract tests | MCP adapter/intake implemented; installed Hermes discovery and actual MCP sample workflow passed; OCR/model workflow and corrections unqualified |
| M5: scheduling and optional collaboration | Schedule occurrence keys, dependencies, timezone, cancellation, no overlaps; notification outbox; versioned memory; delegated jobs; Kanban as a projection | Local contracts implemented and tested; external/native Hermes collaboration disabled |
| M6: reports and optional desktop/browser | Inventory each report and filters; source totals and derived calculation labels; approved capability-specific GUI flow shares duplicate reconciliation and evidence | Historical receipt register implemented/qualified; native financial reports and GUI fallback unavailable |
| M7: deployment qualification | Backup/restore, least privilege identities, TLS/QBWC authentication, resource limits, security/audit review, migration drills, per-company pilot; explicitly authorized live gate only after real tests | Private sample snapshot/restore and package qualification implemented; production onboarding/failover/external audit retention remain gated |

## Hermes workstream contracts

| Capability | Required bridge contract before enabling |
| --- | --- |
| Chat | Explicit authorized company; clarify ambiguous intent; preparation cannot imply approval |
| Documents | Source digest/locator/original values plus uncertain fields; review identity, amounts, dates, taxes and masters; hostile instructions remain data |
| Skills/tools | Versioned JSON contracts; narrow service calls; tools cannot receive raw SQL/qbXML or operator-wide credentials |
| Scheduling | Persist company, owner, timezone, cadence, occurrence and policy; unique occurrence key, overlap lock, cancellation; recheck permissions each dispatch |
| Notifications | Private company recipient allowlist, redacted payload, outbox ID; delivery failure is independent of accounting state; never resend accounting to retry delivery |
| Memory | Approved preference/mapping only; company, provenance, version, expiry; master IDs and balances freshly queried; no permissions from memory |
| Delegation | Same canonical job ID and originating identity; preparation parallel, writes serialized; no extra authority for child agents |
| Kanban | Read/projection of backend state; moving a card cannot verify, requeue uncertainty or override approvals |
| Reports | Capability allowlist, dates/filters/company, source result evidence, reconciled totals, derived calculation labels; export permissions |
| Browser/desktop | Disabled by default; specific approved workflow and observed company identity; GUI and API share business key and external reconciliation |
| Extensions | Version, supported interface, permissions, purpose, dependencies and tests recorded before activation |

Optional features must fail clearly while the local CLI continues to work. No external
schedule, recipient, board, memory store or deployment was created by this milestone.
