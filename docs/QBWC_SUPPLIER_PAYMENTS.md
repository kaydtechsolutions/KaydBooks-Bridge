# Supplier payments through Web Connector

Browser supplier-payment checks now queue vendor, bank, payable-account and
allocated-bill queries through Web Connector. Evidence binds the operation,
submitter, company, connector, exact payload and current mappings, and expires.
Customer-payment evidence cannot authorize a supplier payment.

The sample posting action queues a BillPaymentCheck through the shared durable
lifecycle. Independent saved-payment readback verifies the payee, bank account,
amount and allocations, then proves each bill's outstanding balance decreased by
the payment plus any explicit discount. BillToPay supplies the per-bill balance;
the vendor's aggregate balance is not substituted. Unallocated supplier payments
remain unsupported.

Lost-response recovery searches the reference and reads the exact saved payment.
It uses the retained original balance baseline and never resends the write.
Missing results, wrong balance effects and missing original evidence stay held.
Supplier-payment native and QBWC attempts share their own cumulative quota;
customer payments, bills and invoices retain separate limits and histories.
Existing native reconciliation remains available for native attempts. Scheduled
native dispatch and legacy CLI commands are not migrated by this browser change.

Automated coverage includes partial/full payments, discounts, lost responses,
incorrect payable effects, missing recovery results, revoked permission,
never-started attempts, fresh owned evidence and cross-operation rejection.
Browser checks keep draft saving disabled while QBWC evidence is pending.
Schema-upgrade tests cover older two-operation and three-operation databases,
transaction rollback, immutable history and pending-read exclusivity.
Installed sample-company qualification must be recorded separately; these tests
do not establish production readiness or close broader acceptance gates.
