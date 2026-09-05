# KaydBooks Bridge project status

Updated: 2026-09-06. Live posting: DISABLED. Real QuickBooks integration tests: NONE.

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
  review rejected deletion. Exact thumbprint cleanup instructions are stored privately;
  a Windows operator must remove only that superseded certificate before connector use.

## Blockers and next actions

- Corrected initial PATH observation: a local Hermes executable was found.
  Read-only version/help, CLI tool inventory and tool registration source inspection
  completed. Actual installed version/tool enablement evidence is stored privately
  outside Git. Bridge-specific permissions, profile, schemas and integration behavior
  remain unverified. No Hermes settings, schedules, recipients or boards were changed.
- The sole remaining immediate M2 requirement is an operator-confirmed dedicated
  synthetic QuickBooks company opened as QuickBooks Admin. The staged service/QWC are
  ready, but the current open company is not identifiable as synthetic and must not be
  used. The same Windows operator must remove the precisely identified superseded local
  certificate. Follow `docs/M2_QUALIFICATION.md` to import the QWC and initiate one
  fail-closed candidate-capture update. Real callback behavior and binding are unverified.
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
