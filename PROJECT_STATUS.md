# KaydBooks Bridge project status

Tax functionality and tax reports are excluded from this release by operator instruction
on 2026-09-07. Non-tax checks remain enforced. 28 acceptance gates remain unfinished:
M3—9, M4—2, M5—2, M6—11, M7—4. Master records M3-11, manual, scheduled and bounded automatic modes
M5-02/03/04, company access M4-07, draft revisions M4-06, spreadsheet intake M4-04,
and offline document extraction M4-03 pass.

Master creation/update now uses shared sources, review, approvals and durable native
attempts. Five isolated sample masters (customer, supplier, sales service, purchased
service and inventory) were created and then updated, with ten independently verified
receipts. Two native response quirks were fixed and the original attempts recovered
without repeat writes. No existing business record was changed or deleted. Customer
and vendor balance summaries remain USD32 and USD23; new stock and balances are zero.
The company is paused and the ten-attempt master quota is exhausted.
Installed browser master review passes on desktop/mobile over verified TLS. Signed
isolated restore preserved all 42 jobs and 1,839 files, including every master attempt
and evidence link, with valid integrity/audit and no restored service activation.
The clean full run passed 1,119 tests; subsequent native-response regression work
passed all 31 master tests. The adapter now exposes 38 narrow MCP tools.
See [master records](docs/MASTER_RECORDS.md).

Updated: 2026-09-07. Production posting: DISABLED. Four sample invoices (USD45 total)
and five USD10 sample bills have verified receipts. Three customer receipts totaling USD20
settled the mixed invoice and left USD5 unapplied. Before the credit test, Customer Balance Summary and CustomerRet both reported USD25,
matching USD30 unpaid invoices minus USD5 unapplied.
Two USD5 supplier payments settled the mixed bill; Vendor Balance Summary and complete
BillToPay independently report USD30 across three unpaid bills. One USD5 unapplied service
credit reduced customer balance to USD20. Applying USD3 of that credit left USD2 unused
and the original invoice at USD7. A recorded USD2 Visa refund then exhausted that credit,
increased customer balance to USD22 and reduced the bank balance 510 -> 508. No transaction
was resent and no payment processor was invoked. Two subsequent supplier credits
(USD2 expense and USD5 mixed expense/service) reduced vendor balance and net payables
30 -> 28 -> 23. Fresh Vendor Balance Summary independently reports USD23. A USD2 supplier-credit
application subsequently reduced the bill 10 -> 8 and unused credit 2 -> 0, leaving
vendor 23 and bank 508 unchanged. The generated zero-amount payment stub was
independently queried and the original held attempt reconciled without resending.
A subsequent USD10 inventory bill and supplier credit independently verified stock
0 -> 2 -> 0 and vendor/net payables 23 -> 33 -> 23, both on their first attempts.

Company user administration is installed-qualified: default full supported assigned-company
permissions, combined preparer/approver/administrator roles, exact restrictions and empty
grants. Self-approval is explicit and rechecked on submit/dispatch. The test principal is
retained with no grants; other principals were preserved. No accounting writes occurred.
The operator's new manage-users grant was an explicit local bootstrap under standing authorization.

Browser workspace now ships in the wheel at `/app`, with company selection, nine current
operation forms, immutable sources, draft correction/review, CSV/XLSX mapping and row
preparation, native reports and company access controls. Its narrow authenticated API
rechecks current permissions and retains existing approval/dispatch limits. Native sample
qualification created one additional USD5 service draft and rendered a complete 16-row
P&L over verified TLS. Desktop/mobile layouts passed, no browser errors occurred, and no
accounting transactions were written. The service now starts from the installed wheel's
private runtime instead of the repository checkout. Original startup settings were backed
up privately. This is supporting M4-02 work; the complete release matrix remains partial.
Signed installed-package restore preserved all 29 jobs and 1,539 files with valid audit,
paused and without starting a restored service. See [browser workspace](docs/BROWSER_WORKSPACE.md).

Offline document intake now ships with optional Python decoders and pinned local Node 22 /
Tesseract.js 7.0.0 English models. Six retained synthetic PDF/image fixtures passed actual
OCR, including conflicting values and embedded instructions. Every prepared field is
held for explicit source review; original observations/source hashes survive retry and
restore. Decoders receive no Bridge credentials. Installed sample upload and native
master checks produced one additional held draft, bringing Bridge history to 30 jobs,
with zero accounting writes. The fixture's party mapping remains unreviewed. M4-03 is
complete for the documented printed-English matrix; unsupported or illegible material
requires manual review. Signed installed restore preserved all 30 jobs and 1,549 files,
with valid audit/integrity, paused and without starting a restored service.
See [offline document extraction](docs/DOCUMENT_EXTRACTION.md).

Scheduled and rule-based automatic dispatch now share all nine native operation
fences. Immutable profiles/occurrences/claims reserve exact jobs, fingerprints and
budgets; cancellation and current permissions/policy are checked at final native
authorization. Restart resumes only an undispatched queued claim; an uncertain native
attempt requires reconciliation and is never resent. Browser rules require review,
and a separately started installed worker is opt-in. No accounting MCP tool was added.

Installed qualification wrote two separately approved USD5 service invoices, one per
mode, and independently verified both saved records. A fresh Customer Balance Summary
reports USD32, matching the prior USD22 plus USD10. Restarted workers performed zero
repeat writes. Both profiles are cancelled and the company is paused. The private
invoice quota allowed exactly two new attempts and is now exhausted. Six sample
invoices totaling USD55 have verified receipts; no production writes occurred.
Signed isolated restore preserved all 32 jobs and 1,623 evidence files, including both
cancelled profiles, claims and results, with valid integrity/audit and no restored service.
Desktop/mobile browser checks passed over verified TLS. See [dispatch modes](docs/DISPATCH_MODES.md).

## M3â€“M7 sample implementation and qualification

### Current release plan: complete M3â€“M6 before final M7

- RESOLVED: the balance discrepancy. BillRet.OpenAmount was USD20 while each sample
  bill's exact-vendor/AP BillToPayQuery reported USD10. VendorQuery independently
  reported USD20. The bridge now requires bill-specific payable evidence; OpenAmount
  remains diagnostic. The held Net30 bill was reconciled without resending BillAdd.
- Supplier bills support base-currency expense, purchased-service, mixed and simple
  inventory lines, private account mappings and standard zero-discount Net30 terms.
  Four USD10 bills passed native saved-line and independent payable verification.
  The inventory bill also verified quantity increasing from zero to two, average cost
  USD5 observed. One isolated purchase-service and one zero-stock item were created.
- Non-tax service, simple inventory and mixed sales invoices share source/master checks,
  durable authorization, native attempts and independent saved-line verification.
  A USD15 installed-package mixed invoice matched its balance and stock decrease from
  two to zero. Original stock baselines are retained; sold-out recovery never resends.
  Ambiguous or missing stock evidence keeps the transaction unverified.
