# Customer payments through Web Connector

Browser customer-payment detail checks now queue fixed customer, account, method
and allocated-invoice reads. Evidence binds the operation, actor, company,
connector, exact payload and current mappings, and expires normally. Draft saving
waits for the check to complete. The sample posting action queues a ReceivePayment
through the existing durable Web Connector lifecycle. Existing native attempts
retain their original reconciliation path.

Posting retains one attempt and allows one write handoff. Independent exact-TxnID
readback validates the saved payment, deposit account, method and allocations. It
also verifies each invoice's balance reduction against the original preflight
response, including any explicit discount. Unapplied payments require their
existing company setting and validate the unused amount. A successful add response
alone never marks the job verified.

After an uncertain outcome, recovery searches the reference and reads back the
exact saved transaction. It reuses the original balance evidence, sends no writes
and retains consumed quota. Missing or mismatched results stay held. A matching
preexisting payment without an original Bridge balance baseline also stays held.
Native and QBWC customer-payment attempts share their own cumulative limit,
separately from invoices and bills. Scheduled native dispatch and legacy native CLI
commands are not migrated by this browser workflow change.

The read-operation schema upgrade preserves existing rows, their insertion order,
indexes and immutable guards inside one transaction. Tests cover a legacy
invoice/bill database, rollback, repeat initialization, unknown-operation rejection
and pending-read exclusivity. An isolated installed-database copy retained all
56 tables and 3,236 rows with identical content, valid audit, integrity and foreign
keys, and paused posting. The original database was not modified.

Automated callback tests cover partial/full payments, discounts, unapplied funds,
lost responses, wrong balance effects, absent recovery results, revoked write
permission, never-started attempts, and owned/fresh browser evidence. Installed
QuickBooks customer-payment qualification remains pending. These automated checks
do not close an acceptance gate or enable production posting.
