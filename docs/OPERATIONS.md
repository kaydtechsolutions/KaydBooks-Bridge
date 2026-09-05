# Foundation operations

This is local simulation software. Use only synthetic data. No QuickBooks or Hermes
access, network listener, desktop session or accounting credentials are needed.

## Prerequisites and private onboarding

Use Python 3.10–3.13 and uv, then `uv sync --frozen --extra dev --python 3.12`.
Create a private directory outside Git, owned by the service operator. Production
deployment must enforce restrictive Windows ACLs or Unix permissions, disk encryption
and backups. This milestone checks paths and company database bindings; it does not
provision OS accounts, encrypt SQLite, set Windows ACLs or rotate credentials for you.
Never use a shared/network filesystem for the SQLite queue.

Example PowerShell setup, with newly generated temporary secrets held only in memory:

```powershell
$bridgePrivate = Join-Path $env:LOCALAPPDATA 'KaydBooksBridgeSimulation'
New-Item -ItemType Directory -Force -Path $bridgePrivate | Out-Null
$bridgeConfig = Get-Content examples/company-config.example.json -Raw | ConvertFrom-Json
$bridgeConfig.state_root = Join-Path $bridgePrivate 'state'
$env:KAYDBOOKS_CONFIG = Join-Path $bridgePrivate 'config.json'
$bridgeConfig | ConvertTo-Json -Depth 10 | Set-Content -Encoding utf8 $env:KAYDBOOKS_CONFIG
$env:KAYDBOOKS_PREPARER_A_SECRET = uv run --frozen python -c 'import secrets; print(secrets.token_urlsafe(32))'
$env:KAYDBOOKS_APPROVER_A_SECRET = uv run --frozen python -c 'import secrets; print(secrets.token_urlsafe(32))'
$env:KAYDBOOKS_OPERATOR_A_SECRET = uv run --frozen python -c 'import secrets; print(secrets.token_urlsafe(32))'
$env:KAYDBOOKS_OPERATOR_B_SECRET = uv run --frozen python -c 'import secrets; print(secrets.token_urlsafe(32))'
$env:KAYDBOOKS_TOKEN = $env:KAYDBOOKS_PREPARER_A_SECRET
uv run --frozen kaydbooks-bridge check-config
```

Use PowerShell 7 for the UTF-8 example. A deployment secret manager should inject
long-lived secrets later. Never paste them into chat, commit them, echo them in logs,
pass them as CLI flags or let an agent choose a different principal's environment.
The one-shell demonstration holds all synthetic roles only to exercise the approval
flow; a real deployment must separate those role credentials and operators.

Company A and Company B have independent master allowlists, base currencies, total
limits, source allowlists and approval policy. The template intentionally grants no
Company B preparation capability. Add explicit per-company principals/grants to the
private config to onboard that company; never use a default company or wildcard grant.
All master IDs in this milestone are synthetic internal IDs, not resolved QuickBooks IDs.

## Synthetic invoice round trip

```powershell
$bridgeJob = uv run --frozen kaydbooks-bridge --company company-a prepare examples/synthetic-invoice.json | ConvertFrom-Json
uv run --frozen kaydbooks-bridge --company company-a validate $bridgeJob.id
$env:KAYDBOOKS_TOKEN = $env:KAYDBOOKS_APPROVER_A_SECRET
uv run --frozen kaydbooks-bridge --company company-a approve $bridgeJob.id
$env:KAYDBOOKS_TOKEN = $env:KAYDBOOKS_PREPARER_A_SECRET
uv run --frozen kaydbooks-bridge --company company-a submit $bridgeJob.id
$env:KAYDBOOKS_TOKEN = $env:KAYDBOOKS_OPERATOR_A_SECRET
uv run --frozen kaydbooks-bridge --company company-a simulate
uv run --frozen kaydbooks-bridge --company company-a status $bridgeJob.id
uv run --frozen kaydbooks-bridge --company company-a audit
```