- Customer payments support exact allocation, partial/full settlement and explicit unapplied
  amounts through the durable lifecycle. Native USD5 and USD10 receipts settled the USD15
  invoice; a third USD5 receipt remained unapplied. Signed AppliedAmount, related invoice
  payments and customer-wide unused credits are handled without double-counting.
- Latest validation: 1,087 full-suite tests pass, including 22 dispatch, 16 browser and 15 intake cases. Lint, 35-tool stdio checks and
  source/wheel builds pass. Eighteen native single-currency report types returned
  complete rows/columns. Independent P&L/balance-sheet/trial-balance/ledger comparisons
  and customer/vendor statement/balance/aging reconciliation passed. Extra native reads
  cover cash basis, empty periods, exact entity/item filters and monthly grouping.
  Installed-package qualification revalidated all 18 retained report responses and
  completed a fresh native P&L read with zero accounting writes. Original read timestamps
  remain stable on cache/recovery. Signed isolated restore preserved 28 jobs and 1499
  files with valid integrity/audit, paused and without starting a restored service.
  CI62 passed spreadsheet intake; native-report CI63 also passed.
- Supplier payments now pass exact vendor/AP/Bank mapping, partial/full settlement,
  independent saved allocation/payable checks and missing-response recovery tests.
  Native sample readbacks verified the bill balance decreasing 10 -> 5 -> 0.
- Customer service credits enforce original-invoice/customer/AR identities, item limits
  after prior Bridge-linked credits and complete non-tax source evidence. One installed
  native USD5 credit passed saved-line, fully unapplied CreditRemaining and separate
  customer-balance verification on its first attempt. A subsequent USD3 link-only
  application now verifies invoice 10 -> 7, credit 5 -> 2 and customer balance unchanged
  at 20. The native reciprocal link sign mismatch was corrected and reconciled without
  resending. A recorded USD2 credit-card refund now independently verifies remaining
  credit 2 -> 0, customer balance 20 -> 22 and bank balance 510 -> 508. The native query
  omits CreditRemaining inside the refund allocation; the separate exact credit query
  remains mandatory. Broader refund variants remain unfinished; M3-06 stays partial.
- Supplier credits now support non-tax single-currency expense/service and mixed lines,
  original-bill limits after prior Bridge-linked credits, immutable dispatch, independent
  CreditToApply unused amounts and net payable/vendor balance checks. Two native tests
  passed on the first attempt. Supplier-credit application and simple average-cost
  inventory returns are now sample-qualified. Broader currency/cost/settings remain partial.
- Current step: browser workflow qualification, posting schedules and broader adjustment variants, then remaining
  M3–M6 acceptance gates. Sample posting is paused between bounded tests.
- The [first-release checklist](docs/FIRST_RELEASE_SCOPE.md) is the release target;
  these qualified simple paths do not complete the wider currency/advanced variants.
  M3â€“M6 remain partial; final M7 follows their required acceptance gates.
- New setup operators receive all currently supported permissions within explicitly
  assigned companies by default. Explicit restrictions are respected. Required reviews,
  company binding and production/sample posting gates remain enforced. Role management
  and separately configurable self-approval pass through CLI/MCP; browser forms remain work.
- CLI sample invoice/bill/payment commands and 33 narrow MCP tools are implemented. Native
  attempts cannot enter simulation recovery; bill receipts are excluded from the
  historical invoice register. All company identity, sources and mappings stay private.

### Reusable company onboarding

- Added a private setup command with independent credentials, operator-only directory
  permissions, unconfirmed identity, full scoped grants by default and no posting gate. Existing
  configurations, company files and registrations are never overwritten or activated.
- Added offline checks scoped to an explicit company, principal and connector.
  Missing credentials, missing file, unconfirmed binding, missing mappings, revoked
  read permission and reused secrets remain visible without exposing private values.
- Actual private sample setup drill and a fresh native SDK company query passed.
  The existing configured identity was verified; zero accounting writes occurred.
  File existence is checked separately from live identity; native helpers use the
  currently open company, not an automatic file switch.
- Validation: 580 local tests pass, including 12 onboarding cases; lint, formatting,
  package build and the separately installed setup entry point pass. The existing
  HTTPS service remains ready with valid audit and no unresolved accounting writes.
- See [company setup](docs/COMPANY_SETUP.md). Production authorization is not a blocker
  for this reusable development and sample qualification path.

| Milestone | Current implementation/evidence | Remaining scope |
| --- | --- | --- |
| M3 | Base-currency invoices/bills, service/simple inventory/mixed lines, receipts/payments, credits/applications/refund and inventory supplier return; native readback and recovery | Broader adjustment/terms/currency/settings variants, master-management interface and full failure matrix |
| M4 | 35 narrow MCP tools; immutable sources/revisions, field review, spreadsheet intake, local OCR corpus, access management and browser forms for current contracts | Complete release forms/transaction selection and actual Hermes conversation |
| M5 | Manual posting default; local board schedules/deduplication, dependencies/cancellation, outbox previews, preferences and delegation | Scheduled/automatic accounting dispatch, configured Hermes delivery and end-to-end qualification |
| M6 | 18 native single-currency reports, exact dates/basis, complete rows and independent financial/balance reconciliation; historical receipt register | Cash flow, currency/location variants and complete supported report matrix |
| M7 | Private setup, installed service and signed quiescent 30-job restore with integrity/audit checks | Final supported workflow matrix, operational ownership/failover, retention/monitoring and readiness report |

- Actual MCP qualification retained original source bytes, ran a fresh sample master
  lookup, prepared/validated/previewed/queued a document invoice, preserved its duplicate
  job identity and rejected a different company. No accounting write tool is exposed.
- Uncertain values require review-source permission and exact fingerprint/value
  confirmation. Original uncertainty is retained and revoked reviewer grants block use.
  This earlier MCP run included no model calls or OCR qualification; the later local
  OCR corpus is documented separately above.
- Local staging workflows passed; the test schedule was cancelled. Outbox entries are
  local previews, never deliveries. The report matched three historical receipts totaling
  USD30; it does not claim current balances or a complete company ledger.
- An isolated signed restore recovered all five staged jobs with valid SQLite integrity
  and audit continuity. No restored service or production connector was started.
- Validation baseline: 568 tests passed, including actual stdio protocol tests against
  synthetic state. Native QuickBooks and installed Hermes proofs remain separate/private.
- Current authorized sample path is implemented. Optional integrations and production
  readiness remain explicitly bounded as above; no merge or release is authorized.

See [Hermes tools](docs/HERMES_TOOLS.md), [local workflows](docs/LOCAL_WORKFLOWS.md),
and [deployment qualification](docs/DEPLOYMENT_QUALIFICATION.md).

## M3 implementation: durable controlled sample posting

- Added an explicit `post-sample` grant and private expiring, reference-prefix, count-limited
  authorization. Company amount limits still apply. The staged gate permits at most three
  non-tax service test invoices in the confirmed sample company, bounded at USD100 each.
