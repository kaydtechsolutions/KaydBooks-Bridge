# Environments and measurable acceptance targets

These are development/test targets to implement and measure. They grant no access,
authorize no accounting transaction and make no current performance guarantee.

## Environment separation

| Environment | Purpose | Data and dispatch boundary |
| --- | --- | --- |
| Development | Tests, builds, hostile inputs and fault injection | Synthetic data and temporary private state outside Git; no QuickBooks writes |
| Installed sample | Native QBWC behavior and realistic operator tests | Separately bound sample company; each new bounded batch needs exact authorization; old grants stay exhausted |
| Restored-company staging | Representative chart, settings and workflow acceptance | Owner-authorized backup copy, distinct QBW path and binding, isolated connector/queue/credentials; live file is inaccessible to its writer |
| Production pilot | Limited business-authorized live operation | Explicit enrollment, exact company and first transactions, actor/operation/count/amount/time limits, current backups and emergency stop |

Service restarts must not start a previously disabled writer. A restored Bridge
must start paused, inspect current QB state and reconcile before activation.
Do not change company identity solely to make a failing test pass.

## Supported-platform evidence to collect

- Linux installer targets Debian 13 and Ubuntu 24.04. Qualify standalone systemd
  hosts and Proxmox LXC/VM separately, including DNS, TLS, Tailscale and LXC TUN.
  Proxmox is optional; shared system services and installer checks must not assume it.
- Baseline installer checks 2+ CPU cores, 4 GiB RAM and 10 GiB free storage, with
  a 20 GiB or larger volume recommended. Measure load before promoting these to
  production sizing guidance. Evidence/backup retention may require more space.
- Baseline CI covers Python 3.10–3.13 on Linux and Windows. Pin all release packages
  and record the tested OS, interpreter and dependency revisions.
- Current installed sample evidence identifies US QuickBooks Enterprise 2024 and
  Web Connector 34.0.10010.76. This proves only that tested configuration. Collect
  edition/year/release/country, qbXML versions and bitness during discovery; reject
  unqualified versions/settings precisely instead of assuming all Desktop editions work.
- Real-company OS/QB versions, file location, currency settings and inventory settings
  remain unknown until authorized discovery. Preserve required multicurrency and
  site-aware variants as work; unsupported configurations are explicit held cases.
- Windows and Linux use separate service identities and private credential stores.
  SSH aliases are optional administration conveniences, not user-facing prerequisites.

## Test targets and bounded execution

| Area | Initial measurable target | How to prove it |
| --- | --- | --- |
| Correctness | Exact decimal totals, saved line identity and expected ledger/stock effects; zero unexplained differences | Independent record queries and reports, never the add response alone |
| Duplicate safety | Zero duplicate accounting effects across replay, crash and response-loss cases | Persisted attempt/handoff records plus independent QB queries |
| Tenant isolation | Zero cross-company access or dispatch, including swapped company file and stale credentials | Negative API/MCP/QBWC/worker tests |
| Interactive local work | p95 under 2 seconds for draft/status/preview without remote QB processing at 10 concurrent clients | Repeatable measured workload with hardware and dataset recorded |
| Queue capacity | Sustain 1,000 synthetic queued jobs for 2 hours without loss or unbounded memory growth | Isolated synthetic soak; not 1,000 real accounting writes |
| Remote job latency | Correctly expose waiting state; two successful configured QBWC polling cycles plus measured processing allowance for ordinary read/write/readback | Installed bounded tests with timestamps and documented polling interval; no promise while QB is offline |
| Process recovery | No loss of durably acknowledged local jobs; no resend of uncertain writes | Termination at each durable state boundary and restart |
| Backup recovery | Initial RTO target 60 minutes; backup interval target at most 24 hours | Timed isolated restore; reconcile post-backup QB writes before dispatch, so backup age is not permission to lose/replay accounting |
| Detection | Actionable service/queue/unknown-outcome alert within 5 minutes in the test environment | Induced failure and retained alert receipt |
| Release quality | All applicable acceptance cases pass; no unresolved critical/high security issue or blocker accounting defect | Exact candidate CI, installed qualification and documented review |

These targets are engineering defaults, not customer commitments. Record measured
results and obtain user agreement before revising a failed acceptance target or
turning it into an operational promise. A real-company pilot must specify its own
approved duration and volume before activation; never mark an unspecified observation
window complete immediately after one successful transaction.

For new native sample batches, propose no more than ten uniquely referenced writes
per review package and explicit amounts, counts, source mappings and effects. The
actual authorized package controls, even when smaller. Use fractional quantities
and cent amounts to exercise rounding. Test retries through faults/readback, never
by submitting a real transaction twice. Synthetic testing has no accounting budget.

## External dependencies and when to ask

Continue all independent development while these are pending:

1. Before each new sample batch: present exact drafts and request any missing write
   approval. The completed six-transaction batch and service grant cannot be reused.
2. Before real-company staging: obtain the owner's consent, backup-copy location,
   approved access method, privacy/retention needs and an accounting reviewer.
3. Before pilot activation: present passed technical gates and request exact live
   company, operations, first transactions, limits, window and emergency owner.
4. Before publishing a release: verify publication authorization and exact tested
   commit/artifacts. Existing authority to update GitHub does not activate production.

Do not solicit passwords, private keys or complete accounting data in chat. Discover
existing secure configuration first, then request only missing secure access paths.
Backups, credentials and real-company evidence stay out of public GitHub content.