`verified` here means the separate synthetic ledger read matched the normalized
invoice exactly. It is not evidence that QuickBooks has a saved record.
The sample source digest is synthetic and is not a hash of an attached document.

Repeated preparation with identical source/payload returns the canonical job.
Conflicting reuse of an idempotency key, source namespace/reference or case-insensitive
invoice reference fails. Invoice references are conservatively unique across a
company's invoice-create jobs; changing the source to evade this check is unsupported.

## CLI and permissions

| Command | Permission | Effect |
| --- | --- | --- |
| capabilities | none | Public capability inventory; no private deployment output |
| check-config | authenticated principal | Validate private config; does not print config |
| prepare | prepare | Persist validated-shape draft with source evidence |
| validate | validate | Require certain fields and current policy |
| approve | approve | Approve fingerprint as a different principal |
| submit | submit | Queue validated, approved work |
| simulate | simulate; current initiator submit and approver approve | One queued synthetic write/read-back |
| status / audit | read | Company-scoped status/evidence |
| pause / resume | pause | Persist company dispatch control |
| recover | recover | Fence expired in-flight attempts; move to unknown |
| reconcile | recover | Read synthetic candidates and saved record; no writes |

Action permissions imply access to the job returned by that action within the company;
`read` grants general job/audit browsing. All grants are company-wide in this milestone.
There are no row-level, account-level or amount-specific user grants yet. Amount limits
are company policy. Only `invoice.create` is implemented and it is simulation-only.
Raw qbXML, arbitrary SQL, arbitrary job-state updates and live mode are rejected.

## Pause, restart and reconciliation

```powershell
uv run --frozen kaydbooks-bridge --company company-a pause
uv run --frozen kaydbooks-bridge --company company-a recover
uv run --frozen kaydbooks-bridge --company company-a status
uv run --frozen kaydbooks-bridge --company company-a reconcile JOB_ID
uv run --frozen kaydbooks-bridge --company company-a resume
```

Replace JOB_ID with the canonical returned ID. The simulator's lease is 60 seconds.
Recovery does not touch an unexpired attempt or replay any write. A pause prevents
new dispatches; it cannot retract a write already sent. Unknown and posted-unverified
states block the company's next write even after resume.

No match, several matches or a mismatched saved record means **hold**. Do not delete
the job, edit SQLite, change keys or run a GUI posting attempt to bypass the hold.
There is deliberately no operator retry override in this milestone. Future retry
authorization must bind conclusive evidence of absence to a specific attempted write
and account for late responses. Transport timeouts are not evidence of absence.

Draft revision, cancellation and blocked-job correction are not implemented. Preserve
the job and evidence until that workflow is added. Submission of an uncertain draft
is refused. A production intake flow needs a reviewed revision mechanism before use.

## Backups, upgrades and deployment preparation

Pause the company and wait for any current simulator process to finish. Preserve
unknown outcomes. Take a consistent SQLite backup using the SQLite backup API, or stop
all processes before copying the entire private company directory. Back up configuration
separately through the private secret/configuration system. Never copy live database
files piecemeal or add backups to Git.

Restore to a private staging directory and verify database company/schema metadata,
SQLite integrity, audit chain and all unresolved jobs before resuming. Restore both
job and synthetic-ledger stores together for a simulation drill. A real QuickBooks
ledger is independent: restored queue state requires external reconciliation before
any writes. Schema versions other than 1 fail closed; no automatic migration exists.

Production preparation still needs TLS/QBWC authentication, authenticated callback
company binding, request size limits, durable callback replay protection, fresh master
queries, exact transaction/report schemas, structured response evidence, OS isolation,
secret rotation, audited policy changes, backup/restore drills and tested upgrades.
Do not deploy the inherited generic `qbwc_kit.server` directly as the Bridge service.
Do not install `qbwc-kit` separately alongside this distribution: both own that namespace.

Real integration access is requested only when starting M2: first an authorized
test-company discovery session with posting disabled. The later Hermes contract test
needs an isolated profile and scoped bridge credentials; notification recipients or
GUI permissions are requested only for the corresponding optional feature.