- Exact request and attempt are persisted before a single native session rechecks company,
  masters and duplicate reference. Parent authorization binds the validated request hash;
  a per-attempt durable fence precedes InvoiceAdd. Unknown outcomes cannot be resent.
- Independent read-only reconciliation resolves by reference and then verifies by TxnID,
  preserving receipt provenance and original dispatch context. QBWC excludes uncertain
  native writes. Simulation cannot consume a dispatched native job.
- Validation: 547 tests passed; lint, formatting and complete native helper compilation
  passed. Tests cover lost responses, restart reconciliation, duplicate prevention,
  changed grants/policy/binding, and native request tampering. These posting tests are synthetic.
- Real preparation passed with retained source bytes/hash and fresh native master evidence.
  QuickBooks permission was granted. One USD10 invoice completed normally; a second USD10
  invoice passed abrupt-parent-exit recovery and duplicate dispatch refusal in a fresh process.
- BLOCKER: none for the controlled sample path. Production and optional integrations remain
  outside the qualified scope. No production data changes, merge or release occurred.

The following sections retain historical evidence; their older next-action and permission
statements are superseded by this current section and the operator's M3-M7 authorization.

## Latest implementation: Web Connector receipt parity

- Added immutable exact-TxnID receipt checks to the existing one-update invoice queue.
  Both transports share request projection, saved-record validation, company binding and
  the authenticated receipt attachment path. Replays recheck current grants and context.
- Added fresh `verify-receipt` for completed jobs. It appends an independent observation
  without replacing the historical receipt or changing the job/transaction.
- Migrated receipt storage atomically to support both transports, preserving existing SDK
  receipts and company-level transaction uniqueness. A private pre-upgrade backup was saved.
- Validation: 535 tests pass (532-test full regression plus three new CLI/grant cases).
  Original SDK receipt preservation, QBWC callback replay/errors, current policy, stale
  observations, queue exclusion and migration are covered. General posting stays disabled.
- Real qualification passed after the operator ran the queued Web Connector update.
  The session closed successfully; the exact saved invoice matched through QBWC and the
  fresh verification action appended its audit proof. The original SDK receipt and job
  were preserved, the audit chain verified, and zero new accounting writes occurred.
- BLOCKER: none for receipt parity. The staged service remains ready with posting disabled.
- NEXT: a separately gated real transaction adapter with durable dispatch and reconciliation.

## Previous implementation: durable invoice receipts and shared job reconciliation

- Added a fixed read-only SDK invoice query by TxnID. Current company binding, exact
  payload/policy, durable request and response, owner and permissions are verified.
- Added authenticated `attach-receipt` with recover/read/validate permissions. It atomically
  persists immutable receipt evidence, completes an owned undispatched job and appends audit.
  Receipt provenance explicitly records an externally saved invoice, not a Bridge dispatch.
- The approved sample invoice passed real readback and attachment. Its shared job now holds
  the verified TxnID with no dispatch attempt. Restart, duplicate prevention and audit passed;
  zero new invoices were written. General posting remains disabled.
- Duplicate preparation retains terminal receipts even after master evidence expires;
  conflicting payloads, stale new observations, state regression and receipt replacement fail.
- Validation: 516 tests pass (515-test regression plus the new CLI case), including
  native receipt allowlist, current grants, timestamp replay, migration and atomic rollback.
  Lint, formatting and source/wheel builds pass. Real sample receipt attachment passed.
- NEXT: QBWC receipt parity and a separately gated posting adapter. No new sample invoice
  writes are included in the consumed one-invoice authorization.

## Previous qualification: one approved sample invoice and independent receipt checks

- Operator explicitly approved one non-taxable service invoice of 10.00 in the confirmed
  sample company. Fresh SDK master evidence and the validated review were checked before
  dispatch; the isolated helper rechecked company, preferences, customer/item/account/code
  values and absence of the invoice reference within the native session.
- Exactly one InvoiceAdd request was sent. Its durable intent preceded dispatch; exact
  request hash was fixed to the approved snapshot. Returned TxnID and response were saved
  before querying by TxnID. The add response and query matched the approved invoice.
- A separate read-only native session queried the reference and verified the same invoice.
  A repeat invocation was refused by the persisted intent guard before opening an SDK
  session. Receipts and audit chain verified; no blind retry, second invoice or void.
- Added pure request construction and strict saved-receipt comparison for bounded
  non-taxable, single-currency service invoices. These helpers have no transport/CLI
  dispatch capability. General service posting and the native read-only allowlist remain
  unchanged. Exact records, write helper, authorization and evidence remain private.
- Validation: 489 tests pass, including 33 request/receipt cases; native helper compilation
  and five altered-request rejection checks passed. The core simulation job was not
  promoted to a production-success state; actual test-write evidence is recorded separately.
- NEXT: integrate durable transaction receipts and reconciliation with the shared job
  lifecycle before any general posting mode. No further sample writes are authorized by
  this one-invoice approval. Taxable/inventory/production posting remain unqualified.

## Latest qualification: non-taxable commercial invoice and review preview

- Operator chose non-taxable qualification with the sample company's sales-tax setting
  unchanged. The approved setup created only an isolated synthetic service and customer.
  Exact company checks, collision queries, returned IDs and independent readback were
  retained privately. No tax vendor/item, existing master, stock or invoice was changed.
- The initial exact-name collision query returned 500/Warn for absence; the private
  helper stopped before any creation. After inspecting the saved response, its exact
  name absence check was corrected and the bounded setup completed. Product exact-ID
  queries retain strict success requirements; no lookup gate was weakened.
- Real direct SDK and QBWC commercial checks both passed for quantity 2 at price 5.00,
  tax 0.00 and total 10.00. Local preparation/validation, duplicate prevention and changing
  the evidence transport while retaining the same job passed with a valid audit chain.
- Added authenticated owner-only `preview` for validated commercial invoice jobs. It
  rechecks current permissions, policy, company binding and fresh evidence, returns a
  deterministic review digest and evidence expiry, and records an audit event without
  dispatching a request, approving an invoice or changing its state.
- Validation: 456 tests passed, including native allowlist and both transport paths;
  lint and formatting passed. The actual non-taxable review was generated from the
  verified QBWC evidence. Source and wheel builds use the project's `uv build` workflow.
- Taxable and positive real inventory qualification remain deferred, not prerequisites
  for non-taxable Bridge development. Any real invoice posting needs separately approved
  sample-transaction qualification; posting remains disabled and no release/merge occurs.

## Latest implementation: bounded inventory, tax and pricing compatibility

- Added optional commercial policy and explicit quantity/unit-price/tax fields. Both
  transports check list prices, configured active tax code/rate, customer/item tax references,
  line arithmetic and tax-inclusive total limits; context binding covers the new policy.
- Inventory checks include exact Income/COGS/asset accounts, explicit simple inventory
  preferences and aggregate requested quantity versus on-hand less sales-order commitments.
  Price levels, UOM, inclusive tax, groups and site/bin/serial/lot inventory remain excluded.
