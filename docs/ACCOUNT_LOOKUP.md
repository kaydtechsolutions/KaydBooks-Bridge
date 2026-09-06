# Active account preview

The direct SDK CLI now supports `--accounts` in addition to discovery. Use the same
private configuration, credentials, principal and connector arguments documented in
[direct SDK operations](DIRECT_SDK.md), with a new numeric run ID. The principal must
have company `read` permission; explicit missing-response recovery also requires
`recover`. The result contains account records, so retain output privately outside Git.

This is a fixed preview of at most 20 active accounts. It requests only ListID,
FullName, AccountType and IsActive. It does not request balances or bank details.
`complete=false` always indicates that this is not a complete chart of accounts.
No pagination, account selection for posting, accounting mappings or account writes
are implemented. All accounting posting stays disabled.

The batch includes HostQuery and CompanyQuery in the same native session. The shared
verifier must match the operator-confirmed company before any account records are
returned to the caller. A mismatch can therefore read evidence from an unexpected
open company, but it blocks release of lookup records and never changes the binding.

The existing company-scoped SDK journal persists the exact request before dispatch
and the response before validation. A run ID cannot switch from discovery to preview
or vice versa. Same-run replay uses stored evidence; missing responses require explicit
read recovery. QBWC/direct overlap and native helper mutex rules remain in force.
The native XML allowlist permits only the fixed projected AccountQuery extension;
it accepts no user-supplied qbXML or transaction requests.

Protocol inventory: the official Intuit [AccountQuery request schema](https://static.developer.intuit.com/qbSDK-current/common/newosr/qbsdk/json/AccountQueryRq.json?v=13)
documents MaxReturned, ActiveStatus and IncludeRetElement (US version 4 onward).
This adapter retains the reviewed US qbXML 17.0 transport restriction. It accepts only
explicit success (status 0 / Info); unsupported queries, warnings, missing fields,
inactive/duplicate records, wrong correlation and over-limit responses are blocked.
The inherited iterator-capability inventory is not evidence that AccountQuery supports
iterator pagination; this adapter does not use iterators.

Real sample-company test: 20 projected records returned and company binding verified.
A separate Python process replayed the same result without dispatch and verified the
audit chain. Records and raw XML remain private. Synthetic tests cover invalid records,
correlation mismatch, wrong company, recovery, operation immutability and projection.
Account preview through QBWC is also implemented and real-tested on the sample:
20 records, progress 100, clean closure, verified binding and audit integrity.
Hermes and production companies remain unqualified.

## Queue one QBWC lookup

Use `python -m kaydbooks_bridge.qbwc_accounts` with `--config`, `--credentials`,
`--principal`, `--connector`, `--job` and `--enqueue`. Paths and secrets remain private;
job IDs use the ordinary Bridge identifier rules. Omit `--enqueue` to retrieve the
result with current company read permission. The result contains private records.

Enqueueing persists one bounded account preview for the connector's next newly
authenticated session. An already-active discovery session is not modified. One
pending job per connector is allowed, and repeating the same job ID is idempotent.
Ownership and assigned ticket are immutable. A consumed job is never silently moved
to another session after expiry/disconnect; inspect its state and explicitly queue a
new read job if needed. Later updates return to ordinary discovery.

The callback rechecks the originating actor's company read permission before returning
requests and validating responses. US qbXML 4+ and matching HCP company evidence are
required before issuing the account request. The callback uses the negotiated version,
persists the exact batch, and shares the direct SDK account-response validator and
company verifier. Duplicate callbacks and restart reuse the same session evidence.
Result retrieval revalidates the binding before returning projected records.

No extra QWC import or permission grant is required for an already-qualified connector.
Keep Auto-Run disabled and trigger one manual update for each explicitly queued test.


## Exact account lookup through QBWC

Add `--list-id` when enqueueing to request one exact account instead of the preview.
The selector is immutable for the job; use a new job ID to select another account.
Result retrieval can omit the selector and uses the persisted one. Only the requested
active account is released, after the same company, permission and correlation checks.
Missing, inactive, extra or different records fail closed. SDK warning statuses remain
blocked rather than being interpreted as a successful no-match.

The fixed request uses ListID instead of MaxReturned/ActiveStatus, following the
Intuit AccountQuery choice schema linked above. Both QBWC and direct SDK support this option. Exact lookup has synthetic callback
and restart coverage. A real sample-company QBWC lookup also returned exactly the
requested active account, completed with progress 100 and clean closure, and passed
company-binding and audit verification. Fresh-process retrieval revalidated persisted
evidence. Raw results remain private. It is not account-type approval for any posting
operation, production qualification or interrupted-query recovery. Posting stays disabled.


## Exact account lookup through direct SDK

Use `--list-id` instead of `--accounts` on the direct SDK CLI. The two modes are
mutually exclusive. A new numeric run ID binds the exact selector in the immutable
request; the same run cannot change selector or switch to discovery/preview.
Response validation requires exactly the requested active account. Native request
validation allows only the fixed ListID query and the four projected fields.

Real sample-company test returned one matching record with verified company binding
and audit. The first attempt stopped before dispatch because QuickBooks reported a
modal; after restoring its window, explicit read recovery completed successfully.
A fresh Python process replayed the persisted result with dispatch disabled. This is
not qualification of interruption inside ProcessRequest or production accounting.
All exact records and native evidence remain private. Posting remains disabled.
