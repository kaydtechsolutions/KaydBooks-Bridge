# Fixed invoice discounts and charges

An invoice may include explicit `adjustments`, each with `kind` (`discount` or
`charge`), `scope` (`line` or `document`), a configured `item_id` and positive
two-decimal `amount`. Line scope also requires a one-based `line_number` into the
original invoice item lines. Original item quantities and list prices are preserved.

Use one document discount or one discount per selected line. A document discount
is allocated proportionally across original item amounts using integer cents and
largest remainders; original line order breaks ties. Every allocated discount is
placed immediately after its item. Zero-cent shares are omitted. For three USD5
items and a USD1 document discount, the reviewed shares are USD0.34, USD0.33 and
USD0.33. Charges are excluded from the discount basis: line charges follow that
line's discount; document charges follow all item lines.

This explicit placement follows Intuit's guidance that a single-item discount
belongs immediately after its item. See [discount placement](https://quickbooks.intuit.com/learn-support/en-us/help-article/accounting-bookkeeping/using-discount-item-invoice/L3qcpBFDR_US_en_US).
The Bridge distributes a document amount into single-item discounts instead of
depending on implicit subtotal behavior. It uses ordinary US `InvoiceLineAdd`
discount/other-charge items, not the Online Edition-only `DiscountLineAdd` aggregate.

Private invoice mappings bind discount aliases to `kind: Discount`, and charge
aliases to `kind: OtherCharge`, with exact item and income account IDs. Fresh checks
require active items/accounts, matching account types, fixed master prices and
non-tax treatment. Percentage/special charge items, inventory tracking on charge
items, foreign currency, tax-inclusive amounts and taxable adjustments fail explicitly.
An absent charge default tax code is accepted only when the invoice explicitly
supplies the independently verified non-tax code. A conflicting default is rejected.

An adjustment cannot exceed its selected item basis, and the invoice net must stay
positive. Company and dispatch budgets count gross items plus charges, so discounts
cannot bypass a limit. Browser review shows the requested scope, amount and exact
allocation before approval. Editing an adjustment invalidates prior checks and approval.

Native requests use explicit signed amounts. Independent readback verifies each
item, amount, order, tax code and invoice subtotal/balance. Historical receipt reports
use the adjusted net. Lost responses remain held for reconciliation without resend.
Customer-credit adjustments and supplier document adjustments remain separate work;
this contract does not enable them implicitly. Production posting remains unavailable.

Installed sample qualification created two separately approved service invoices:
USD15 less USD1 document discount plus USD3 charge (USD17 net), and USD10 less a
USD1 line discount plus a USD3 line charge (USD12 net). Both independently verified
their saved signed lines, order, non-tax codes and remaining balances. The document
discount retained its USD0.34/0.33/0.33 allocation. Repeated posting was refused.
An initial read-only check exposed the omitted charge default code; its response
was retained, the validator corrected and regression-tested, and a new read passed
before any write. No failed accounting attempt or resend occurred.

Customer/vendor balance summaries independently report USD54/USD15 after the two
invoices. Installed desktop/mobile review shows requested scopes and saved allocations;
verified jobs expose no repost action. Signed isolated restore preserved 53 jobs and
2,198 files with valid integrity/audit, paused and without service activation. Native
requests, responses, exact private mappings and test-company identifiers are retained
outside the repository.