- Extended fixed native read allowlist and private bounded commercial master preview.
  Empty 1/Info preview lists are valid absence, never exact-lookup success. SDK request and
  response schemas were checked against official Intuit OSR. All accounting writes disabled.
- Validation: 443 tests pass; lint, format and builds pass. Both transports have synthetic
  taxable/non-taxable service/inventory preparation coverage; real commercial preview passed.
- The original positive real commercial test needed compatible sample masters. The operator
  subsequently authorized isolated setup and chose non-taxable qualification; see the latest
  result above. Sales tax activation is not required for this path. Taxable/inventory real
  qualification remains separate. No production posting, merge or release.

## Latest implementation: fresh master evidence required for mapped invoices

- Invoice preparation resolves an owned SDK/QBWC evidence reference from private durable
  company state. Exact response, current company identity, payload/mappings, read+validate
  authority and valid audit are required. Client success/timestamp claims are rejected.
- Companies with master mappings require evidence; unmapped synthetic simulation remains
  available. Freshness defaults to 900 seconds and uses the original SDK dispatch or QBWC
  session start. Replay cannot renew it. All lifecycle gates and simulator dispatch recheck.
- Evidence links and history are append-only. Owner refresh before dispatch retains the
  invoice/deduplication IDs, returns it to draft and clears prior approval. Already dispatched
  jobs cannot be refreshed. Removing mappings cannot bypass a linked invoice's checks.
- Real sample SDK read, draft preparation, validation, fresh-instance duplicate prevention,
  controlled-clock stale rejection and audit verification passed. Sample prepare permission
  added; no approve/submit/posting permission. Both transport paths tested synthetically.
- Validation: 408 full-suite tests passed (54 evidence-link integration cases), including
  the native allowlist test; lint, formatting, wheel and source builds passed. One inherited
  Starlette/httpx deprecation warning remains. Raw company evidence stays private.
- Next: inventory, tax and pricing compatibility rules. Production posting remains disabled.

## Latest implementation: QBWC invoice compatibility parity qualified

- Added authenticated one-update invoice check CLI and durable per-company queue.
  Job owner, connector, payload, policy hash and assigned session are immutable.
  Pending account/invoice reads are mutually exclusive per connector, including DB
  constraints. Active sessions are unaffected and consumed jobs are never reassigned.
- Before dispatch/replay and response acceptance, invoice callbacks recheck read and
  validate permissions plus policy hash. Requires matching HCP and US qbXML 17.0.
  Exact request/response evidence uses the existing durable callback lifecycle.
- Shared customer/service-item/AR/income/currency checks now work through both SDK and
  QBWC. Result reads reverify company binding and current configured policy. No writes.
- Real sample QBWC check passed: closed, progress 100, compatibility matched, no error,
  valid audit and fresh-process CLI retrieval. Currency basis configured-single-currency.
  Explicit CurrencyRef mode remains synthetic-only. Exact evidence stays private.
- Local suite: 354 passed; lint, formatting and build passed. New tests cover restart,
  repeated callbacks, changed contexts, both currency modes, queue conflicts, immutable
  job context, disconnect and invalid HCP/version/master/company responses.
- Next: connect verified master evidence to invoice preparation with explicit stale
  evidence handling. Inventory/tax/pricing and production posting remain unqualified.

## Latest implementation: invoice currency/customer/service-item compatibility

- Added direct SDK --invoice-check using private exact customer/item/income mappings,
  existing invoice payload policy and read+validate permission. Supports up to 20
  distinct service items, active exact masters and receivables/income account checks.
- Currency modes are explicit: configured-single-currency requires multicurrency off;
  verified-home-currency requires an exact currency code and matching HomeCurrencyRef,
  customer and AR references. Missing/foreign/ambiguous evidence blocks the check.
- Durable context hash binds payload and policy; saved replay revalidates with no
  dispatch. Native allowlist covers only fixed projected read queries. Posting disabled.
- Added bounded private master preview to obtain exact sample mappings. CurrencyQuery
  3250 is tolerated for preview only when preferences explicitly report multicurrency
  off; currency records remain absent and no verified home currency is inferred.
- Actual sample preview and new exact compatibility check passed. Customer/service item,
  AR/income references, company binding and audit verified. Currency basis is explicitly
  configured-single-currency; the base code is operator policy. Fresh-process replay
  passed without querying. All raw evidence and sample mappings remain private.
- Validation: 337 full-suite tests passed plus one native C# allowlist test; lint,
  formatting and build passed. Explicit CurrencyRef mode is synthetic-only.
- Scope: direct SDK service items only. QBWC invoice compatibility, inventory/tax/price
  rules and invoice preparation linkage remain future work. No full invoice approval.
  See docs/INVOICE_COMPATIBILITY.md. Next: QBWC parity for these bounded master checks.

## Latest qualification: staged account-role blocker cleared

- On explicit operator request, added only validate to the staged operator's sample
  company grants and configured the invoice receivables role using the sole active
  AccountsReceivable record in the previously verified preview. Private config was
  backed up and validated before replacement; mode remains simulation.
- New real exact lookups for that configured account passed via direct SDK and QBWC.
  Both role checks returned role-matched with saved-evidence-only scope, reverified
  company binding and valid audit. QBWC completed and closed; direct SDK verified.
- A fresh-process direct SDK CLI role check also passed. Records, mapping, credentials
  and evidence remain private. No posting/submit/approve permissions were added.
- Staged role testing is now qualified for this sample and the permission blocker is
  cleared. Prior full suite: 306 passed; application code unchanged in this update.
- Next: currency, customer and item compatibility checks. Accounting posting disabled.

## Latest implementation: configurable invoice receivables role checks

- Added optional private company account_roles mapping and authenticated account-role
  CLI. Initial supported rule: invoice.create / receivable / AccountsReceivable.
- Requires current read+validate permissions, owned verified exact lookup evidence,
  configured ListID, active status, correct type and reverified company binding.
  Both QBWC and direct SDK evidence are supported. Previews cannot qualify a role.
- Successful checks record policy/response digests in company audit and explicitly
  report saved-evidence-only scope. No invoice state transitions or posting enabled.
- Local suite: 306 passed; lint, format and build passed. Synthetic tests cover both
  transports and changed policy, permission, binding, owner, type and unsupported rules.
  Actual staged role validation subsequently passed after the explicitly authorized
  sample-company validate grant; see qualification above and docs/ACCOUNT_ROLES.md.
- Next: define and validate transaction-specific currency/customer/item dependencies
  before connecting these preflight checks to invoice preparation. Posting disabled.

## Latest implementation: direct SDK exact account lookup qualified

- Added mutually exclusive `--list-id` / `--accounts` CLI modes. Exact ListID is bound
  to the immutable durable request; run IDs cannot change selector or operation.
- Shared validator rejects wrong/missing/inactive records. Native C# XML allowlist
  accepts only the fixed exact selector and projected fields, retaining SDK read-only
  and no-personal-data authorization and company/session overlap controls.
