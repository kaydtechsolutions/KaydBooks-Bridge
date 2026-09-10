# Record a customer credit-card refund

`customer-refund.create` records an accounting refund against existing unused
credit memos. It never contacts a processor. The immutable native request omits
`CreditCardTxnInfo`; card numbers, processing results and arbitrary SDK fields are
not accepted. No bank transfer or actual refund of money is initiated.

This initial path requires a non-tax, single-currency company, active customer,
receivable account, Bank account and credit-card payment method. QuickBooks uses
`AmericanExpress`, `Discover`, `MasterCard`, `OtherCreditCard` and `Visa` method
types. Cash and check refunds require a separately implemented operation.

The payload uses `customer_id`, `deposit_id` (the refund source bank), `method_id`,
`txn_date`, `ref_number`, `currency`, `total_amount` and `allocations`. Every
allocation contains a credit-memo `txn_id` and positive `amount`; their sum must
equal the total. Company mappings remain private. Credit customer, AR, date and
available amount are checked using fresh exact queries before dispatch.

`check_customer_refund_v1` obtains retained read-only evidence;
`prepare_customer_refund_v1` creates a source-linked draft. Validation, review,
approval and submission use the shared lifecycle and never write to QuickBooks.
The SDK CLI also accepts `--refund-check` with a private payload file.

`post-sample-refund` requires a private `sample_refund_posting` gate with connector,
authorization, reference prefix, maximum refunds and expiry. Current permissions,
company identity, approved source and immutable context are checked again after
native preflight. The helper accepts one exact `ARRefundCreditCardAdd` shape.

An independent session must match the saved refund and credit allocations, show
the exact decrease in bank balance and unused credit, and the exact increase in
customer balance. Native queries may omit the allocation-level `CreditRemaining`
returned by Add; the independent exact CreditMemo query remains required. Missing replies or contradictory balances remain held. Use
`reconcile-sample-refund` for read-only recovery; a dispatched refund is never resent.
Production posting remains unavailable.

Reference: [Intuit SDK Programmer's Guide, Chapter 19](https://static.developer.intuit.com/resources/QBSDK_ProGuide.pdf).
