# M3 supplier-bill implementation contract

Status: design and SDK schema review complete; runtime bill preparation/dispatch
is not implemented. This starts M3-03 in the [release checklist](FIRST_RELEASE_SCOPE.md).

## First implementation increment

Begin with a non-tax, base-currency expense bill in the authorized sample. The
release still requires item/inventory bills, taxes, currencies and adjustments;
this increment does not narrow the agreed release scope.

Use `bill.create` as the planned operation name. The payload will carry a supplier
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

This contract does not enable supplier-bill posting. Implementation and synthetic
tests precede the controlled native qualification.
