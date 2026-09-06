# First release scope and acceptance checklist

Agreed with the operator on 2026-09-06. This is the release target, not a claim
that all capabilities below are implemented or qualified. It supersedes earlier
suggestions that M3–M6 can be considered finished with only the current sample path.

The product is reusable across supported QuickBooks Desktop deployments. Real
company names, QBW paths, credentials, master mappings and policies stay in private
configuration. Public examples and tests use synthetic companies.

## Agreed product decisions

| Area | First-release decision |
| --- | --- |
| Transactions | Sales invoices, supplier bills, customer receipts and supplier payments |
| Items | Services and inventory products, including mixed documents |
| Adjustments | Customer credits/refunds, supplier credits, partial payments, deposits, discounts and additional charges |
| Tax | Excluded from this release by operator instruction on 2026-09-07; retain non-tax checks |
| Currency | Single-currency and multi-currency companies; base-currency-only use needs no multi-currency activation |
| Input | Manual forms, uploaded PDFs/photos/scans, Excel/CSV imports and Hermes chat |
| Review | Review before posting; configurable approval policy and separately controlled self-approval |
| Roles | Preparer, approver and administrator can be combined; individual permissions can be restricted |
| Default access | New users receive full supported Bridge permissions within explicitly assigned companies unless restricted |
| Posting | Manual, scheduled and rule-based automatic modes; manual is the default |
| Companies | One user may have multiple assigned companies, each with isolated settings, permissions, data and evidence |
| Notifications | Configured messaging channels supported by the installed Hermes deployment |
| Master records | Create and update customers, suppliers and items with permissions, review and saved-record verification |

Recording a payment means an accounting entry. Initiating bank transfers, card
charges or spending money is not included. In-app/email notification delivery is
not an agreed release requirement; normal application status views are still needed.
Other transaction families and every possible integration belong to the longer-term
roadmap unless separately added to this release.

Optional per-company use does not remove an agreed capability from the release.
A company can leave multi-currency, scheduling or a channel disabled while the
product still must qualify the supported enabled configuration. Unsupported editions,
countries or versions must get a precise capability result, not a silent substitute.

## Permission and approval contract

- Company assignment is explicit. Full permissions in Company A grant no access to B.
- Full access covers supported Bridge operations; it is not an OS account, QuickBooks
  administrator grant, transaction approval, posting-mode selection or production gate.
- New setup currently writes a concrete list of all supported permission names.
  An explicit `permissions` list, including an empty list, restricts that new operator.
  Existing principal lists are never silently expanded when configuration is loaded.
- Company administration now exposes combinable role presets, exact permissions,
  individual denials and self-approval policy through authenticated CLI/MCP contracts.
  Browser forms remain separate work under M4-02.
- A user with prepare and approve permissions may approve their own work only when
  company self-approval policy permits it. This remains a separate policy setting.
- Manual posting is the default. Scheduled dispatch must wait for required approval.
  Automatic mode must use explicit company rules/limits and route exceptions to review.
- Recheck current permissions and policy at review, submission and dispatch. Revocation
  must stop future actions even for previously queued jobs or scheduled work.
- Preserve original sources and all revisions. Edited content invalidates prior
  approval; an uncertain write is reconciled, never treated as an editable draft.

## Acceptance gates by milestone

An unchecked item is unfinished. Automated tests and actual QuickBooks/Hermes
qualification are separate evidence; a passing mock does not establish native support.

### M3 — Accounting operations and master records

- [x] **M3-01:** Non-tax service invoice in the confirmed sample: exact request,
  duplicate prevention, durable dispatch, independent read-back, abrupt-parent-exit
  recovery and refusal to resend a dispatched invoice.
- [ ] **M3-02:** Inventory and mixed invoices: fresh items/accounts, supported inventory
  settings, quantity/price calculations, saved-line and stock-effect comparison.
  The non-tax, single-currency simple-inventory/mixed path is sample-qualified: a USD15
  mixed invoice matched saved lines/balance and stock decrease from two to zero. Broader
  currency and inventory settings remain separate unqualified variants; tax is excluded.
- [ ] **M3-03:** Supplier bills: vendor binding, expense/service/inventory lines,
  payable and expense/asset accounts, due dates, terms and reference checks. Verify
  saved lines, totals and liability effects; preserve an unknown outcome without retry.
  Base-currency expense, mixed purchased-service and simple inventory bills, including
  standard Net30 terms and native inventory quantity increase, are sample-qualified.
  Advanced inventory, discounted/date-driven terms and broader variants remain unchecked.
- [ ] **M3-04:** Customer receipts: exact customer/invoice allocation, full and partial
  settlement, supported deposits/unapplied amounts, and independent balance read-back.
  Single-currency partial/full cash receipts and an explicit unapplied receipt are
  sample-qualified. Fresh Customer Balance Summary and customer balance independently
  reconcile unpaid invoices minus unapplied funds. Broader variants remain unchecked.