- Actual sample lookup returned exactly one matching account with verified company
  binding and audit. Initial native BeginSession failed on a modal before dispatch;
  restoring QuickBooks and explicit read recovery succeeded. Fresh-process replay
  returned the saved result without dispatch. Exact evidence remains private.
- Local suite: 282 passed; lint, format and build passed. Nine new synthetic cases
  cover saved-response recovery, immutable selector/mode, invalid inputs and rejected
  wrong/missing/inactive/wrong-company responses. No production posting is qualified.
- Next: operation-specific account validation and explicit configurable account roles.
  Accounting posting stays disabled.

## Latest qualification: actual QBWC exact-ID lookup passed

- The queued exact-ID job completed against the previously confirmed sample company.
  One active account matched the immutable requested ListID; response_result=100,
  no last error, closed session, company binding verified and audit chain valid.
- Initial connection was held by a QuickBooks icon-bar modal after screen resolution
  changed. The prompt was dismissed and the operator acknowledged Web Connector's
  retry dialog. No account request had been dispatched before that retry.
- Result retrieval in a fresh Python process validated persisted response evidence.
  Raw records, selectors and exact callback evidence remain private outside Git.
- This qualifies successful real QBWC exact-ID lookup, not production accounting or
  interrupted-query recovery. Previous full local suite: 273 passed; this update
  changes documentation only. Direct SDK exact lookup and operation-specific account
  rules remain next. Accounting posting stays disabled.

## Latest development: exact account selector through QBWC

- Added optional immutable ListID selection to the authenticated account job CLI.
  Existing preview jobs migrate with a null selector and retain preview behavior.
- Fixed requests select ListID instead of preview filters. Results require exactly
  the requested active record, with existing company/permission/correlation checks.
- Local suite: 273 passed; lint and format passed. New synthetic tests cover restart,
  selector changes, invalid IDs, missing/wrong/inactive records and successful lookup.
- Real exact-ID qualification subsequently passed; see the latest qualification above.
  Direct SDK exact lookup and operation-specific account rules remain next.
  Accounting posting stays disabled.

## Latest fix: QBWC account lookup implemented and real-tested

- Added an authenticated CLI to queue/read one bounded account preview for a connector's
  next new QBWC session. Per-company queue ownership and assigned ticket are durable
  and immutable; repeated enqueue is idempotent. No arbitrary qbXML injection exists.
- Callbacks recheck originating actor read permission, require matching HCP company
  evidence and US qbXML 4+, persist the negotiated-version request, and share account
  and company validation with direct SDK. Results are projected only after validation.
  Consumed jobs are not silently reassigned after expiry or disconnect.
- Actual saved Web Connector registration ran the queued lookup on the confirmed sample:
  20 account records, response_result=100, no error, clean closure and valid audit.
  Exact records and evidence are private. No new operator setup was needed.
- Local suite: **264 passed**; lint passed. Four new synthetic tests cover callback
  restart/repeats/one-shot behavior, missing HCP, permission revocation and wrong company.
  Posting remains disabled. The previously noted QBWC lookup implementation gap is fixed.
- Next: exact-ID lookup and operation-specific account validation. Hermes and production
  accounting remain unqualified; this preview is not a full chart or posting permission.

## Latest implementation: bounded active-account preview

- Added `--accounts` to the authenticated direct SDK CLI. Fixed AccountQuery reads
  at most 20 active records and projects ListID, FullName, AccountType and IsActive.
  Intuit's official request schema was inspected; no iterator support is assumed.
- Reuses the company-scoped durable SDK lifecycle, read/recover permissions, immutable
  request/response evidence, audit and binding verification. Native XML allowlist
  permits only the fixed extension. Operation changes under the same run ID, wrong
  correlation, malformed/duplicate/inactive records and excessive results are blocked.
- Real sample-company preview returned 20 records, matched the confirmed binding and
  passed new-process replay without dispatch and audit verification. Exact records
  and XML remain private. Results explicitly indicate that the preview is incomplete.
- Local suite: 260 passed; lint and format passed. No accounting writes enabled.
  Account preview via QBWC and Hermes is not implemented or real-tested. Discovery
  via QBWC retains its previously verified behavior. See docs/ACCOUNT_LOOKUP.md.
- Next: exact-ID account lookup and capability-specific validation; do not use preview
  records as posting authorization or assume a complete chart of accounts.

## Verified QBWC post-restart update

- Operator ran a second manual update after the staged bridge process restarted.
  A new ticket and correlation were durably recorded after that restart, with the
  complete actual authenticate/sendRequestXML/receiveResponseXML/closeConnection cycle.
- Both real sessions closed with response_result=100 and no last_error. The latest
  CompanyRet matched the persisted, previously operator-confirmed company binding;
  the earlier response also still verified. Audit chain and read-only health passed.
- Raw responses and exact timestamps remain private. This qualifies normal actual
  QBWC discovery across service restart in the operator-approved broader QuickBooks
  permission mode; Bridge accounting writes remain disabled. It does not qualify
  interrupted real QBWC callbacks, production posting, or Hermes integration.
- No further operator update is required for this milestone. Next: inventory and
  implement a narrowly supported read-only lookup through shared company controls.

## Verified actual QBWC discovery: registration blocker resolved

- Operator completed the fresh stable registration and manual update after explicitly
  approving broader QuickBooks application permission for QBWC metadata. The bridge
  continues to enforce query-only discovery; personal data and unattended access were
  not granted. Strict QuickBooks-enforced read-only QBWC registration is not claimed.
- Actual persisted callback sequence: authenticate, sendRequestXML, receiveResponseXML,
  closeConnection. Session closed with response_result=100 and no last_error; HCP,
  exact request and response were durably saved. CompanyRet matched the previously
  operator-confirmed binding and stored identity hash. Audit integrity passed.
- The captured real response was rejected with a deliberately wrong expected digest
  offline. This was not an actual mismatched-company connection. All company claims,
  raw XML, credentials and exact deployment evidence remain private outside Git.
- Cleared the credential clipboard. Restarted the staged bridge after verifying this
  cycle; one further manual update is needed for actual QBWC post-restart qualification.
  The AppLock registration blocker is resolved for the approved permission mode.
  Earlier blocker entries below are chronological history, not current status.

## Current QBWC repair decision

- Operator subsequently approved broader QuickBooks permission. The approved profile
  was imported and its QuickBooks grant confirmed: only while the sample is open,
  with personal data excluded. Bridge posting is still disabled.
- Existing R3 identity then failed with `Unique OwnerID/FileID pair value required`;
  Web Connector showed no registered applications. Retained existing stamps and
  created one fresh stable registration profile, preserving endpoint, credentials
  and confirmed Bridge company binding. Removed replacement-only AppUniqueName
  from that fresh profile after the old replacement path logged a null appName.
- Before that fresh import, native input failed with `GetCursorPos failed: Access
  is denied (0x80070005)`. Reconnect/unlock the interactive Windows session to proceed.
  The new profile and exact evidence are private. No real successful callback cycle
  is claimed. Next: import the prepared fresh profile, then verify manual discovery.

