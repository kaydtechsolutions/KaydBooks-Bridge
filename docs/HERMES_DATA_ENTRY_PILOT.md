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
| Review and exact human confirmation | Bridge reviews/approvals exist; trusted WhatsApp confirmation binding and batch approval are unfinished |
| Hermes-to-Windows connection | Local stdio MCP tools exist; this Linux-to-Windows deployment is not qualified |
| Web Connector master checks and dispatch from Hermes | Browser paths exist for six types; Hermes tools still contain native reads and expose no approval/posting tool |
| All eight selected types | Four have basic sample QBWC qualification: invoice, bill, customer payment, credit memo; sales receipt, journal, inventory transfer and check remain |
| Result generation and WhatsApp delivery | Job states/receipts exist; batch summary, delivery tracking and actual channel test remain |
| Multiple company files | Company isolation/private onboarding exist; each actual file needs its own binding, mappings and qualification; automatic file switching is not claimed |
| Installable pilot and recovery | Development package exists; final candidate install, walkthrough and end-to-end recovery qualification remain |

Four of eight entry types (50%) have basic installed sample qualification. This
is not the percentage of the complete Hermes workflow. The older 13/41 broad
roadmap count is historical context, not this release's completion score. No
complete upload-confirm-post-WhatsApp run has been verified yet.

## v0.1.0 milestone scorecard

Count one acceptance check for each of the eight entry types and one for each of
the seven other rows below: 15 checks total. Partial implementation earns no
fraction of a passed check. This is an equal-check completion measure, not an
estimate of time or effort.

| Milestone | Status | Verified checks | Completion |
| --- | --- | --- | --- |
| V01-1 Upload and prepare through Hermes | Local foundation exists; conversation test pending | 0/1 | 0% |
| V01-2 Exact operator confirmation | Bridge approval exists; trusted channel binding pending | 0/1 | 0% |
| V01-3 Linux Hermes to Windows Bridge | Remote connection qualification pending | 0/1 | 0% |
| V01-4 Hermes-driven QBWC dispatch | Browser foundation exists; Hermes migration pending | 0/1 | 0% |
| V01-5 Eight selected entry types | Invoice, bill, customer payment, credit memo basic sample checks passed | 4/8 | 50% |
| V01-6 Mini report in operator WhatsApp | Summary/delivery workflow pending | 0/1 | 0% |
| V01-7 Per-company onboarding/isolation walkthrough | Private setup exists; complete deployment walkthrough pending | 0/1 | 0% |
| V01-8 Installable candidate and end-to-end recovery | Final candidate/walkthrough pending | 0/1 | 0% |
| **Total** | **In development; not ready for production** | **4/15** | **26.7% (27% rounded)** |

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
