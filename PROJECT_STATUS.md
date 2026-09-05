# KaydBooks Bridge project status

Updated: 2026-09-05. Live posting: DISABLED. Real integration tests: NONE.

## Scope and branch

- Started from `main` at `4f86e44f890c88ee89f53daf0ec2b8c0f59730ff`.
- Inspected open draft PR #1; merged its exact head
  `3ce990cd3091dc5c52adfca793025ace30cfadfc` into `codex/foundation`.
  PR #1 is not assumed merged into main.
- No repository or parent AGENTS.md found. Upstream MIT attribution retained.
- Public repository; examples and tests must contain synthetic data only.

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

## In progress

Foundation: strict private configuration, company-scoped durable jobs, audit,
permissions, idempotency, simulation adapter, reconciliation and CLI.

## Blockers and next actions

- Hermes executable not found on PATH. Installed product/version, enabled tools,
  permissions and runtime endpoints are unverified. No Hermes access needed yet.
- No authorized QuickBooks test company/SDK connection. All wire capabilities and
  actual company binding remain unverified; no live worker will be exposed.
- Next: finish foundation tests and docs, commit milestone, open review PR;
  then perform capability-specific read-only deployment discovery before connecting.
