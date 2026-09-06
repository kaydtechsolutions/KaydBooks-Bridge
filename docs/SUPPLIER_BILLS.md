# M3 supplier-bill implementation contract

Status: base-currency expense-bill preparation, simulated lifecycle and controlled
native sample posting/read-back are implemented and qualified. M3-03 remains partial
in the [release checklist](FIRST_RELEASE_SCOPE.md): item/inventory bills, terms,
tax/currency variants and broader liability-effect testing remain unfinished.

## Implemented expense-bill path

`bill.create` accepts `vendor_id`, `txn_date`, `due_date`, `ref_number`, `currency`
and `lines` containing `expense_id`/`amount`. Private `bill_masters` maps vendor
aliases to ListIDs, `payable` to the AP ListID, and expense aliases to ListIDs.
Existing invoice policy and historical evidence retain their original identities.

The direct SDK module provides `--bill-preview`, `--expense-accounts`, `--bill-check`
and `--bill-receipt-check`. Preview is bounded and does not prove compatibility.
Exact evidence binds the payload, mappings, company, operator and observation time.
Native bills require active vendors, AccountsPayable plus Expense/OtherExpense
accounts and a verified single-currency company.

CLI `prepare`, `validate`, `approve` (when required), `preview` and `submit` share the
job lifecycle. `post-sample-bill JOB` and `reconcile-sample-bill JOB` use the separate
private `sample_bill_posting` gate: connector, authorization, ref_prefix, max_bills
and expires_at. Production posting is unavailable. A preview or submission does
not authorize dispatch. The MCP tools `prepare_bill_v1` and `lookup_bill_masters_v1`
provide retained-source preparation and read-only evidence without write tools.

Each native attempt has an immutable request and one-write fence. Supplier/reference
duplicates are checked in the same native session before authorization; another
supplier's reference does not collide. Saved bills are independently queried by
TxnID and compared for supplier, AP account, dates, expense lines and unpaid totals.
Bill receipts never enter the invoice register or invoice native adapter.

Actual sample qualification created one USD 10 expense bill and verified it through
a separate read-only session. A preceding attempt returned SDK 3210 because an
explicit NotBillable flag is invalid without a reimbursable customer in that sample.
The corrected request omits the flag and rejects reimbursable saved lines. The
rejected attempt was closed as failed only after a fresh company/vendor/reference
query confirmed absence; its immutable request and error remain retained. This
does not authorize retrying the failed job. Other uncertain outcomes remain held.

## First implementation increment

Begin with a non-tax, base-currency expense bill in the authorized sample. The
release still requires item/inventory bills, taxes, currencies and adjustments;
this increment does not narrow the agreed release scope.

Use `bill.create` as the operation name. The payload carries a supplier
alias, transaction date, due date, reference, base currency and expense lines with
explicit account aliases and amounts. Source provenance and line confidence use
the shared document contract. Aliases resolve only through private company policy
and fresh verified vendor/account evidence, never names extracted from a document.

The first increment will reject item groups, tax, foreign currency, payment/credit
application and purchase-order links until their separate adapters are implemented.
Supplying unsupported fields must produce an error rather than ignoring them.

## SDK schema findings

Intuit's [BillAdd request schema](https://static.developer.intuit.com/qbSDK-current/common/newosr/qbsdk/json/BillAddRq.json)
includes VendorRef, APAccountRef, TxnDate, DueDate, RefNumber, TermsRef and
ExpenseLineAdd; item lines are a separate branch. The Bridge will require exact
ListIDs and explicit reviewed accounts, even where the SDK allows omitted values.

The [BillQuery request schema](https://static.developer.intuit.com/qbSDK-current/common/newosr/qbsdk/json/BillQueryRq.json)
provides transaction-ID and reference selectors, with IncludeLineItems and
IncludeLinkedTxns. These will support duplicate detection and independent read-back.
Exact reference matches must be checked against the intended vendor; a reference
alone is not sufficient identity. Incomplete or ambiguous results cannot prove absence.

The [BillQuery response schema](https://static.developer.intuit.com/qbSDK-current/common/newosr/qbsdk/json/BillQueryRs.json)
includes TxnID, EditSequence, VendorRef, APAccountRef, dates, amount/currency fields,
payment state, linked transactions and expense/item line returns. Qualification
must establish amount and balance semantics using actual bills and payment state;
do not equate a field name with a current outstanding balance without verification.

These schema findings establish request vocabulary, not compatibility with every
edition or proof that a bill was posted. The initial native path must negotiate
and test the supported sample's qbXML version and independently compare saved fields.

## Required changes to the current implementation

The controls below are implemented for the expense-only increment; broadening them
to the remaining bill variants is still release work.

| Component | Change required before enabling bill.create |
| --- | --- |
| Private config | Vendor aliases, AP/expense account roles and bill-specific master policy. Existing invoice configuration continues to load unchanged. |
| Payload/source validation | Typed bill validation, positive decimal line amounts, date/term checks, company limits and retained source values. |
| Shared service | Explicit operation dispatch for validation, preview, evidence, approval, submission and recovery. Unsupported operations remain blocked. |
| Duplicate identity | Company + bill.create + resolved vendor identity + normalized reference; different suppliers may reuse a bill number. Preserve existing invoice keys. |
| Read evidence | Durable fixed Vendor/Account/company queries, freshness and context binding; no arbitrary query endpoint. |
| Store | Append-only bill evidence and receipts with typed transaction identity. Existing invoice rows, triggers, receipts and audit hashes must survive migration. |
| Native helper | Reviewed BillAdd allowlist, current company/vendor/account/duplicate preflight, durable exact request/attempt and one-write fence. |
| Reconciliation | Exact saved BillQuery by TxnID; vendor-scoped complete reference recovery when response is lost. Never retry an uncertain write. |
| Interfaces | CLI/MCP/forms may expose bill.create only after all shared controls exist. No label-only capability advertisement. |

Do not rename invoice tables or reinterpret historical invoice payloads as bills.
Receipt lookup and transaction uniqueness must retain their operation type, so
invoice and bill evidence cannot be attached to each other's jobs.

## Increment acceptance tests

1. Existing invoice jobs, receipts, recovery and audit remain valid after migration.
2. Same supplier/reference is deduplicated; another supplier using that reference
   does not collide. Two aliases resolving to the same vendor cannot bypass checks.
3. Wrong company, inactive vendor, unsupported AP/expense account, stale evidence,
   revoked permission and source uncertainty block dispatch.
4. Full permissions do not bypass required approval, company pause or sample limits.
5. Before a write, persist the exact request and durable attempt. Capture the response,
   then independently compare vendor, AP account, dates, reference, lines and totals.
6. Missing/ambiguous responses become held outcomes. New-process reconciliation
   resolves only from matching saved evidence, without resending the bill.
7. Qualify one controlled expense bill in the authorized sample and retain a private
   receipt/audit proof. Do not perform supplier payment or delete that business record.

The controlled expense-bill increment is qualified. The remaining release bill
variants still require their own implementation and native qualification.