- Re-read actual QWCLog: AppLock registration repeatedly failed with SDK 3263 under
  IsReadOnly=true; the same metadata operation succeeded under IsReadOnly=false.
  Returning to read-only authorization caused the failure again. This is a permission
  conflict, not evidence of missing SDK runtime or a TLS certificate problem.
- Prepared a private, unimported `.qwc.pending` proposal retaining the existing R3
  connector identity and endpoint, requesting no schedule, no personal data and no
  required unattended access. It requests broader QuickBooks application permission;
  that grant cannot be described as metadata-only or QuickBooks-enforced read-only.
- Applying it requires explicit operator agreement to change the original read-only
  permission constraint. Bridge accounting posting remains disabled; the shared
  discovery verifier and existing confirmed binding remain unchanged. No permission,
  certificate or registration was changed. The default strict QWC generator is intact.
- Deployment/discovery synthetic regressions: 23 passed. Actual callback qualification
  remains pending. Next: obtain this specific permission decision, then apply and
  verify the proposed repair on the confirmed sample only. No repeated bootstrap cycle.

## Latest qualification: parent interruption with surviving native helper

- Real sample-company test passed: a private helper copy paused after saving the
  read-only response while its native session and mutex remained open. The harness
  terminated only its Python parent; the helper survived and later closed normally.
- A fresh service refused automatic replay. Explicit read recovery launched a second
  helper, which the native mutex rejected before SDK dispatch. Private native evidence
  showed exactly one actual SDK dispatch. Audit dispatch-intent events also include
  the rejected recovery attempt and must not be counted as actual SDK requests.
- After releasing the checkpoint, the survivor published its response. Shared binding
  verification and audit integrity passed, with no additional query. A separate CLI
  process then verified the completed run from durable evidence.
- QBWC authenticate returned busy against the held company journal when invoked
  locally. This is a service-level overlap check, not a real Web Connector callback.
- Focused synthetic regressions: **26 passed**. Application source is unchanged;
  private harness, instrumented helper hashes, PIDs, XML and results remain outside Git.
- Limits: OS power loss and interruption inside ProcessRequest remain unqualified.
  QBWC AppLock registration remains blocked. Live posting remains disabled.
- Next: inventory and implement the next narrow read-only lookup adapter through
  the same company validation and durable recovery controls. No user action is needed.

## Latest qualification: native helper interruption/recovery

- Actual sample-company fault injection passed at two native-helper checkpoints.
  Private copies of the packaged helper added only a checkpoint marker and pause;
  the qualification harness terminated the exact child PID. QuickBooks itself was
  not terminated. Source/instrumented hashes, PIDs, XML and results remain private.
- After a real response was saved but before session closure/publication, termination
  left the durable run dispatched with no published response. A fresh service refused
  automatic replay. Explicit authorized read recovery issued one new fixed read,
  verified the operator-confirmed binding and completed successfully.
- After closure and atomic response publication, termination likewise left the
  parent journal dispatched. Recovery consumed the saved real response with no SDK
  call. Both original captured payloads passed the shared company-binding verifier.
- A subsequent independent Python process verified both completed runs without
  dispatch. Audit chains passed; dispatch counts were exactly two for explicit
  recovery and one for saved-response recovery. No accounting writes were enabled.
- Focused synthetic regression: **26 passed** (SDK and QBWC discovery). Previous
  full-suite evidence remains 251 passed; application source was unchanged here.
- Limits: controlled native process termination is now real-tested at these two
  boundaries. Power loss, termination inside ProcessRequest, parent-only death with
  a surviving helper, and production-company operation remain unqualified. QBWC's
  AppLock registration blocker remains unchanged. No QWC import/removal is required.
- Next: qualify parent-only interruption/overlap, then extend narrowly supported
  read-only adapters. No missing user access was needed for this qualification.

## Latest milestone: durable direct SDK discovery

- Integrated fixed read-only HostQuery/CompanyQuery with shared private configuration,
  actor/connector authentication, company read/recover permissions, SQLite durability,
  immutable evidence, binding validation and chained audit. Added an operational CLI
  and [direct SDK runbook](docs/DIRECT_SDK.md). No transaction dispatch was enabled.
- Requests persist before dispatch; responses persist before verification. Repeated
  run IDs recover saved responses without SDK calls; missing responses require explicit
  authorized read recovery. Per-company active sessions exclude QBWC and direct SDK
  overlap. A native mutex serializes helpers across parent-process exits.
- Real synthetic-company integration: two successful integrated SDK discovery runs,
  including one after staged HTTPS service restart. Each matched the previously
  operator-confirmed private binding. New-process replay used persisted evidence
  without dispatch; the audit chain verified. HTTPS health reports posting disabled.
  Exact responses, permissions, closure evidence and qualification summary are private.
- Local verification: **251 tests passed**, including **8 new synthetic direct SDK
  tests** for response recovery, duplicate execution, held missing responses, binding
  mismatch, overlap, permissions, immutable evidence and audit integrity. Ruff lint
  and format passed; wheel/source build passed. One inherited deprecation warning remains.
- Real SDK process-crash/power-loss recovery and actual mismatched-company sessions
  are not qualified. Those failure paths are synthetic tests, not real QBWC results.
  QBWC registration/callback qualification remains blocked by AppLock metadata behavior.
- Next: qualify native helper interruption/recovery on the sample company, then extend
  explicitly supported read-only adapters. Hermes and production transaction support
  remain unqualified. Earlier chronological entries below describe prior milestones.

## Scope and branch

- Started from `main` at `4f86e44f890c88ee89f53daf0ec2b8c0f59730ff`.
- Inspected open draft PR #1; merged its exact head
  `3ce990cd3091dc5c52adfca793025ace30cfadfc` into `codex/foundation`.
  PR #1 is not assumed merged into main.