- [ ] **M3-05:** Supplier payments: exact vendor/bill allocation, partial settlement,
  source account and supported payment method; compare saved allocation and balances.
  Two native single-currency bank-account payments independently verified partial and
  final settlement. Vendor balance and Vendor Balance Summary agree with remaining
  complete BillToPay evidence. Broader methods and variants remain unchecked.
- [ ] **M3-06:** Customer credit notes and refunds: reference the intended customer,
  invoice/credit and accounts, avoid duplicate applications, verify remaining credit.
  One native USD5 non-tax service credit passed original-invoice limits, saved credit
  verification and independent customer balance decrease from USD25 to USD20. It was
  initially unapplied. A subsequent native USD3 application verified the invoice balance
  10 -> 7, remaining credit 5 -> 2 and unchanged customer balance 20. Both reciprocal
  links are retained with their native negative sign. A recorded USD2 Visa refund then
  verified credit 2 -> 0, customer balance 20 -> 22 and bank 510 -> 508, without card
  processing. Other refund and broader variants remain unqualified; M3-06 stays partial.
- [ ] **M3-07:** Supplier credits: correct vendor/accounts/items and bill applications;
  verify remaining payable and unused credit independently. Native USD2 expense and
  USD5 mixed service/expense credits passed source-bill limits, saved lines, independent
  CreditToApply and vendor/net payable balance changes 30 -> 28 -> 23. Vendor Balance
  Summary independently agrees. A native USD2 credit application then verified bill
  10 -> 8, unused credit 2 -> 0, vendor balance unchanged at 23 and bank unchanged at 508.
  The generated zero-amount payment stub was independently read and the held attempt
  reconciled without resend. A later two-unit USD10 inventory purchase/return verified
  stock 0 -> 2 -> 0, unused return credit USD10 and vendor/net payables 23 -> 33 -> 23.
  This qualifies simple average-cost returns; broader currency/cost/settings remain unfinished.
- [ ] **M3-08:** Discounts and additional charges: explicit line/document treatment,
  non-tax rounding rules, correct accounts and exact saved totals for each operation.
- **M3-09 — EXCLUDED:** Tax-enabled transaction qualification is outside this release.
  Non-tax validation remains enforced; taxable requests must not silently lose tax.
- [ ] **M3-10:** Single- and multi-currency behavior: transaction/base amounts,
  exchange rate/date, master currency compatibility and payment allocations verified.
- [ ] **M3-11:** Customer/vendor/item creation and updates: explicit fields and review,
  duplicate detection, stale-edit rejection, independent read-back and no record deletion.
- [ ] **M3-12:** Every enabled write family passes wrong-company, stale-master,
  revoked-permission, duplicate, missing-response, crash/restart and reconciliation tests
  plus a controlled real sample test. Preserve already verified invoice evidence.

### M4 — Input, review and user permissions

- [x] **M4-01:** Immutable source bytes/hash, field confidence and exact source review
  through narrow MCP tools; installed Hermes discovery and a sample MCP preparation run.
- [ ] **M4-02:** Manual forms use the shared service contracts for all release operations;
  explicit company, preview, errors and review are visible without technical commands.
- [ ] **M4-03:** PDF/photo/scan extraction is qualified against a retained test corpus;
  uncertain identities, numbers, dates and totals are held for review. Embedded
  instructions cannot change permissions or execution policy.
- [ ] **M4-04:** Excel/CSV intake: explicit column mapping, row errors, preview, source
  hashes and stable duplicate identity across re-imports and partial batches.
- [ ] **M4-05:** Hermes chat: operation/company clarification, retained intent and
  review, actual conversational test, no arbitrary shell/SQL/qbXML write interface.
- [x] **M4-06:** Draft correction/revision: preserve original evidence, create a new
  revision, invalidate prior approvals and stale previews, preserve canonical lineage,
  and forbid editing/resubmitting an unknown or already posted transaction. Installed
  sample correction retained the original, rejected stale evidence/preview, required a
  fresh exact master check and left the successor validated with zero accounting writes.
  Signed isolated restore preserved all 26 jobs and valid audit without service activation.
- [x] **M4-07:** Company user management: full permissions by default for a new assigned
  user, combinable roles, explicit restrictions, revocation and separately configurable
  self-approval. Cross-company denial, queued-job revocation, stale/concurrent updates
  and failed atomic replacement pass. Installed-package qualification exercised full
  default access, two/all role presets, explicit two-permission access and revocation.
  The test user was left with no grants, other principals unchanged and no accounting
  writes. CLI/MCP administration is complete; manual browser forms are tracked in M4-02.

### M5 — Posting modes and Hermes messaging

- [x] **M5-01:** Local schedule occurrence deduplication, timezones, dependencies,
  cancellation, versioned preferences, canonical delegation and read-only board contracts.
