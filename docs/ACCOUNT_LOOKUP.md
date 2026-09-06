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
Account preview has not been integration-tested through QBWC; its callback service
continues discovery only. Hermes and production companies remain unqualified.
