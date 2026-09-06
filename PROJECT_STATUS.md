# KaydBooks Bridge project status

Updated: 2026-09-06. Live posting: DISABLED. Real direct SDK discovery: PASS; QBWC qualification: BLOCKED.

## Latest qualification: native helper interruption/recovery

- Actual sample-company fault injection passed at two native-helper checkpoints.
  Private copies of the packaged helper added only a checkpoint marker and pause;
  the qualification harness terminated the exact child PID. QuickBooks itself was
  not terminated. Source/instrumented hashes, PIDs, XML and results remain private.
- After a real response was saved but before session closure/publication, termination
  left the durable run dispatched with no published response. A fresh service refused
  automatic replay. Explicit authorized read recovery issued one new fixed read,
  verified the operator-confirmed binding and completed successfully.
- After closure and atomic response publication, termination likewise left the
  parent journal dispatched. Recovery consumed the saved real response with no SDK
  call. Both original captured payloads passed the shared company-binding verifier.
- A subsequent independent Python process verified both completed runs without
  dispatch. Audit chains passed; dispatch counts were exactly two for explicit
  recovery and one for saved-response recovery. No accounting writes were enabled.
- Focused synthetic regression: **26 passed** (SDK and QBWC discovery). Previous
  full-suite evidence remains 251 passed; application source was unchanged here.
- Limits: controlled native process termination is now real-tested at these two
  boundaries. Power loss, termination inside ProcessRequest, parent-only death with
  a surviving helper, and production-company operation remain unqualified. QBWC's
  AppLock registration blocker remains unchanged. No QWC import/removal is required.
- Next: qualify parent-only interruption/overlap, then extend narrowly supported
  read-only adapters. No missing user access was needed for this qualification.

## Latest milestone: durable direct SDK discovery

- Integrated fixed read-only HostQuery/CompanyQuery with shared private configuration,
  actor/connector authentication, company read/recover permissions, SQLite durability,
  immutable evidence, binding validation and chained audit. Added an operational CLI
  and [direct SDK runbook](docs/DIRECT_SDK.md). No transaction dispatch was enabled.
- Requests persist before dispatch; responses persist before verification. Repeated
  run IDs recover saved responses without SDK calls; missing responses require explicit
  authorized read recovery. Per-company active sessions exclude QBWC and direct SDK
  overlap. A native mutex serializes helpers across parent-process exits.
- Real synthetic-company integration: two successful integrated SDK discovery runs,
  including one after staged HTTPS service restart. Each matched the previously
  operator-confirmed private binding. New-process replay used persisted evidence
  without dispatch; the audit chain verified. HTTPS health reports posting disabled.
  Exact responses, permissions, closure evidence and qualification summary are private.
- Local verification: **251 tests passed**, including **8 new synthetic direct SDK
  tests** for response recovery, duplicate execution, held missing responses, binding
  mismatch, overlap, permissions, immutable evidence and audit integrity. Ruff lint
  and format passed; wheel/source build passed. One inherited deprecation warning remains.
- Real SDK process-crash/power-loss recovery and actual mismatched-company sessions
  are not qualified. Those failure paths are synthetic tests, not real QBWC results.
  QBWC registration/callback qualification remains blocked by AppLock metadata behavior.
- Next: qualify native helper interruption/recovery on the sample company, then extend
  explicitly supported read-only adapters. Hermes and production transaction support
  remain unqualified. Earlier chronological entries below describe prior milestones.

## Scope and branch

- Started from `main` at `4f86e44f890c88ee89f53daf0ec2b8c0f59730ff`.
- Inspected open draft PR #1; merged its exact head
  `3ce990cd3091dc5c52adfca793025ace30cfadfc` into `codex/foundation`.
  PR #1 is not assumed merged into main.
