# Account role checks

The first reviewed role is `invoice.create` / `receivable`. An optional per-company
`account_roles` configuration maps `invoice_receivable` to an exact QuickBooks ListID:

```json
"account_roles": {"invoice_receivable": "synthetic-ar-account"}
```

This example is synthetic. Actual mappings belong in operator-controlled private
configuration. Unknown roles, malformed IDs and null mappings are rejected. Omitting
the map preserves existing discovery and simulation behavior but blocks role checks.
Names are never used to infer account selection. Operators cannot configure arbitrary
allowed types to bypass the built-in role rule.

Run `python -m kaydbooks_bridge.account_roles` with `--config`, `--credentials`,
`--principal`, `--connector`, `--operation invoice.create`, `--role receivable`,
`--transport qbwc|direct-sdk` and `--evidence-id`. Supply the existing QBWC exact
lookup job ID or direct SDK numeric run ID. No raw XML or caller-supplied records
are accepted and this command does not dispatch queries.

The principal requires both company `read` and `validate` permission and must own
the referenced lookup. The lookup must be verified, exact (not a preview), for the
same connector and current configured ListID. The validator rechecks response
correlation, active status and company binding before requiring `AccountsReceivable`.
It records successful checks in the company audit with policy and response digests.
The result contains evidence references and `scope=saved-evidence-only`; it does not
return account names or raw records.

This is a preflight role match against saved evidence, not posting approval, a fresh
master-data check or full invoice validation. Currency, customer, linked transaction
and item-account compatibility still require separate checks. No check changes an
invoice job's state. No live transaction is enabled.
Intuit's [Desktop SDK Programmer's Guide](https://static.developer.intuit.com/resources/QBSDK_ProGuide.pdf)
documents currency-specific ARAccountRef restrictions; matching account type alone
cannot establish transaction compatibility.

Synthetic tests cover both transports, policy changes, type mismatch, missing roles,
previews, revoked permission, company-binding changes, evidence ownership and audit.
Actual sample-company role validation passed for both QBWC and direct SDK after the
operator explicitly authorized adding company `validate` permission. New exact reads
matched the configured active receivables account; both role checks and company audit
verified. A fresh-process direct SDK CLI check also passed. Only `validate` was added;
no submit, approve or posting permission was granted. Exact mapping and evidence stay
private. Currency/customer/item compatibility is still outside this role-only check.
