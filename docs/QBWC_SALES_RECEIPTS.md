# Sales receipts through Web Connector

`sales-receipt.create` records a non-tax cash or check sale using `SalesReceiptAdd`.
It does not create an invoice plus payment, contact a bank or process a card charge.
The browser supports customer, date/reference, deposit account, payment method,
optional check number, and service/inventory lines with quantity, rate and amount.
Each exact input is reviewed and approved before a bounded sample dispatch.

This first scope supports Cash and Check methods, single-currency companies and
simple inventory under the existing sales policy. Check methods require a check
number. Multi-currency, card-processing fields, tax and document adjustments are
rejected. Company/customer/item and deposit/method mappings stay private.

Master checks verify the customer, item pricing/accounts, company preferences,
deposit account (Bank or verified UndepositedFunds) and active payment method.
Inventory checks retain quantity and average cost. Preflight also queries the
exact reference to prevent a duplicate. A saved receipt must match its transaction
identity, customer, date, reference, lines, total, deposit and method. Readback
must independently prove the deposit balance increase, unchanged customer balance,
stock decrease and unchanged average cost.

The shared durable QBWC lifecycle retains one write attempt. A lost response uses
read-only reference/transaction lookup; absence or mismatch holds the outcome and
never authorizes resend. Evidence is company-, owner-, connector-, payload- and
policy-bound, expires, and is rechecked before handoff. This operation has its own
sample quota and no native SDK or legacy scheduled-dispatch fallback.

Private policy adds `sample_sales_receipt_posting` with `connector`, `authorization`,
`ref_prefix`, `max_receipts` (1–10 cumulative attempts) and `expires_at`. An absent
gate leaves historical policy hashes unchanged. Production posting is unavailable.
This implementation reuses the existing `invoice_masters` and `payment_masters`
maps and requires their selected customer identities to agree.

Automated qualification covers service/inventory sales, sold-out recovery after a
lost reply, wrong saved values/balances/stock/cost, expired or changed evidence,
revoked authority and unresolved outcomes. A real browser checks the sales-receipt
form. Installed sample qualification passed: one USD10 mixed receipt increased the deposit
balance 507 to 517, left customer balance 44 unchanged, and reduced stock 2 to 1
at unchanged average cost 5. Exact saved lines, one attempt/write, valid audit and
repeat-action refusal passed; posting was paused again. Broader variants remain.

The request and response shape was checked against Intuit's
[SalesReceiptAdd reference](https://developer.intuit.com/app/developer/qbdesktop/docs/api-reference/qbdesktop/salesreceiptadd)
and its official Onscreen Reference schema. See the
[active pilot scorecard](HERMES_DATA_ENTRY_PILOT.md) for release status.
