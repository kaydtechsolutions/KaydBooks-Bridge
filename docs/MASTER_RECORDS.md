# Reviewed customer, supplier and item changes

The `master.change` operation prepares explicit creation or update proposals for
customers, suppliers, sales services, purchased services, simple inventory items,
fixed discount items and additional-charge items.
The browser's **Customers, suppliers & items** view and narrow MCP preparation tools
use the same immutable sources, review, permissions, approvals and job lifecycle.
Company identity, account mappings and authorization remain private configuration.

## Supported fields and boundaries

| Record | Creation | Update |
| --- | --- | --- |
| Customer / supplier | Flat name, active flag, company name, phone, email | Same explicit fields |
| Sales service | Flat name, active flag, sales description/price, income account role | Name, active flag, sales description/price |
| Purchased service | Sales fields plus purchase description/cost and expense account role | Name, active flag, descriptions, price/cost |
| Simple inventory | Sales/purchase fields and income, cost-of-goods and asset account roles | Name, active flag, descriptions, price/cost |
| Fixed discount | Flat name, active flag, description, fixed discount amount, income account role | Name, active flag, description, fixed amount |
| Additional charge | Same sales-only or sales-and-purchase fields as service items | Preserve the original sales/purchase aggregate; update explicit fields |

New records have no opening balances or stock. Account changes that could affect
historical transactions, deletions, parent/child structures, unit-of-measure changes,
tax fields and raw native commands are unavailable. Percentage discounts/prices and
special charge items are also unavailable. Service and charge updates preserve the
observed sales-only or sales-and-purchase aggregate. This implementation qualifies
single-currency US QuickBooks Desktop with qbXML 17; broader currency and inventory
variants remain separate acceptance work.

The SDK field order and limits follow Intuit's
[Onscreen Reference](https://static.developer.intuit.com/qbSDK-current/common/newosr/index.html).
Updates use the original ListID and EditSequence, as described in the
[SDK Programmer's Guide](https://static.developer.intuit.com/qbSDK-current/doc/pdf/QBSDK_ProGuide.pdf).

## Review and execution

1. Select the assigned company and record kind. For an update, read the exact
   existing ListID; the original projection and EditSequence remain visible.
2. Enter only supported fields. Fresh read-only checks verify company identity,
   name collisions across entities/items, original revision and account types.
3. Save and review the immutable proposal. Field edits invalidate checks. A draft
   correction creates a successor and invalidates prior approval.
4. An authorized reviewer approves when required. Submission and posting are
   distinct actions; preparation tools cannot write accounting data.
5. A bounded, expiring private `sample_master_posting` gate limits connector,
   master kinds, name prefix and cumulative attempts. Sample updates additionally
   require proof that this Bridge created and independently verified that record.
6. Immediately before the native write, recheck grants, source review, approval,
   identity, policy, original revision, collision and account evidence. All write
   families share the unresolved-write fence and company SDK lock.
7. Independently read the saved record and compare requested and preserved fields.
   A creation carries the durable job's ExternalGUID. A lost response becomes
   uncertain and requires read-only reconciliation; it is never automatically resent.

Recovery requires the original helper to close and its durable write-intent hash
to match the stored request. Exact identity, preserved values and native revision
are retained in the verified receipt. Interrupted read-only observations also retain
their original timestamps and can be recovered without manufacturing fresh evidence.
Master changes are currently manual; accounting schedules do not select this operation.

## Interfaces

MCP adds `master_lookup_v1`, `check_master_change_v1` and
`prepare_master_change_v1`. Browser actions use the same contracts, including
`master-lookup`, `check`, `prepare`, approval, submission, sample posting and
reconciliation. Private account-role mappings include `master_income`,
`master_expense`, `master_cogs`, `master_asset` and `master_discount`; each selected native account is
checked for the required type and active state.

Automated tests cover six record kinds, explicit native shapes, preservation,
duplicate names, stale targets, source/approval lifecycle, late revocation,
missing responses, request-intent tampering and independent recovery. Headless
browser tests cover fresh checks and original-record selection. These synthetic
tests do not themselves establish installed QuickBooks qualification.

Installed-package qualification on the confirmed sample created five isolated
masters (customer, supplier, sales service, purchased service and inventory), then
updated only those five records. All ten attempts have independently verified
receipts, zero opening balances/stock and valid audit. Unapproved submission and
duplicate posting were rejected. Customer and supplier balance summaries remained
USD32 and USD23. Both initial response-handling exceptions were recovered with
zero additional writes.

Actual responses exposed two behaviors now covered by regressions: repeated
additional contact aggregates outside the reviewed projection, and omitted item
ExternalGUIDs when `IncludeRetElement` is used. Item identity reads use one exact
ListID/name without return-field filtering, then retain the reviewed projection.
The complete raw response remains private evidence; duplicate selected fields fail.

Installed desktop/mobile review, field-change invalidation and exact original
lookup passed over verified TLS. A signed isolated restore preserved all 42 jobs
and 1,839 files, including ten immutable master attempts, evidence links and
verified receipts. Integrity/audit passed, the restored company was paused, and
no restored service started. The live sample company is paused and its ten-attempt
master quota was exhausted. No existing business records were edited or deleted.

A subsequent bounded qualification created and updated a fixed discount, a sales
charge and a purchased charge. All six attempts independently verified, with the
purchased aggregate preserved on update. Unsupported percentage/special items and
account edits are rejected. The installed discount form read the exact updated record,
invalidated its check after an edit, and passed desktop/mobile checks without writes.
Fresh customer/vendor summaries remained USD25/USD15 after these master-only changes.
A signed isolated restore preserved 51 jobs, 2,104 files and all 16 verified master
attempts with valid integrity/audit, paused and without service activation. The final
suite passed 1,184 tests. Using these items on transactions is separate M3-08 work.
