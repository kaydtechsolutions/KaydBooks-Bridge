# Bounded scheduled and automatic sample dispatch

Manual remains the default. Creating a company or starting the web service does not
start an accounting worker. These modes use the existing nine sample-only native
operation contracts; production posting is unavailable.

In **Posting schedules**, select an exact queued job for scheduled mode, or an
operation and original source namespace for automatic mode. Set the first UTC run,
cadence, run count, expiration, missed-run policy, job counts and amount budgets.
Review the exact rules before enabling the profile. Editing a field invalidates
the review. The API also supports explicit IANA timezones with matching offsets,
multiple operations/sources and up to 100 exact scheduled jobs. Cadence is elapsed
seconds from the first instant; it is not a floating local wall-clock appointment
and does not change with daylight saving time.

Only owned queued work can be selected. Preparing, validating, reviewing sources,
approving and submitting remain separate actions. The current company approval
policy applies at the native write fence; schedules cannot grant approval, bypass
field review, refresh stale masters, expand permissions or increase native quotas.
All required permissions and sample identity checks are shared with manual posting.

## Worker and operator controls

The browser's **Run due sample work** runs one due-work scan. For unattended runs,
start the installed `kaydbooks-bridge-dispatch` project service explicitly with
`--company` and `--token-env`. Supply the private configuration through
`KAYDBOOKS_CONFIG`, and optionally load the existing private credential file through
`KAYDBOOKS_QBWC_SECRET_FILE`. The token argument is an environment variable name,
never the credential itself. `--once` performs one scan and exits; otherwise the
worker scans every five seconds. Provision service ownership and monitoring using
the deployment procedure; no user credentials belong in the repository.

Cancellation is durable and irreversible for that profile ID. Create a new profile
to change rules. Pausing the company stops planning and native authorization. A
process-wide OS file lock prevents overlapping company dispatch workers and is
released automatically when its owner exits. Native operations retain their separate
SDK lock and QuickBooks session ownership.

## Missed runs, limits and recovery

- **Skip:** missed instants beyond the grace period are recorded without dispatch.
- **Coalesce:** record missed instants but execute at most the latest due occurrence.
  No catch-up burst executes every missed batch.
- Immutable occurrence records bind exact job fingerprints and amounts before any
  native call. Claims reserve lifetime count/amount budgets, including failed or held
  attempts. One job can belong to only one dispatch occurrence across all profiles.
- Exceptions remain in review. A limit failure leaves the original queued job intact;
  an attempted dispatch that fails a check retains its claim and result. It is not
  automatically reselected later or silently moved into another profile.
- If the parent exits after planning but before native preparation, the original
  queued claim can resume after restart. Once native preparation changes its state,
  restart records the existing state and requires reconciliation, never resending.
- Missing responses and failed read-back use the existing operation-specific
  read-only reconciliation. Completed occurrence records do not prevent that recovery.
  Do not revise an unknown or posted transaction. A held undispatched job may use
  the normal immutable correction workflow after the cause is resolved.

Every native operation checks the original claim, current profile, current grants,
policy fingerprint and expiration both before preparing an attempt and immediately
before authorizing its write. A claimed job cannot bypass those rules by using the
manual posting endpoint. Cancellation before the final authorization transaction
prevents the write. An already authorized native write cannot be recalled; it must
finish or be reconciled without resend.

No accounting dispatch tool is added to the read-only/preparation Hermes MCP surface.
The authenticated browser and explicitly started project worker are the entry points.
Notifications remain a separate delivery contract and never trigger accounting retry.

## Evidence and current qualification

Synthetic tests exercise both modes through the real durable posting lifecycle,
including independent read-back, cancellation and revocation at the native fence,
missed runs, restart, no overlap, budget holds, rule validation and lost responses.
Chromium tests cover rule review invalidation, cancellation and persisted status.
Installed qualification separately wrote and independently verified two approved USD5
sample invoices, one per mode. Unapproved submission was refused. Restarted workers
made no repeat writes. A fresh Customer Balance Summary matched the expected USD10
increase. Both profiles were cancelled and the company paused. Signed isolated restore
preserved both claims/results and 32 jobs, with no restored worker/service activation.
The 1,087-test suite includes 22 dispatch and 16 browser cases. This establishes the
bounded single-currency sample paths; notification delivery and the broader operation
matrix remain separate acceptance gates.
