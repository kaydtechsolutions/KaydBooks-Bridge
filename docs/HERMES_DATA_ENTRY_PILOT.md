# KB v0.1.0: reviewed data entry through Hermes

This is the active release target selected by the operator. It supersedes broader
daily-report, customer-statement and outbound-customer-messaging work for v0.1.0.
The reference to v1.0.0 inside the requested workflow is treated as v0.1.0 throughout;
it does not select a second Bridge installation or publish a release.

## The complete workflow

1. Upload transaction documents or a spreadsheet to Hermes and select a configured
   company. Hermes retains source provenance and prepares the proposed entries.
2. Hermes shows the company, batch identifier/revision, transaction types, dates,
   references, parties, items/accounts, quantities, amounts, currencies and totals.
   Missing fields and uncertain extraction must be resolved with the operator.
3. The operator explicitly confirms that exact preview. Approval must bind the
   authenticated operator, company, source/payload hashes and batch revision.
   Changed entries require a new preview and confirmation. Uploaded instructions,
   model-generated approval text and confirmation of a different batch cannot approve it.
4. Hermes requests dispatch of the confirmed entries through Bridge. Web Connector
   polls the Windows Bridge service and exchanges qbXML with QuickBooks Desktop.
   Bridge checks company identity, permissions, fresh evidence and duplicates at
   dispatch. Each entry retains its own job and idempotency key.
5. Bridge reads back saved transactions and verifies accounting/stock effects.
   Queued, dispatched and posted-unverified do not mean verified completion.
6. Hermes sends the operator a brief result in the configured Hermes WhatsApp chat.
   Results come from durable Bridge evidence. A failed message delivery never causes
   another accounting write; uncertain posting is held for read-only reconciliation.

The confirmation is a transaction approval, not a general permission to change
any company. Existing reviewer separation remains until a trusted channel-to-user
approval integration is implemented and qualified; an agent holding a preparer's
token must not impersonate the reviewer.

## Included entries

Sales receipt, invoice, credit memo, customer payment, bill, journal, inventory
transfer and check. Preserve the previously selected eight types; this workflow
request does not drop four of them to claim an earlier completion.

Tax is excluded. Unsupported transaction variants must be identified before
confirmation. Each company has its own enabled operations, currency, limits and
master mappings. Supplier payments and bill credits already implemented remain
available within their qualified scope but are not new release requirements here.

## Result message contract

Example format (illustrative, not a record of actual posting):

```text
KB v0.1.0 | company-a | Batch B-104, revision 2
Requested: 8 | Verified: 6 | Rejected: 1 | Held: 1 | Pending: 0
Verified: 3 invoices, 2 bills, 1 customer payment.
Rejected: row 7, unknown account. Held: row 8, outcome awaiting verification.
Review: batch B-104 in KaydBooks.
```

Identify every exception by source row/job/reference and next action. Do not total
different currencies or net unrelated transaction types into a misleading amount.
Keep accounting status and notification status separate. Store destination,
batch/result revision, attempt state and provider acknowledgment where available.
Do not claim handset delivery/read without corresponding channel evidence. The
recipient is the operator's configured chat, not customers or numbers in uploads.

## Release acceptance and current evidence

| Requirement | Current evidence / remaining work |
| --- | --- |
| Capture and extract uploads, prepare rows | Local tools exist; the actual Hermes WhatsApp upload-to-batch conversation is not qualified |
| Review and exact human confirmation | Actual operator DM confirmed the exact immutable sample invoice batch; separate reviewer approval recorded |
| Hermes-to-Windows connection | Actual Linux SSH/MCP uses the same durable Windows state as Web Connector |
| Web Connector master checks and dispatch from Hermes | Confirmed sample invoice dispatched once and saved invoice fields verified through QBWC readback |
| All eight selected types | All eight have basic sample QBWC qualification within documented limits: sales receipt, invoice, bill, customer payment, credit memo, journal, expense check and site transfer |
| Result generation and WhatsApp delivery | Deterministic verified batch result accepted by WhatsApp provider; preview and result IDs retained; handset read not inferred |
| Multiple company files | Company isolation/private onboarding exist; each actual file needs its own binding, mappings and qualification; automatic file switching is not claimed |
| Installable pilot and recovery | Development package exists; final candidate install, walkthrough and end-to-end recovery qualification remain |

All eight entry types (100%) have basic installed sample qualification. This
is not the percentage of the complete Hermes workflow. The older 13/41 broad
roadmap count is historical context, not this release's completion score. No
complete operator-upload conversation has been verified yet. The controlled-source
preview-confirm-post-result path has passed in the sample company.

## v0.1.0 milestone scorecard

Count one acceptance check for each of the eight entry types and one for each of
the seven other rows below: 15 checks total. Partial implementation earns no
fraction of a passed check. This is an equal-check completion measure, not an
estimate of time or effort.

