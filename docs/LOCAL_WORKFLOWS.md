# Local workflows and receipt reports

These optional adapters use the same private company state and authenticated principal.
Run `python -m kaydbooks_bridge.operations --company company-a ACTION [INPUT_JSON]`
with private `KAYDBOOKS_CONFIG` and environment `KAYDBOOKS_TOKEN`.
The installed entry point is `kaydbooks-bridge-operations`.
Hermes can access these contracts through board_v1, memory_v1, receipt_register_v1
and workflow_v1 when its company principal has the required grants. This does not
activate Hermes' independent cron, messaging or agent-delegation systems.

| Action | Required private JSON fields | Permission |
| --- | --- | --- |
| board | none | read |
| schedule | schedule_id, timezone, first_run, interval_seconds, max_runs, dependencies | manage-workflows/read |
| cancel | schedule_id | manage-workflows; owner only |
| tick | none | manage-workflows/read |
| remember | name, value, expires_at, provenance, expected_version | manage-workflows |
| memory | none | read |
| delegate | job_id, assignee | manage-workflows; job owner; assignee already has read/validate |
| report | date_from, date_to | report/read |
| export | date_from, date_to, destination | export/report/read |

Schedules currently run only local `board.snapshot`. They never post invoices or
invoke arbitrary commands. First-run timestamps must include an offset consistent
with the named IANA timezone. Cadence is elapsed seconds (60–2,678,400), not local
wall-clock recurrence across daylight-saving changes; max_runs is 1–1000.
Dependencies are existing company job IDs and must be verified. One occurrence per
due schedule is processed per tick, bounded at 100 schedules. Catch-up is incremental.

Occurrence ID, result, local outbox entry and next due time commit atomically. SQLite
serializes competing ticks. Owner permissions, pause and captured policy are rechecked;
changed policy/dependencies hold execution. Cancellation preserves definitions and
past results. There is no background timer installed: operators or an explicitly
configured local scheduler invoke tick. No Hermes cron or external schedule was enabled.

The outbox contains redacted company/state counts and is strictly `local-only`.
No recipients or delivery transport exist; retrying notifications cannot resend
accounting. Connecting external channels requires recipient/delivery authorization.
Memory supports only versioned, expiring display-label/preferred-report hints with
provenance. It cannot contain configured master IDs, balances or permission grants.
Delegation records the canonical job ID without changing ownership or granting access.
Board cards are projections; no card-movement write API exists.

The supported report is `verified-invoice-register`, filtered by inclusive transaction
dates. It includes only independently verified real receipts, with source-response
hashes and observation timestamps; synthetic-only verified jobs are excluded. Totals
are derived from matched historical receipts. It is neither a complete QuickBooks
ledger nor current accounts receivable. It does not re-query current balances.
JSON exports require a new private path and never overwrite files.

Native QuickBooks financial reports, Hermes message delivery/cron/delegate-task/Kanban
connections and accounting GUI fallbacks remain unavailable or disabled. Their local
alternatives above remain usable without those integrations.
