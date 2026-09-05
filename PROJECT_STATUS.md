# KaydBooks Bridge project status

Updated: 2026-09-05. Live posting: DISABLED. Real integration tests: NONE.

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
- Latest local suite: **214 passed**, including 42 Bridge synthetic tests and 172
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

## Blockers and next actions

- Corrected initial PATH observation: a local Hermes executable was found.
  Read-only version/help, CLI tool inventory and tool registration source inspection
  completed. Actual installed version/tool enablement evidence is stored privately
  outside Git. Bridge-specific permissions, profile, schemas and integration behavior
  remain unverified. No Hermes settings, schedules, recipients or boards were changed.
- No authorized QuickBooks test company/SDK connection. All wire capabilities and
  actual company binding remain unverified; no live worker will be exposed.
- Real Hermes and QuickBooks integration tests: **none**. Production-enabled features:
  **none**. Real transaction/report/tax/inventory/landed-cost support is unverified.
- Planned: durable QBWC callback adapter and real company binding; per-operation
  master/account/tax validation; native Hermes tools and document intake; schedules,
  notifications, memory, delegation, Kanban projections, reports, optional GUI flows.
- Draft revisions/cancellation, dependencies, operator correction of blocked jobs,
  policy-change audit, OS ACL provisioning and signed external audit checkpoints
  are not implemented. Held outcomes cannot be bypassed through the CLI.
- Next: resolve any PR CI/review findings, then M2 read-only discovery and durable
  callback design. Request an authorized
  QuickBooks test-company connection only when beginning the real discovery run.