- [x] **M5-02:** Manual workflow is the default for a new company; approval and the
  deliberate posting action remain distinct and auditable. New-company setup requires
  approval and enables no dispatch gate. The manual acceptance test proves approval
  and submit perform zero writes; only deliberate native dispatch writes, with ordered audit.
- [ ] **M5-03:** Scheduled posting of approved work: persisted cadence/timezone,
  cancellation, missed-run policy, restart recovery and no overlapping dispatch.
- [ ] **M5-04:** Automatic mode: explicitly configured rules, limits, source/review
  policy and eligible operations; exceptions held for review; permission/identity/master
  and duplicate checks remain identical to manual dispatch.
- [ ] **M5-05:** Hermes messaging: verify supported channel interface/version, company
  destination and recipient authorization; redact output, deduplicate/retry deliveries
  without replaying accounting transactions; test approvals, failures and completion.
- [ ] **M5-06:** End-to-end input → review → posting → verification → notification tests
  for all three modes, including restart, revocation and notification failure.

Existing board snapshots are not scheduled accounting posting. A local outbox is
not a delivered message. Existing preferences and delegation are supporting work;
additional native Hermes Kanban/delegation interfaces are optional extensions unless
needed by the agreed workflow.

### M6 — Reports

All reports below are required in the supported first-release matrix. Every report
must show company, dates/as-of time, basis and currency where applicable, source
evidence, native totals and clearly labelled derived calculations. Test supported
filters, paging/completeness, empty results, permissions, and independent reconciliation.

- [ ] **M6-01:** Unpaid customer invoices and unpaid supplier bills.
- [ ] **M6-02:** Receivables and payables aging, with explicit aging date/buckets.
- [ ] **M6-03:** Customer and supplier statements, including payments and credits.
- [ ] **M6-04:** Profit and loss.
- [ ] **M6-05:** Balance sheet.
- [ ] **M6-06:** Cash flow.
- [ ] **M6-07:** Inventory quantities and valuation with supported location filters.
- [ ] **M6-08:** Sales analysis by customer, item and period.
- [ ] **M6-09:** Purchase analysis by supplier, item and period.
- **M6-10 — EXCLUDED:** Sales/purchase tax summaries are outside this release.
- [ ] **M6-11:** Trial balance and general ledger.
- [ ] **M6-12:** Native report support matrix and actual sample reconciliation for each
  enabled report. Unsupported requests fail explicitly; history is not current balance.

The existing historical verified-invoice register is qualified supporting evidence.
It does not satisfy the unpaid invoices, aging, financial statement or ledger gates.

### M7 — Final deployment qualification (after required M3–M6 gates)

- [x] **M7-01:** Reusable private company setup and scoped offline readiness checks;
  configured identity versus verified connection are distinguished.
- [x] **M7-02:** Signed Bridge snapshot and isolated SQLite/evidence restore drill;
  integrity and audit continuity checked without activating the restored connector.
- [ ] **M7-03:** Run the complete supported workflow matrix through the installed
  package with actual authorized sample data and representative company settings.
- [ ] **M7-04:** Operational backup and failover procedure, including authoritative
  service ownership, no simultaneous writers and uncertain-write recovery. Bridge
  snapshots and QuickBooks company backups must be identified separately.
- [ ] **M7-05:** Deployment identity, secrets, TLS lifecycle, monitoring, resource limits,
  upgrades/migrations and external immutable audit retention qualified for deployment.
- [ ] **M7-06:** Publish a traceable release-readiness report: accepted scope, supported
  versions, test evidence, operational ownership and explicit remaining limitations.

Backup and access-control work may support earlier development. It must not move the
project into final M7 qualification while required M3–M6 functionality is unfinished.
Production pilot authorization, PR merge and release publication are separate from
implementing and qualifying the software with the authorized sample company.

## Implementation order and current work

1. M3 supplier bill preparation/read-back and generalized operation lifecycle, retaining
   existing invoice invariants. First qualify a base-currency non-tax bill path.
2. Complete service/inventory transactions, payments, credits/refunds, master updates,
   discounts/charges, then currency variants for each operation. Tax is excluded.
3. Complete M4 forms/import/extraction/chat and revision/permission workflows over those
   contracts. Individual supporting changes may land earlier when required by M3.
4. Complete M5 manual/scheduled/automatic posting and Hermes notification integration.
5. Complete M6 required native reports and reconciliation.
6. Complete final M7 deployment/recovery qualification across the finished workflows.

Before implementing each native operation, verify the exact QuickBooks SDK schema,
edition/version support and saved-record projections. Do not infer support from generic
request names in the inherited library. Current supported operation families are listed in the project status; each new
family requires policy, evidence, durable dispatch and independent native readback.

Current step: **M4-04, spreadsheet intake**, then broader adjustment variants and the
remaining non-tax acceptance gates. No real company details belong here.
