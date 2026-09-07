# Credit memos and bill credits through Web Connector

Browser customer-credit and supplier-credit checks now use fixed Web Connector
queries. The exact original invoice or bill, prior credits, party, accounts,
items, supported inventory settings and source-return capacity are checked before
preparation. Evidence is owned, operation-specific, bound to current mappings and
expires. Pending checks keep saving disabled.

Controlled sample posting uses CreditMemoAdd or VendorCreditAdd through the shared
durable lifecycle. Independent exact-transaction readback must match all saved
lines and prove the party balance effect against the original preflight. Supplier
credits also verify the complete net payable/unused-credit evidence. Inventory
returns require the expected stock movement and supported unchanged average cost;
customer returns can start with zero stock. Incorrect balances, quantities or
costs remain posted-unverified.

Each operation keeps its own cumulative native/QBWC quota. One attempt permits one
write handoff; reference-search recovery reuses the retained original baseline and
sends no writes. Missing results never authorize resending. Previous native jobs
retain native reconciliation. Credit application and refund workflows, scheduled
native dispatch and legacy CLI commands are separate from this migration.

The affected regression suite passed 226 checks, including credit/payment/bill/
invoice callbacks, original-record and inventory boundaries, schema upgrades and
web contracts. Six browser waiting-state checks passed. Lint, formatting,
JavaScript syntax and package build passed. A separate installed-database copy
retained identical contents across all 56 tables and 3,739 rows with valid audit,
integrity and foreign keys; the live database was not modified by that check.
Actual installed QuickBooks credit qualification remains pending. Automated
evidence does not close the broader release gates or enable production posting.