- No repository or parent AGENTS.md found. Upstream MIT attribution retained.
- Public repository; examples and tests must contain synthetic data only.
- Review PR: [#2 â€” company-scoped foundation](https://github.com/kaydtechsolutions/KaydBooks-Bridge/pull/2).
  Native Git push stalled; uploaded through the connected GitHub API. The uploaded
  milestone trees exactly matched the tested local trees. Original local commit
  history is preserved on `codex/foundation-local`; `codex/foundation` tracks the PR.

## Completed / evidence

- Verified Git 2.54.0, uv 0.12.7, isolated `.venv` with Python 3.12.14.
  Shell Python alias, gh and dotnet were unavailable; none blocks foundation work.
- Baseline: `uv run --frozen pytest -q`: 167 passed, 4 failed (171 total).
  All failures concern the exact TTL deadline on Windows. Baseline Ruff passed.
- Fixed inclusive expiry boundary; added deterministic clock regression.
- Reviewed inherited release workflow: v-tags/manual runs could publish the upstream
  qbwc-kit package using PYPI_TOKEN and create releases without test gates.
  Replaced with manual build validation and artifact upload; removed publishing,
  secret references and write permissions. This protection takes effect on main
  only after review/merge. Do not create release tags on the inherited main branch.
- CI now includes Windows and Linux. No release or deployment performed.

## Foundation milestone implemented and synthetic-tested

- `kaydbooks_bridge` application package and authenticated CLI; private config/state
  rejected inside Git checkouts. Company A/B templates contain synthetic values only.
- Separate company databases and durable company/schema binding; strict Decimal
  amounts, dates, currency, masters, source allowlists and uncertainty checks.
- Immutable prepared jobs, fingerprint-bound separate approval, current policy and
  permission checks, persistent pause, per-company serialized dispatch.
- Canonical idempotency including aliases, source/reference duplicate checks,
  append-only chained audit, receipt persistence and independent synthetic read-back.
- Durable synthetic ledger, unknown/posted-unverified holds, explicit expired-attempt
  recovery and read-only reconciliation. No retry override or live transport exists.
- Shared service-boundary tests cover CLI/chat/document/tool/schedule/delegation/
  Kanban/browser/desktop labels. These are not real interface integration tests.
- Latest local suite: **235 passed**, including 63 Bridge synthetic tests and 172
  inherited/regression transport tests. Ruff lint and format checks pass.
- Actual subprocess termination after a synthetic external commit was tested:
  restart preserved in-flight state, recovery marked unknown, reconciliation found
  one saved record, and no second write occurred.
- PowerShell CLI walkthrough completed prepare â†’ validate â†’ approve â†’ submit â†’
  simulate â†’ verified, with valid audit. Runtime state/secrets remained outside Git.
- Distribution renamed to `kaydbooks-bridge` (`0.1.0.dev1`), preserving the upstream
  `qbwc_kit` namespace and license. Wheel/source build passed; no upload/release.
- `twine check` passed for wheel and source archive. Clean isolated wheel install
  with no dependencies imported both namespaces and confirmed the disabled live gate.
  Source archive includes operational docs and synthetic examples.
- CI uses the frozen uv lock on Windows/Linux across Python 3.10â€“3.13 and builds
  artifacts. [PR checks](https://github.com/kaydtechsolutions/KaydBooks-Bridge/pull/2/checks)
  are the live source for remote results; local passes do not establish remote success.
- Initial remote CI passed all four Linux versions and lint/build, but exposed a
  Windows path-canonicalization race during concurrent first-company initialization.
  Directory creation now precedes canonical containment comparison; symlink escape
  protection has an additional regression test. Follow-up [CI run 33980287486](https://github.com/kaydtechsolutions/KaydBooks-Bridge/actions/runs/33980287486)
  passed all eight Windows/Linux Python 3.10â€“3.13 combinations plus lint/build at
  commit `781c168c9a6e370e73f42689f6eeb7d9a1f99e41`. This subsequent status update
  changes documentation only.
- Architecture, M0â€“M7 acceptance plan, capability evidence rules, onboarding,
  permissions, troubleshooting, pause/recovery, backup/upgrade and deployment gates
  are documented in `docs/`. One upstream Starlette/httpx deprecation warning remains.
- Durable queue integrity is now enforced in SQLite after restart: immutable prepared
  identity/payload/source, append-only jobs and idempotency aliases, one-time approvals
  and receipts, coupled dispatch attempt fields, legal state transitions, immutable
  company/schema metadata, and boolean durable pause control. Tests prove malformed
  inserts, requeue of unknown writes, mutation/deletion and receipt/approval rewrites
  are rejected. Dispatch attempt IDs are retained through reconciliation as evidence.
- Queue-hardening CI run `33989477054` passed lint/build and all eight Windows/Linux
  Python 3.10â€“3.13 jobs. No real Hermes or QuickBooks behavior was exercised.
- Durable read-only QBWC discovery now maps each authenticated connector identity to
  one configured company and rejects missing, ambiguous or inconsistent CompanyRet
  fingerprints. At least three official CompanyRet claims are required, including a
  claim stronger than display/fiscal names; callback file paths are hashed evidence,
  never identity. Connector passwords and optional file paths remain environment-backed.
- Reviewed the official Intuit QBWC callback guide, SDK programmer guide and CompanyQuery
  OSR schema. The adapter persists HCP preflight, callback context, exact correlated
  HostQuery/CompanyQuery request, exact response and callback outcomes before advancing.
  It checks response count/status/correlation, host country, negotiated supported qbXML
  version and configured company digest. It emits no write request and has no task hook.
- Seventeen focused synthetic tests cover inherited fake-connector flow, restart recovery,
  exact duplicate callbacks,
  conflicting responses, expired tickets, overlapping connectors, disconnect release,
  cross-company/cross-session replay, path/name insufficiency, missing/ambiguous/mismatched
  bindings, documented country/version minimums and immutable SQLite evidence. Full local
  suite, Ruff lint/format, wheel/source build and Twine metadata checks pass. The artifacts
  contain the new module and discovery documentation. No QuickBooks process was involved.
- [QBWC discovery CI run 33990832056](https://github.com/kaydtechsolutions/KaydBooks-Bridge/actions/runs/33990832056)
  passed lint/build and all eight Windows/Linux Python 3.10â€“3.13 jobs at feature commit
  `da4bab2d179cf1d5f6a2efd21304979996d3c226`.
- M2 qualification staging now has an HTTPS-only Bridge entry point, bounded callback
  bodies, health/support endpoints, environment-backed private credential loading and
  stable QWC generation that refuses silent OwnerID/FileID changes. An all-zero private
  identity sentinel captures HCP evidence durably but returns no Bridge request until an
  operator confirms the synthetic company. Candidate export writes claims/evidence to a
  new private file and cannot edit the configured binding.
- Current local verification: **242 passed**; Ruff lint and format checks pass; wheel
  and source builds pass Twine metadata checks. The seven added tests are synthetic or
  local HTTP/TLS shape tests and do not count as QuickBooks integration evidence.
- [M2 staging CI run 33991930089](https://github.com/kaydtechsolutions/KaydBooks-Bridge/actions/runs/33991930089)
  passed lint/build and all eight Windows/Linux Python 3.10â€“3.13 jobs at commit
  `fc1d9814060af8aa6744d76f56dd8eda84000db3`. GitHub reported PR #2 mergeable; it
  remains open and was not merged.
- Documentation follow-up [CI run 33992022387](https://github.com/kaydtechsolutions/KaydBooks-Bridge/actions/runs/33992022387)
  also passed all nine checks at `194c584dedfa784007a797628922f5a99b0618db`.
- On the available Windows host, QuickBooks Enterprise 2024 R21 and Web Connector 34
  were found running in the current session. The current company-window title did not
  identify a sample/test company, so no connector was imported. A private localhost
  stage was provisioned outside Git with restricted ACLs, generated credentials, stable
  QWC IDs and a 30-day leaf certificate trusted for the current user. Windows HTTPS
  health/WSDL and manual authenticate/close callback probes passed. These probes did not
  involve QuickBooks and are not real integration evidence. Redacted version, hash,
  trust and readiness evidence is retained privately outside Git.
- The active staged certificate is explicitly `CA:FALSE`. Its predecessor was generated
  CA-capable and remains in the current-user trusted store because automatic approval
  review rejected deletion; deletion was not retried. A live Windows TLS handshake and
  chain check proved the endpoint presents the active certificate, not its predecessor.
  The superseded entry does not prevent qualification and is not an M2 prerequisite.
  Exact private fingerprint evidence remains available for a separate hygiene task.
- Exact-path private operator instructions plus local password-copy, clipboard-clear and
  candidate-export helpers are staged outside Git. The helpers contain no credentials.
- The first actual QWC import reached QuickBooks but failed before any service callback:
  Web Connector 34 logged QBWC1039 and QuickBooks reported that the application had not
  previously been authorized by the company Admin. No `CompanyRet` was received. The
  deployment profile now requires Intuit's `IsReadOnly=true` authorization preference
  and permits only optional unattended access. This QuickBooks request-processor control
  is independent of the Bridge's enforced query-only mode. Focused tests are synthetic;
  the failed authorization attempt is real integration evidence, not a successful test.
- A second real import against an operator-confirmed Intuit sample company proved that
  QuickBooks honored `IsReadOnly=true`: its permission summary allowed reading without
  personal data and only while QuickBooks was running. QBWC then failed before callbacks
  with SDK status 3263 because its own first-time registration attempted to add the
  required FileID data-extension definition under read-only authorization. A private,
  same-ID registration bootstrap is staged for this QBWC limitation. It must never run
  an update; the stable read-only QWC must replace it before any callback.
- A removal experiment established that QBWC's Remove action deletes the FileID value
  from the company, so it cannot mediate the transition back to read-only. No callback
  occurred. The corrected private v3 QWC uses the same IDs plus Intuit's documented
  `AppUniqueName` replacement path; the generator and profile now support and require
  this stable name.

## Blockers and next actions

- Corrected initial PATH observation: a local Hermes executable was found.
  Read-only version/help, CLI tool inventory and tool registration source inspection
  completed. Actual installed version/tool enablement evidence is stored privately
  outside Git. Bridge-specific permissions, profile, schemas and integration behavior
  remain unverified. No Hermes settings, schedules, recipients or boards were changed.
- R3 real qualification failed: matching AppUniqueName on bootstrap and final QWC
  did not prevent AppLock DataExtDefAdd during read-only import. SDK status 3263
  rejected the metadata write. The previously documented replacement workaround is
  withdrawn; do not repeat imports, rotate IDs, or remove registrations.
- Immediate work: establish supported QBWC metadata permission requirements or assess
  direct SDK read-only discovery separately. Keep Auto-Run off, passwords blank,
  bindings unconfirmed and posting disabled. No actual Bridge CompanyRet received.
- Real Hermes and QuickBooks integration tests: **none**. Production-enabled features:
  **none**. Real transaction/report/tax/inventory/landed-cost support is unverified.
- Planned: real qualification of the synthetic-tested QBWC discovery adapter;
  per-operation master/account/tax validation; native Hermes tools and document intake;
  schedules, notifications, memory, delegation, Kanban projections, reports, optional GUI flows.
- Draft revisions/cancellation, dependencies, operator correction of blocked jobs,
  policy-change audit, OS ACL provisioning and signed external audit checkpoints
  are not implemented. Held outcomes cannot be bypassed through the CLI.
- Next: after operator confirmation, capture and privately review CompanyRet identity,
  configure its digest, restart the staged service, run the read-only update twice to
  exercise recovery/duplicate behavior, and retain actual callback evidence outside Git.

## Direct SDK diagnostic follow-up

- Installed QBXMLRP2 COM runtime and Intuit interop assembly were discovered. SDK
  development-kit installation is not required for this diagnostic. Typed COM
  activation and PutIsReadOnly(true) passed on Windows PowerShell/.NET Framework,
  without opening a company session. PowerShell Core's legacy interop invocation
  was unsuitable; the successful Framework probe is the prerequisite evidence.
- A private diagnostic compiled against the actual installed interface. It requests
  read-only/no-personal-data authorization, checks the granted preferences using the
  session ticket, and can issue only fixed HostQuery/CompanyQuery requests. It saves
  dispatch intent and exact response outside Git and does not change company bindings.
- Direct SDK diagnostic launched; authorization/response outcome remains pending.
  This is not a QBWC callback test, a supported Bridge transport, or an M2 pass.
  There is no new operator QWC import requirement. All bootstrap/replacement retry
  instructions remain withdrawn. Live posting remains disabled.

## Verified direct SDK read-only discovery

- Real sample-company diagnostic: QuickBooks granted read-only access and excluded
  personal data; granted preferences were checked before any query. The initial
  qbXML 1.0 request failed with COM 0x80040400 (XML parse error). No CompanyRet was
  returned by that attempt. The exact cause within the 1.0 format remains unverified.
- A fresh read-only session confirmed request-processor support for qbXML 17.0.
  The fixed HostQuery/CompanyQuery batch using 17.0 succeeded: one successful HostRet
  and one successful CompanyRet; session closure completed. Exact XML, permissions,
  dispatch intent, supported versions, and closure evidence are retained privately.
- Private operator review contains only the three configured identity claims. All
  are present; candidate digest is calculated but configuration remains unconfirmed.
  Operator confirmation is required before binding. No identity details are in Git.
- This is actual direct SDK discovery evidence, not a mock and not QBWC qualification.
  QBWC callbacks, binding persistence and real restart qualification remain unfinished.
  Bridge posting remains disabled; no accounting write requests were sent.

## Operator-confirmed binding and restart verification

- Operator explicitly confirmed the three private company identity claims. The expected
  connector digest is now configured outside Git, with private confirmation evidence and
  company-scoped audit intent/completion events. Audit chain verification passed.
- Restarted the staged HTTPS Bridge process after the configuration update. Health
  reports read-only discovery and live_posting=false. A fresh actual direct SDK session
  returned HostRet/CompanyRet successfully, closed, and matched the persisted expected
  binding using the shared Bridge verifier loaded in a new process.
- Offline test with the captured real payload and a deliberately wrong expected digest
  was rejected by the shared verifier. No production company was opened. This is a
  real-payload offline test, not an actual mismatched-company QBWC callback test.
- Focused regression suite: 23 passed (synthetic discovery/deployment tests). No source
  changes in this milestone; changes are private configuration/evidence and documentation.
- Historical M2 blocker (superseded by subsequent successful QBWC qualification): real
  QBWC read-only registration/callback qualification. The
  direct SDK diagnostic is not a production transport. Do not repeat withdrawn QWC
  bootstrap/replacement workarounds or enable posting. Next implementation work should
  bring any chosen direct SDK transport through the same authenticated, company-scoped
  durable lifecycle before claiming full M2 integration qualification.