| Milestone | Status | Verified checks | Completion |
| --- | --- | --- | --- |
| V01-1 Upload and prepare through Hermes | Remote source capture verified; full upload/prepare conversation pending | 0/1 | 0% |
| V01-2 Exact operator confirmation | Real native operator reply accepted for the exact sample batch | 1/1 | 100% |
| V01-3 Linux Hermes to Windows Bridge | Same-state SSH/MCP read and repeatable source capture verified | 1/1 | 100% |
| V01-4 Hermes-driven QBWC dispatch | One confirmed sample invoice dispatched and matched to saved QBWC readback | 1/1 | 100% |
| V01-5 Eight selected entry types | All eight basic sample checks passed; supported variants remain bounded | 8/8 | 100% |
| V01-6 Mini report in operator WhatsApp | Verified batch result has durable provider acknowledgment | 1/1 | 100% |
| V01-7 Per-company onboarding/isolation walkthrough | Private setup exists; complete deployment walkthrough pending | 0/1 | 0% |
| V01-8 Installable candidate and end-to-end recovery | Clean bundle installation and signed batch restore passed; end-to-end recovery remains open | 0/1 | 0% |
| **Total** | **In development; not ready for production** | **12/15** | **80%** |

Include this scorecard in major progress updates. Name the milestone changed and
the evidence closing its check. Do not increase completion for a commit, documentation
update, queued transaction or skipped test. Publishing is separate from candidate
acceptance and still requires authorization.

Finish only work needed for this workflow and its selected entry types. Automatic
daily financial reports, statements to customers, other messaging destinations,
new transaction types and broader roadmap expansion are deferred.

## Installation and authorization

Use the [multi-company installation guide](INSTALL_HERMES_DATA_ENTRY.md). Current
installation is a development/qualification setup, not a ready production release.
All company identities, paths, secrets, account/item mappings and chat destinations
remain outside Git. The existing confirmed-sample authorization remains valid.
Adding a production file to configuration does not authorize accounting writes.
Publishing/merging and production accounting still require separate authorization.
This request selects operator WhatsApp result delivery as a feature; the exact
destination must be supplied before an actual message test can be performed.

## Journal, check and transfer qualification completed

The existing journal's approved line memos were restored through one bounded,
separately approved sample-only Mod; no replacement journal was created. Saved
memos, original transaction/line IDs and the original USD5 accounting effect match.
The USD5 expense check passed bank -5, expense +5 and unchanged vendor payables.
The one-unit site transfer passed source -1, destination +1 and unchanged total
stock/average cost. Each new transaction retained one Add, exact saved fields,
valid audit and duplicate refusal. General posting is paused with no unresolved
sample write. Their [supported scope and recovery details](QBWC_JOURNALS_CHECKS_TRANSFERS.md)
remain explicit. These complete V01-5; three other scorecard checks remain.

## Hermes connection and reviewed-batch implementation

The actual Linux Hermes host now reaches the same durable Windows database used by
Web Connector. A remote MCP lookup returned an existing verified journal; repeated
original-source capture reused the same document ID and hash. This closes V01-3.
The stdio launcher uses the physical Windows state path: packaged-app path
virtualization must not create a second empty database when SSH launches Python.

An explicitly authorized connection message was accepted by the selected operator's
existing WhatsApp bridge and returned a provider message ID. Phone delivery/read
receipt and the complete batch result workflow are not inferred from that response.
The previous home chat remains unchanged.

Exact source-bound batch previews, separate native-chat confirmation, serial QBWC
dispatch and deterministic results are implemented with simulated regression checks.
The signed private channel is not exposed as an MCP approval tool. Wrong chat,
sender, group, owner-generated message and attachment-derived confirmations are
rejected. A result waits for verified readback; uncertain writes stop the batch.
A durable delivery claim prevents automatic resend after an unknown network result.
Live inbound confirmation and batch-result delivery subsequently passed for one
controlled USD5 service invoice. Installing the plugin in the actual named Hermes
profile fixed the previously unknown confirmation command. After the first review
expired, fresh evidence revalidated the same unposted job and a new review replaced
the old one. The real operator confirmed that new review; one QBWC attempt created
the invoice and matched its saved reference, lines, USD5 subtotal, zero tax and USD5
balance remaining. Both preview and final result have provider acknowledgments.
Posting was paused after verification and the audit remained valid. Private proofs
retain the job, transaction, native event and provider IDs outside Git.

This test used a controlled source captured through remote MCP, not an actual
operator attachment conversation or a paid model run. It closes V01-2, V01-4 and
V01-6, while V01-1, V01-7 and V01-8 remain open. The invoice receipt verifies the
saved transaction, not a fresh full customer-balance report.
See [channel integration](HERMES_CHANNEL.md) for installation and trust boundaries.