- No repository or parent AGENTS.md found. Upstream MIT attribution retained.
- Public repository; examples and tests must contain synthetic data only.
- Review PR: [#2 — company-scoped foundation](https://github.com/kaydtechsolutions/KaydBooks-Bridge/pull/2).
  Native Git push stalled; uploaded through the connected GitHub API. The uploaded
  milestone trees exactly matched the tested local trees. Original local commit
  history is preserved on `codex/foundation-local`; `codex/foundation` tracks the PR.

## Completed / evidence

- Verified Git 2.54.0, uv 0.12.7, isolated `.venv` with Python 3.12.14.
  Shell Python alias, gh and dotnet were unavailable; none blocks foundation work.
- Baseline: `uv run --frozen pytest -q`: 167 passed, 4 failed (171 total).
  All failures concern the exact TTL deadline on Windows. Baseline Ruff passed.
- Fixed inclusive expiry boundary; added deterministic clock regression.
- Reviewed inherited release workflow: v-tags/manual runs could publish the upstream
  qbwc-kit package using PYPI_TOKEN and create releases without test gates.
  Replaced with manual build validation and artifact upload; removed publishing,
  secret references and write permissions. This protection takes effect on main
  only after review/merge. Do not create release tags on the inherited main branch.
- CI now includes Windows and Linux. No release or deployment performed.

## Foundation milestone implemented and synthetic-tested

- `kaydbooks_bridge` application package and authenticated CLI; private config/state
  rejected inside Git checkouts. Company A/B templates contain synthetic values only.
- Separate company databases and durable company/schema binding; strict Decimal
  amounts, dates, currency, masters, source allowlists and uncertainty checks.
- Immutable prepared jobs, fingerprint-bound separate approval, current policy and
  permission checks, persistent pause, per-company serialized dispatch.
- Canonical idempotency including aliases, source/reference duplicate checks,
  append-only chained audit, receipt persistence and independent synthetic read-back.
- Durable synthetic ledger, unknown/posted-unverified holds, explicit expired-attempt
  recovery and read-only reconciliation. No retry override or live transport exists.
- Shared service-boundary tests cover CLI/chat/document/tool/schedule/delegation/
  Kanban/browser/desktop labels. These are not real interface integration tests.
- Latest local suite: **235 passed**, including 63 Bridge synthetic tests and 172
  inherited/regression transport tests. Ruff lint and format checks pass.
- Actual subprocess termination after a synthetic external commit was tested:
  restart preserved in-flight state, recovery marked unknown, reconciliation found
  one saved record, and no second write occurred.
- PowerShell CLI walkthrough completed prepare → validate → approve → submit →
  simulate → verified, with valid audit. Runtime state/secrets remained outside Git.
- Distribution renamed to `kaydbooks-bridge` (`0.1.0.dev1`), preserving the upstream
  `qbwc_kit` namespace and license. Wheel/source build passed; no upload/release.
- `twine check` passed for wheel and source archive. Clean isolated wheel install
  with no dependencies imported both namespaces and confirmed the disabled live gate.
  Source archive includes operational docs and synthetic examples.
- CI uses the frozen uv lock on Windows/Linux across Python 3.10–3.13 and builds
  artifacts. [PR checks](https://github.com/kaydtechsolutions/KaydBooks-Bridge/pull/2/checks)
  are the live source for remote results; local passes do not establish remote success.
- Initial remote CI passed all four Linux versions and lint/build, but exposed a
  Windows path-canonicalization race during concurrent first-company initialization.
  Directory creation now precedes canonical containment comparison; symlink escape
  protection has an additional regression test. Follow-up [CI run 33980287486](https://github.com/kaydtechsolutions/KaydBooks-Bridge/actions/runs/33980287486)
  passed all eight Windows/Linux Python 3.10–3.13 combinations plus lint/build at
  commit `781c168c9a6e370e73f42689f6eeb7d9a1f99e41`. This subsequent status update
  changes documentation only.
- Architecture, M0–M7 acceptance plan, capability evidence rules, onboarding,
  permissions, troubleshooting, pause/recovery, backup/upgrade and deployment gates
  are documented in `docs/`. One upstream Starlette/httpx deprecation warning remains.
- Durable queue integrity is now enforced in SQLite after restart: immutable prepared
  identity/payload/source, append-only jobs and idempotency aliases, one-time approvals
  and receipts, coupled dispatch attempt fields, legal state transitions, immutable
  company/schema metadata, and boolean durable pause control. Tests prove malformed
  inserts, requeue of unknown writes, mutation/deletion and receipt/approval rewrites
  are rejected. Dispatch attempt IDs are retained through reconciliation as evidence.
- Queue-hardening CI run `33989477054` passed lint/build and all eight Windows/Linux
  Python 3.10–3.13 jobs. No real Hermes or QuickBooks behavior was exercised.
- Durable read-only QBWC discovery now maps each authenticated connector identity to
  one configured company and rejects missing, ambiguous or inconsistent CompanyRet
  fingerprints. At least three official CompanyRet claims are required, including a
  claim stronger than display/fiscal names; callback file paths are hashed evidence,
  never identity. Connector passwords and optional file paths remain environment-backed.
- Reviewed the official Intuit QBWC callback guide, SDK programmer guide and CompanyQuery
  OSR schema. The adapter persists HCP preflight, callback context, exact correlated
  HostQuery/CompanyQuery request, exact response and callback outcomes before advancing.
  It checks response count/status/correlation, host country, negotiated supported qbXML
  version and configured company digest. It emits no write request and has no task hook.
- Seventeen focused synthetic tests cover inherited fake-connector flow, restart recovery,
  exact duplicate callbacks,
  conflicting responses, expired tickets, overlapping connectors, disconnect release,
  cross-company/cross-session replay, path/name insufficiency, missing/ambiguous/mismatched
  bindings, documented country/version minimums and immutable SQLite evidence. Full local
  suite, Ruff lint/format, wheel/source build and Twine metadata checks pass. The artifacts
  contain the new module and discovery documentation. No QuickBooks process was involved.
- [QBWC discovery CI run 33990832056](https://github.com/kaydtechsolutions/KaydBooks-Bridge/actions/runs/33990832056)
  passed lint/build and all eight Windows/Linux Python 3.10–3.13 jobs at feature commit
  `da4bab2d179cf1d5f6a2efd21304979996d3c226`.
- M2 qualification staging now has an HTTPS-only Bridge entry point, bounded callback
  bodies, health/support endpoints, environment-backed private credential loading and
  stable QWC generation that refuses silent OwnerID/FileID changes. An all-zero private
  identity sentinel captures HCP evidence durably but returns no Bridge request until an
  operator confirms the synthetic company. Candidate export writes claims/evidence to a
  new private file and cannot edit the configured binding.
- Current local verification: **242 passed**; Ruff lint and format checks pass; wheel
  and source builds pass Twine metadata checks. The seven added tests are synthetic or
  local HTTP/TLS shape tests and do not count as QuickBooks integration evidence.
- [M2 staging CI run 33991930089](https://github.com/kaydtechsolutions/KaydBooks-Bridge/actions/runs/33991930089)
  passed lint/build and all eight Windows/Linux Python 3.10–3.13 jobs at commit
  `fc1d9814060af8aa6744d76f56dd8eda84000db3`. GitHub reported PR #2 mergeable; it
  remains open and was not merged.
- Documentation follow-up [CI run 33992022387](https://github.com/kaydtechsolutions/KaydBooks-Bridge/actions/runs/33992022387)
  also passed all nine checks at `194c584dedfa784007a797628922f5a99b0618db`.
- On the available Windows host, QuickBooks Enterprise 2024 R21 and Web Connector 34
  were found running in the current session. The current company-window title did not
  identify a sample/test company, so no connector was imported. A private localhost
  stage was provisioned outside Git with restricted ACLs, generated credentials, stable
  QWC IDs and a 30-day leaf certificate trusted for the current user. Windows HTTPS
  health/WSDL and manual authenticate/close callback probes passed. These probes did not
  involve QuickBooks and are not real integration evidence. Redacted version, hash,
  trust and readiness evidence is retained privately outside Git.
- The active staged certificate is explicitly `CA:FALSE`. Its predecessor was generated
  CA-capable and remains in the current-user trusted store because automatic approval
  review rejected deletion; deletion was not retried. A live Windows TLS handshake and
  chain check proved the endpoint presents the active certificate, not its predecessor.
  The superseded entry does not prevent qualification and is not an M2 prerequisite.
  Exact private fingerprint evidence remains available for a separate hygiene task.
- Exact-path private operator instructions plus local password-copy, clipboard-clear and
  candidate-export helpers are staged outside Git. The helpers contain no credentials.
- The first actual QWC import reached QuickBooks but failed before any service callback:
  Web Connector 34 logged QBWC1039 and QuickBooks reported that the application had not
  previously been authorized by the company Admin. No `CompanyRet` was received. The
  deployment profile now requires Intuit's `IsReadOnly=true` authorization preference
  and permits only optional unattended access. This QuickBooks request-processor control
  is independent of the Bridge's enforced query-only mode. Focused tests are synthetic;
  the failed authorization attempt is real integration evidence, not a successful test.
- A second real import against an operator-confirmed Intuit sample company proved that
  QuickBooks honored `IsReadOnly=true`: its permission summary allowed reading without
  personal data and only while QuickBooks was running. QBWC then failed before callbacks
  with SDK status 3263 because its own first-time registration attempted to add the
  required FileID data-extension definition under read-only authorization. A private,
  same-ID registration bootstrap is staged for this QBWC limitation. It must never run
  an update; the stable read-only QWC must replace it before any callback.
- A removal experiment established that QBWC's Remove action deletes the FileID value
  from the company, so it cannot mediate the transition back to read-only. No callback
  occurred. The corrected private v3 QWC uses the same IDs plus Intuit's documented
  `AppUniqueName` replacement path; the generator and profile now support and require
  this stable name.

## Blockers and next actions

- Corrected initial PATH observation: a local Hermes executable was found.
  Read-only version/help, CLI tool inventory and tool registration source inspection
  completed. Actual installed version/tool enablement evidence is stored privately
  outside Git. Bridge-specific permissions, profile, schemas and integration behavior
  remain unverified. No Hermes settings, schedules, recipients or boards were changed.
- R3 real qualification failed: matching AppUniqueName on bootstrap and final QWC
  did not prevent AppLock DataExtDefAdd during read-only import. SDK status 3263
  rejected the metadata write. The previously documented replacement workaround is
  withdrawn; do not repeat imports, rotate IDs, or remove registrations.
- Immediate work: establish supported QBWC metadata permission requirements or assess
  direct SDK read-only discovery separately. Keep Auto-Run off, passwords blank,
  bindings unconfirmed and posting disabled. No actual Bridge CompanyRet received.
- Real Hermes and QuickBooks integration tests: **none**. Production-enabled features:
  **none**. Real transaction/report/tax/inventory/landed-cost support is unverified.
- Planned: real qualification of the synthetic-tested QBWC discovery adapter;
  per-operation master/account/tax validation; native Hermes tools and document intake;
  schedules, notifications, memory, delegation, Kanban projections, reports, optional GUI flows.
- Draft revisions/cancellation, dependencies, operator correction of blocked jobs,
  policy-change audit, OS ACL provisioning and signed external audit checkpoints
  are not implemented. Held outcomes cannot be bypassed through the CLI.
- Next: after operator confirmation, capture and privately review CompanyRet identity,
  configure its digest, restart the staged service, run the read-only update twice to
  exercise recovery/duplicate behavior, and retain actual callback evidence outside Git.

## Direct SDK diagnostic follow-up

- Installed QBXMLRP2 COM runtime and Intuit interop assembly were discovered. SDK
  development-kit installation is not required for this diagnostic. Typed COM
  activation and PutIsReadOnly(true) passed on Windows PowerShell/.NET Framework,
  without opening a company session. PowerShell Core's legacy interop invocation
  was unsuitable; the successful Framework probe is the prerequisite evidence.
- A private diagnostic compiled against the actual installed interface. It requests
  read-only/no-personal-data authorization, checks the granted preferences using the
  session ticket, and can issue only fixed HostQuery/CompanyQuery requests. It saves
  dispatch intent and exact response outside Git and does not change company bindings.
- Direct SDK diagnostic launched; authorization/response outcome remains pending.
  This is not a QBWC callback test, a supported Bridge transport, or an M2 pass.
  There is no new operator QWC import requirement. All bootstrap/replacement retry
  instructions remain withdrawn. Live posting remains disabled.

## Verified direct SDK read-only discovery

- Real sample-company diagnostic: QuickBooks granted read-only access and excluded
  personal data; granted preferences were checked before any query. The initial
  qbXML 1.0 request failed with COM 0x80040400 (XML parse error). No CompanyRet was
  returned by that attempt. The exact cause within the 1.0 format remains unverified.
- A fresh read-only session confirmed request-processor support for qbXML 17.0.
  The fixed HostQuery/CompanyQuery batch using 17.0 succeeded: one successful HostRet
  and one successful CompanyRet; session closure completed. Exact XML, permissions,
  dispatch intent, supported versions, and closure evidence are retained privately.
- Private operator review contains only the three configured identity claims. All
  are present; candidate digest is calculated but configuration remains unconfirmed.
  Operator confirmation is required before binding. No identity details are in Git.
- This is actual direct SDK discovery evidence, not a mock and not QBWC qualification.
  QBWC callbacks, binding persistence and real restart qualification remain unfinished.
  Bridge posting remains disabled; no accounting write requests were sent.

## Operator-confirmed binding and restart verification

- Operator explicitly confirmed the three private company identity claims. The expected
  connector digest is now configured outside Git, with private confirmation evidence and
  company-scoped audit intent/completion events. Audit chain verification passed.
- Restarted the staged HTTPS Bridge process after the configuration update. Health
  reports read-only discovery and live_posting=false. A fresh actual direct SDK session
  returned HostRet/CompanyRet successfully, closed, and matched the persisted expected
  binding using the shared Bridge verifier loaded in a new process.
- Offline test with the captured real payload and a deliberately wrong expected digest
  was rejected by the shared verifier. No production company was opened. This is a
  real-payload offline test, not an actual mismatched-company QBWC callback test.
- Focused regression suite: 23 passed (synthetic discovery/deployment tests). No source
  changes in this milestone; changes are private configuration/evidence and documentation.
- Remaining M2 blocker: real QBWC read-only registration/callback qualification. The
  direct SDK diagnostic is not a production transport. Do not repeat withdrawn QWC
  bootstrap/replacement workarounds or enable posting. Next implementation work should
  bring any chosen direct SDK transport through the same authenticated, company-scoped
  durable lifecycle before claiming full M2 integration qualification.
