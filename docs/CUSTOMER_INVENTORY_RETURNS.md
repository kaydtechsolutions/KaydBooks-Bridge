# Customer inventory returns and stock reconciliation

Customer credit notes accept non-tax service, simple inventory, or mixed lines tied
to an exact source invoice. Fresh checks verify customer, receivable account, item
identity, configured price, income/asset/COGS accounts and the original invoice's
quantity/amount limits after prior Bridge-linked credits. Creating a credit does
not automatically apply it to an invoice or issue a refund.

A return increases stock, so a sold-out item can be returned. Fresh native evidence
must still show nonnegative stock and a positive average cost. FIFO, sites, bins,
serial/lot tracking and other advanced inventory settings are outside this qualified
contract. Tax, currency variants, zero-cost returns and adjusted credit documents
remain excluded or unqualified. Current item price checks still apply; this does
not claim support for every historical-price return.

The existing reviewed credit lifecycle requires fresh validation, separate approval
and a bounded sample posting grant. Native requests use exact item references,
quantities and rates from the checked credit contract, following Intuit's
[CreditMemoAdd reference](https://developer.intuit.com/app/developer/qbdesktop/docs/api-reference/qbdesktop/creditmemoadd).
Before/after evidence must contain every inventory item, the exact returned quantity,
the corresponding stock increase, unchanged average cost and the full customer
balance decrease. Missing baselines, changed cost or unexpected stock leave the
attempt unverified. Lost responses require reconciliation, never blind resending.
Verified browser review displays the retained stock effects.

## Installed sample qualification

One separately approved USD15 mixed credit returned two units at USD5 and one USD5
service line from a verified original invoice. Its first attempt verified. Stock
increased from zero to two, average cost stayed USD5 and customer balance decreased
by USD15. An unapproved submission and repeat posting were rejected.

Independent native reports confirm customer/vendor balances of USD39/USD39 and
inventory valuation of two units/USD10. Five general-ledger entries independently
show AR credit USD15, inventory-asset debit USD10, income debits USD10 plus USD5,
and COGS credit USD10: total debits and credits both USD25.

Installed desktop/mobile review passed over verified TLS without additional writes.
Signed isolated Bridge restore preserved 56 jobs and 2,374 files, both credit
attempts, stock evidence and valid audit/integrity. It remained paused without
starting a restored service. This is a Bridge restore, not a company-file restore.
The full suite passed 1,274 tests, including missing/wrong stock and cost evidence,
source capacity, unsupported settings, lost-response recovery and native query gates.
Lint, JavaScript syntax and package build passed. Private receipts and company
identities remain outside the repository. Sample posting is paused, the bounded
credit quota is exhausted and production is disabled. M3-06 remains partial for
broader customer credit/refund variants.
