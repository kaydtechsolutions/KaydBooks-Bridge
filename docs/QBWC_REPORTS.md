# Customer balances through Web Connector

`qbwc_report_v1` exposes a fresh, read-only Customer Balance Summary to Hermes.
It uses the shared QBWC read queue and requires the assigned company's `read` and
`report` permissions. It never uses the direct SDK, posts entries or sends messages.

All five fields are required:

```json
{
  "company": "company-a",
  "connector_id": "connector-company-a",
  "request_id": "balances-20260908-01",
  "report": "customer-balances",
  "date_to": "2026-09-08"
}
```

Use company and connector aliases from `company_catalog_v1`. Dates are explicit
as-of dates; the report uses accrual basis and all customers, without optional filters.
Repeat identical parameters while `pending=true`, after Web Connector runs. Reuse
that request ID only for the same request. A new fresh snapshot needs a new ID.
If another read is queued, wait for it; do not clear the queue or request a write.

Success returns the company display name verified from the same QuickBooks response,
typed rows, unchanged native totals, requested/native date evidence, currency and
immutable read-start time. Responses older than five minutes, incomplete reports,
wrong-company responses and unsupported multicurrency results release no balances.
Closing a session again cannot renew the evidence timestamp. Unsupported report
types must not be routed to transaction checks or inferred from historical receipts.

Add `qbwc_report_v1` to the named Hermes profile's MCP tool allowlist and reconnect
that profile after deploying the Bridge update. `setup hermes` includes it in new
bundles. Previously generated bundles need this explicit allowlist addition. The
original seven data-entry tools and accounting/confirmation gates are unchanged.

This is an additional report route; the eight-entry v0.1.0 pilot score stays 15/15.
