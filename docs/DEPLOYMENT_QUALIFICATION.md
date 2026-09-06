# Sample deployment qualification and production gate

The implemented pilot is limited to the confirmed sample company: non-tax service,
simple inventory/mixed invoices, expense/service/inventory bills, customer receipts,
fixed SDK/QBWC read paths and the local tools/workflows described in this repo.
Production posting and release publishing remain disabled.

Use [private company setup](COMPANY_SETUP.md) for each deployment. No sample company
name, QBW path, credential or master mapping is required in application code.
General development and sample qualification do not require a production company.

## Snapshot and isolated restore drill

Use a private `KAYDBOOKS_BACKUP_SIGNING_KEY` of at least 32 characters, retained separately
from snapshots. A credential file can supply it to the trusted operator process.
The operator needs backup/read permission. Pause the company and resolve all active
SDK/QBWC sessions and uncertain writes before taking a snapshot.

```
python -m kaydbooks_bridge.qualification --company company-a backup NEW_PRIVATE_DIRECTORY
python -m kaydbooks_bridge.qualification --company company-a restore-drill NEW_ISOLATED_ROOT --snapshot PRIVATE_SNAPSHOT
```

`kaydbooks-bridge-qualification` is the equivalent installed entry point. Config and
token come from the standard private environment. Snapshot destinations must be new,
outside Git and outside the live company directory. Resume the source company after
the snapshot operation, including when it fails.

A company SDK lock and database writer fence protect a consistent SQLite backup plus
native evidence files. The manifest binds company policy, connector identity/settings,
file SHA-256 values and the audit head, authenticated with HMAC-SHA256. Config and
credentials are excluded. Limits are 128 MiB per file and 512 MiB total; symlinks fail.

Restore drills validate signature, binding, paths and hashes before copying into a
new root. They check restored file hashes, SQLite integrity/foreign keys, append-only
audit continuity and paused state. No live directory is replaced, no runtime config is
generated, and no restored service is started. This is an isolated migration/restore
drill, not an operational failover implementation. A trusted OS administrator who has
the signing key can rewrite evidence; HMAC is not a third-party immutable checkpoint.

## Latest staging evidence

- 742 local tests pass. Three customer receipts total USD20: USD15 applied to the mixed
  invoice and USD5 unapplied. Independent Customer Balance Summary matches USD25.
- The latest installed-package signed restore recovers 14 jobs with valid integrity,
  foreign keys and audit. It stays paused and launches no service.
- The following earlier drills remain historical evidence; their counts predate later work.

## Earlier verified staging evidence

- Native helper compilation; normal sample posting and independent readback.
- A second controlled sample invoice: abrupt parent exit after the native helper saved
  its response; lease recovery, fresh-process read-only reconciliation and duplicate
  dispatch refusal passed. Two new USD10 invoices were created under this authorization.
- Earlier isolated sample invoice retained: the historical receipt register contains
  three invoices totaling USD30. Register generation itself sends no accounting writes.
- Installed Hermes discovered thirteen tools; actual MCP-to-SDK sample preparation
  and local workflow/report calls passed.
  No model calls or external messages occurred.
- Bounded local scheduling, duplicate ticks, cancellation, delegation and preferences
  passed against private staging data; the test schedule was cancelled.
- Signed snapshot and isolated restore recovered five staged jobs with valid audit and
  database integrity. The restored state stayed paused and no connector was launched.
- Private staging and tool-secret ACLs were inspected and restrict access to the local
  operator. The HTTPS service binds loopback; existing TLS/QBWC authentication remains.
- A separately installed wheel runtime passed the private workflow, report and signed
  restore drill, independently of the editable development environment.

Exact company identifiers, sources, credentials, snapshots and proof files stay outside
Git. Public tests use synthetic data; real native/Hermes evidence is recorded separately.

## Remaining production requirements

No production approval is requested by the software itself. A production pilot needs
explicit operator authorization and a separate company-onboarding review, supported
transaction/report scope, operational backup/failover procedures, dedicated least-
privilege service identity, external immutable audit retention, monitoring and ownership.
Taxable and advanced-inventory variants and native financial-report APIs need separate real
qualification. OCR quality, full Hermes conversational workflows and optional external
collaboration have not been qualified. Current code rejects unsupported operations.

No production accounting changes, business-record deletion, external messaging, paid
model calls, PR merge or release publication are part of the staging qualification.
