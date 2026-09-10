# Explicit settlement discounts

Customer receipts and supplier payments accept an optional positive two-decimal
`discount_amount` and an explicit `discount_account` on each allocation. Both fields
must be supplied together. Omit both for a cash-only allocation; zero, negative and
fractional-cent discounts are rejected. Discounts are never inferred from payment terms.

For example, a customer allocation can settle USD7 using USD6 cash and USD1 discount:

```json
{
  "txn_id": "synthetic-invoice-id",
  "amount": "6.00",
  "discount_amount": "1.00",
  "discount_account": "customer_discount"
}
```

The receipt/payment `total_amount` remains the cash amount. Allocation cash must obey
the existing total/unapplied rules. Cash plus discount cannot exceed the independently
read outstanding balance. Company and dispatch budgets include both cash and discount.
Review shows cash, discount and total settled separately. Editing either discount field
invalidates the browser's previous check.

Configure private `account_roles.customer_discount` as an active Income account and
`account_roles.supplier_discount` as an active Expense account. Native ListIDs stay in
private configuration. These deliberately narrow account types are rechecked before
dispatch and read-back; changing the mapping invalidates the reviewed context. The
current path requires a single-currency company. Tax remains excluded.

The fixed qbXML allocation contains `PaymentAmount`, `DiscountAmount` and
`DiscountAccountRef`. Saved `AppliedToTxnRet.Amount` is the cash allocation; the
discount amount/account are independently matched. A separate read verifies that
the original invoice/bill balance decreased by cash plus discount. Existing credits
and payments remain related history and are never counted as this payment's allocation.

## Native reference rejection and recovery

BillPaymentCheck references are limited to 11 characters. Browser input, payload
validation and the native write helper enforce this before a new dispatch. Read-only
reconciliation can still inspect a legacy request made under the former 20-character
validation bound; this compatibility path cannot build or send a new write.

A correlated native error 3070 with no returned transaction can be resolved as failed
only after the helper has closed, the original request and write-intent hashes agree,
an independent exact-reference query finds no payment, and current identity, authority,
context and audit checks pass. The rejection proof is immutable. Missing or conflicting
proof leaves the job uncertain. The original attempt cannot be resent or edited.
A corrected payment requires a new reviewed source/job, separate approval when configured,
fresh checks and available dispatch authorization.

## Sample qualification

An installed USD6 customer receipt plus USD1 discount settled a USD7 invoice balance.
An installed USD7 supplier payment plus USD1 discount settled a USD8 bill balance.
Both saved native records and independently read remaining balances matched exactly.
Fresh Customer Balance Summary and Vendor Balance Summary reported USD25 and USD15.
Separate approvals and duplicate refusal passed. No payment processor was invoked.

The initial customer read was recovered after correcting the read helper's fixed
discount-account query. The first supplier attempt was rejected for a long reference;
the original correlated rejection and independent absence were retained without resend.
One separately approved corrected job then verified successfully. The company is paused;
the private customer/supplier attempt quotas are exhausted. Production posting is disabled.

Signed isolated restore preserved all 45 jobs and 1,968 files, both verified discount
receipts and the immutable rejected attempt, with valid integrity/audit and no restored
service activation. Automated coverage includes partial/full settlement, missing replies,
exact discount accounts/amounts, limits, native query/write rules and rejection-proof
tampering. Document/line discounts and additional-charge variants remain M3-08 work.

Primary schema references: Intuit's [ReceivePaymentAdd](https://developer.intuit.com/app/developer/qbdesktop/docs/api-reference/qbdesktop/receivepaymentadd)
and [BillPaymentCheckAdd](https://developer.intuit.com/app/developer/qbdesktop/docs/api-reference/qbdesktop/billpaymentcheckadd),
including its [reference-length schema](https://static.developer.intuit.com/qbSDK-current/common/newosr/qbsdk/json/BillPaymentCheckAddRq.json).
