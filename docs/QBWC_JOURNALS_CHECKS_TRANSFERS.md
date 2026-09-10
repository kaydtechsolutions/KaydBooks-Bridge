# Journals, checks and inventory transfers

These paths use the same reviewed draft, separate approval, durable Web Connector
handoff, exact saved-record lookup and audit as the existing entry types. They
have no native SDK fallback. All three passed basic installed sample acceptance
with exact saved records and accounting/site-stock effects. This qualifies the
bounded scope below; a successful Web Connector update alone is insufficient.

## Supported scope

| Type | Payload and verified effect | Limits |
| --- | --- | --- |
| Journal (`journal.create`) | Date, reference, currency, optional default memo, debit/credit lines with mapped accounts, positive amounts and optional line memos. Debits equal credits; each account's balance changes by its exact expected amount. | 2–100 lines, at least two distinct accounts, single currency. Ordinary balance-sheet and income/expense accounts; no AR/AP party allocations, special accounts, tax, foreign currency or home-currency adjustments. |
| Check (`check.create`) | Mapped bank and vendor, date, explicit check number, currency, optional memo and expense lines. Bank decreases, expenses increase, vendor payable balance stays unchanged. | 1–99 expense lines. Ordinary Bank, Expense, OtherExpense and COGS accounts. No item checks, printing, bill settlement, billable customer expenses, tax, foreign currency or transmission to a bank. Supplier bill payments are a separate operation. |
| Inventory transfer (`inventory-transfer.create`) | Mapped source/destination sites and inventory items, date, reference, currency, optional memo and quantities. Source decreases, destination increases; company-wide quantity and average cost stay unchanged. | 1–20 distinct stock items; positive quantities with at most six decimals. Multi-location inventory enabled, single currency, average-cost items. No bins, assemblies, FIFO or serial/lot tracking. Sufficient source-site stock and company value limit required. |

References have at most 11 ASCII letters, digits or hyphens. A journal's default
memo is copied to lines without an explicit line memo: US QuickBooks does not
support `JournalEntryAdd/Memo`. The bridge never sends that unsupported header
field. Saved line memos must match; a warning that discarded requested data does
not count as verification.

Multi-location transfers and simple-inventory sales/purchases have different
prerequisites. The previously qualified inventory invoice, bill, sales-receipt
and credit variants require multi-location inventory to be off. Site-aware sales
and purchase lines are not included in this change. Service-only entries do not
need site fields. Do not advertise stock sales/purchases with sites as qualified.

## Private company configuration

Configure `journal_masters.accounts` with alias-to-ListID mappings. Checks reuse
`supplier_payment_masters.banks`, `bill_masters.vendors` and
`bill_masters.expenses`. Transfers use `inventory_transfer_masters.sites` and
`inventory_transfer_masters.items`, each with explicit alias-to-ListID mappings.

Read-only `inventory-sites.read` jobs discover active top-level site names and
ListIDs through Web Connector. They are not posting contracts, cannot produce
accounting drafts, require read/validate authority and retain company identity
verification. Discovery also returns inventory preferences. Bins are excluded
from setup choices; ambiguous item/site quantities fail closed.

Controlled sample gates are `sample_journal_posting` (`max_entries`),
`sample_check_posting` (`max_checks`) and `sample_inventory_transfer_posting`
(`max_transfers`). Each requires the confirmed connector, a reference prefix,
explicit authorization, expiry and a cumulative maximum of 1–10 attempts.
Existing company permissions, independent approval and pause controls still apply.
No production permission is implied by these sample gates.

## Recovery and qualification

An uncertain handoff must be reconciled by read-only lookup, never by resending.
The original approved payload, attempt, baseline, request/response hashes and
audit stay intact. Changed balances or saved fields leave the transaction held.
Recovery uses a separate query correlation for the find and verification phases.

The former unsupported-header-memo bug has a separate, one-shot repair path:
`journal_memo_repair.enqueue(bridge, token, company, job_id, approver_token=reviewer)`.
Ordinary `recover()` remains read-only. The repair requires paused posting, a
separate reviewer, the original approved payload and immutable warning-530 Add
response, and fresh confirmation that this is a QuickBooks sample company. It
checks the original transaction/line IDs, every saved accounting field and the
original balance effects before dispatch. Its ten-minute grant permits one
`JournalEntryModRq`, containing only the current TxnID/EditSequence and existing
line IDs with their approved memos. It sends no amounts, accounts, new lines or
second Add. QuickBooks optimistic concurrency protects against intervening edits.
Saved fields and balances are read back afterward. An uncertain Mod is held for
ordinary read-only recovery; the grant cannot be reused to resend it.

Focused tests cover balanced journals, invalid payloads, exact lines/payees/sites,
bank and expense effects, source shortages, changed totals/costs, revoked grants,
stale evidence, lost responses and duplicate refusal. Real browser tests exercise
the forms against the shared service contract. Isolated schema-upgrade rehearsals
preserve historical records and audit integrity before installed upgrades.

The original installed sample journal's warning-530 memo problem is now repaired
and verified: both approved memos match, original transaction/line IDs are retained,
and the original USD5 accounting effect is unchanged. There is one original Add
and one memo-only Mod, not a second journal. The USD5 expense check passed bank -5,
expense +5 and unchanged vendor payable balance. The one-unit inventory transfer
passed source -1, destination +1, unchanged total quantity and average cost. Both
new transactions retained one Add with exact readback, valid audit and duplicate
refusal. Posting is paused, with no unresolved sample write. See [the pilot
scorecard](HERMES_DATA_ENTRY_PILOT.md) for remaining Hermes workflow checks.
