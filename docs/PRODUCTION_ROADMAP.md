# KaydBooks production readiness goal

Created 2026-09-11. This is an execution plan, not a production-readiness claim.

## Objective and scope

Build, test, document and qualify a production-capable KaydBooks Bridge from the
GitHub repository, then conduct an explicitly authorized limited real-company
pilot and operational handover. Preserve the completed v0.2.0 sample evidence.
The known qualified baseline is main commit
`3a7cca3d7859f316464f1eaf7cf4520f96c07dae` (PR #12); confirm current main before
starting implementation. Do not replay the six completed sample transactions or
reset their exhausted grants.

Reconcile this plan with FIRST_RELEASE_SCOPE.md, DATA_ENTRY_READINESS.md,
RELEASE_PLAN.md and the existing operation contracts. Retain previously agreed
features: invoices, bills, sales receipts, customer/supplier payments, journals,
credits/refunds, checks, inventory transfers, supported master changes, reports,
manual/upload/spreadsheet/chat inputs, multi-company, supported multi-currency,
and manual/scheduled/bounded automatic workflows. Inventory each variant rather
than assuming one passing example qualifies the family. Tax remains excluded by
the existing scope; unsupported combinations must be explicitly denied. Any
proposed release narrowing or scope expansion needs an explicit user decision.

Linux must work on a supported standalone server as well as Proxmox LXC/VM.
Windows hosts QuickBooks Desktop and Web Connector. Hermes uses WhatsApp only;
retain ChatGPT and Composio base integration. Preserve Claude/Gemini compatibility
contracts without assuming accounts are available. Google Drive, Sheets and Docs
connections remain excluded by the user's instruction. Recording accounting
payments never authorizes bank transfers, card charges or supplier messaging.

## Authority and execution boundaries

Proceed autonomously with repository inspection, isolated implementation, tests,
synthetic fixtures, documentation, fixes and reviewable PRs. Preserve user edits.
Keep all app core changes, tests and documentation in GitHub with passing checks;
merge only qualifying changes under the user's existing repository authorization.
Do not publish a release or enable live posting merely because code was merged.

Development authority is not a blanket authorization to post new transactions.
Existing sample approvals and unattended grants were bounded and exhausted.
Prepare new exact sample test batches and obtain any missing authorization before
posting. Prepare a concrete review package before requesting real-company access,
use of a restored company backup, or pilot activation. Keep real-company writes
disabled until the user authorizes the exact company, operations, actors, limits
and pilot window. Do not convert a sample company into production by relabeling
its configuration. Never weaken binding, duplicate protection or audit controls.

An unknown write outcome requires read-only reconciliation. Do not resend or
create a replacement reference to get around a held outcome. Restoring Bridge
state does not reverse QuickBooks accounting effects; restoration must reconcile
with current QuickBooks state before any dispatch can resume.

## Milestones, steps, tasks and exit evidence

Each step is complete only when its tasks and stated exit evidence are satisfied.
Dependencies are milestone order unless the next paragraph explicitly permits
independent work. No code or test result alone counts as a live pilot approval.

### M1 — Baseline and measurable scope (steps 01–03)

- **01 — Baseline inventory.** Inspect current main, deployed versions, existing
  CI and private qualification evidence; map every exposed capability to its
  implementation and evidence; record stale claims and remaining gaps.
  **Exit:** commit-pinned inventory distinguishing synthetic, sample and live evidence.
- **02 — Release acceptance matrix.** Reconcile all previous scope documents;
  enumerate operation/variant, currency, inventory, input and dispatch combinations;
  define pass/fail requirements and explicit exclusions for each.
  **Exit:** versioned matrix with no silently dropped agreed feature.
- **03 — Environments and budgets.** Define isolated development, sample,
  restored-company staging and live environments; document support versions,
  test data, bounded transaction budgets and required access; propose measured
  performance, recovery and pilot acceptance targets.
  **Exit:** environment plan and all unresolved external dependencies recorded.

### M2 — Production architecture and security (steps 04–06)

- **04 — Production authorization design.** Specify explicit environment/company
  enrollment, final write authorization and revocation; separate production from
  sample gates and reject accidental promotion; record threat model and trust boundaries.
  **Exit:** design maps every write entry point to an enforceable authorization check.
- **05 — Identity, roles and secrets.** Implement company-scoped least privilege,
  approver/preparer separation and credential rotation/revocation; secure service
  identities and secret storage; audit administration and cross-company denial.
  **Exit:** negative tests prove unauthorized actors cannot gain or retain write access.
- **06 — External input and endpoint security.** Harden authentication, sessions,
  CSRF where applicable, uploads, XML parsing, path handling and MCP boundaries;
  test prompt-injected documents/chats and hostile requests; redact logs and scan dependencies.
  **Exit:** reproducible security checks pass; no unresolved critical/high release findings.

### M3 — Durable production posting (steps 07–09)

- **07 — Production lifecycle.** Implement reviewed opt-in policy and durable
  prepare/validate/approve/submit/dispatch states; keep existing installs disabled;
  recheck identity, payload fingerprint and policy immediately before the write.
  **Exit:** tests prove restart, edits and stale approval cannot authorize a write.
- **08 — Concurrency and duplicates.** Persist idempotency and transaction lineage;
  serialize conflicting workers/QuickBooks sessions; test duplicate submissions,
  response loss and crashes at each dispatch boundary.
  **Exit:** uncertain outcomes are held and reconciled without blind retries.
- **09 — Limits and emergency stop.** Implement per-company/operation amount and
  count limits, approval expiry and cancellation; enforce stop/revocation at final
  authorization; expose operator-visible hold reasons and controlled recovery.
  **Exit:** queued work stops after revocation; in-flight work is accounted for honestly.

### M4 — Accounting correctness (steps 10–12)

- **10 — Documents and masters.** Qualify invoice, bill, sales receipt and permitted
  customer/vendor/item changes across the agreed matrix; verify references, terms,
  decimal rounding, non-tax settings, currencies and supported inventory variants.
  Implement and qualify new chart-of-accounts account creation from the fourteen-entry scope.
  **Exit:** independent saved-record and accounting-effect evidence for every supported variant.
- **11 — Payments and adjustments.** Qualify customer/supplier payments, partial
  allocations, credits/refunds, deposits, discounts and charges; verify fresh balances
  and source accounts; hold mismatched or unsupported allocation combinations.
  **Exit:** saved allocations and receivable/payable/bank effects reconcile exactly.
- **12 — Journals, stock and reports.** Qualify journals, checks and transfers;
  reconcile balanced entries, stock/site/cost effects and company identity; verify
  agreed reports, date ranges, basis, statements and supported currency behavior.
  Include account transfers and cash-flow reports; existing inventory transfers are
  not a substitute for account transfers.
  **Exit:** independent QuickBooks reports agree with expected ledger and stock changes.

### M5 — Operator experience (steps 13–15)

- **13 — Company onboarding.** Provide guided identity discovery and binding,
  QWC export/import, mappings and credentials; explain initial discovery before
  binding confirmation; expose prerequisite and compatibility failures clearly.
  **Exit:** a first-time operator completes onboarding without generated ad hoc scripts.
- **14 — Entry and review.** Complete manual/upload/CSV/spreadsheet/chat paths;
  show source evidence, confidence and corrections; invalidate approval on edits
  and provide exact reviewable previews and accessible error handling.
  Include the requested batch-entry workflow with stable row identities, per-row
  errors, partial completion and repeat-import protection.
  **Exit:** representative operator workflows pass browser and integration tests.
- **15 — Dispatch and recovery UI.** Show queued, held, unknown and verified states;
  provide safe reconcile, pause and resume controls; qualify scheduled and bounded
  automatic modes with explicit rules and no implicit approval grants.
  **Exit:** restart/cancellation tests and operator walkthrough show no duplicate writes.

### M6 — Channels and AI integrations (steps 16–18)

- **16 — Hermes WhatsApp.** Bind allowed senders to scoped principals; preserve
  company context and reviewed drafts; test reconnect, replay, attachments and
  authorized result delivery with deduplicated notification identifiers.
  **Exit:** WhatsApp-only end-to-end evidence, including wrong-user/company denials.
- **17 — ChatGPT and MCP.** Verify enrollment, authenticated tunnel lifecycle,
  tool contracts and revocation; preserve explicit workflow permissions and approval
  barriers; test supported client compatibility without claiming unavailable account tests.
  **Exit:** real connected ChatGPT workflow evidence plus supported MCP contract tests.
- **18 — Composio and isolation.** Verify base connection, secret rotation and
  least-privilege tool exposure; enforce the excluded Google integrations; test
  external outages and cross-principal denial without accounting retries.
  **Exit:** base integration works and cannot widen accounting permissions.

### M7 — Installation, upgrades and delivery (steps 19–21)

- **19 — Linux installer.** Qualify supported fresh standalone Linux and LXC/VM
  installs; detect/install prerequisites and configure HTTPS/DNS/services; ensure
  reruns preserve settings and failed installs leave a diagnosable state.
  **Exit:** reproducible clean-install and repair results for the support matrix.
- **20 — Windows setup.** Qualify supported QuickBooks/Web Connector versions,
  bitness and runtime combinations; guide local consent and sample binding; test
  passwordless access if configured without making SSH mandatory for ordinary use.
  **Exit:** clean-PC setup works and rejects unsupported environments clearly.
- **21 — Upgrade and rollback.** Version configuration/database migrations and
  package artifacts; verify upgrade from supported versions and partial failures;
  preserve jobs, audit and identity through rollback without replaying accounting writes.
  **Exit:** installed upgrade/rollback evidence and matching artifact/commit hashes.

### M8 — Reliability, recovery and operations (steps 22–24)

- **22 — Backup and restore.** Protect Bridge configuration, evidence and secrets;
  separately require a valid QuickBooks backup; test isolated restoration and
  reconciliation before worker activation; measure recovery targets defined in step 03.
  **Exit:** restoration drill meets targets and produces no unsolicited writes.
- **23 — Fault and load testing.** Exercise network loss, QB closure, service crashes,
  machine restart, disk full, locks, clock drift and sustained queue load; establish
  supported capacity; preserve durable states under every ambiguous failure.
  **Exit:** measured results meet targets with no silent loss or duplicate accounting.
- **24 — Monitoring and support.** Add structured redacted logs, useful health checks,
  queue/unknown-outcome metrics, alert deduplication and incident runbooks; document
  retention, access controls and support ownership.
  **Exit:** induced failures trigger actionable alerts and documented recovery succeeds.

### M9 — Full release verification (steps 25–27)

- **25 — Automated regression.** Run appropriate unit, integration, security,
  browser, build and installed tests across supported platforms; close every
  failure or document an explicitly accepted non-release limitation.
  **Exit:** exact candidate passes all required CI; no silent skipped acceptance gates.
- **26 — Installed sample qualification.** Prepare new uniquely referenced,
  bounded test batches for approval; execute only authorized batches; collect
  independent readback, balance effects, duplicate blocking and audit evidence.
  **Exit:** release matrix has installed evidence; old completed tests are never resent.
- **27 — Release review.** Review full diff and traceability, scan secrets and
  dependencies, inspect artifacts and licenses, and verify documentation matches
  actual capability; record remaining risks with explicit disposition.
  **Exit:** candidate is technically ready for authorized real-company staging.

### M10 — Real-company staging (steps 28–30)

- **28 — Staging approval and isolation.** Present the exact backup-copy access
  plan, privacy boundaries, test amounts and isolation controls; obtain required
  owner authorization and backup; prevent staging from targeting the live QBW file.
  **Exit:** separately bound restored company with documented authorization.
- **29 — Representative workflows.** Verify real chart, master mappings and opening
  balances; run approved representative staging transactions and reports; reconcile
  results with the operator/accounting reviewer and fix discovered gaps.
  **Exit:** business acceptance evidence against the restored company, not just synthetic data.
- **30 — Recovery rehearsal.** Rehearse stop, failure recovery and upgrade on staging;
  measure recovery and capacity targets; prepare the final pilot change plan,
  exact limits, rollback/reconciliation procedure and unresolved decisions.
  **Exit:** reviewable pilot package, with all pre-pilot technical gates passed.

### M11 — Authorized limited live pilot (steps 31–33)

- **31 — Explicit live authorization.** Obtain user approval for exact company,
  operations, users, amount/count ceilings, window and first transactions; verify
  current backups and bind the production identity; enable only approved scope.
  **Exit:** recorded activation approval and effective bounded policy.
- **32 — Pilot execution.** Execute only reviewed authorized real transactions;
  verify independent readback, totals and balances; monitor errors and stop on
  mismatches or uncertainty without blind resend.
  **Exit:** all authorized pilot outcomes reconciled, with no unexplained accounting changes.
- **33 — Pilot acceptance.** Observe the agreed pilot window, reconcile reports and
  audit, obtain operator acceptance and record findings; disable temporary grants;
  request a separate expansion decision if broader operation is desired.
  **Exit:** accepted pilot results and explicit steady-state operating scope.

### M12 — Release and handover (steps 34–36)

- **34 — GitHub and release candidate.** Ensure every core fix, test and public doc
  is reviewed and merged with required checks; pin the exact tested artifact,
  dependency manifest and release notes; obtain any missing publication authorization.
  **Exit:** published release when authorized, consistent with the qualified commit.
- **35 — Operator handover.** Deliver complete Linux/Windows installation, setup,
  access, approvals, daily operation, backup and troubleshooting guides; demonstrate
  normal tasks and incident recovery without AI-generated one-off commands.
  **Exit:** operator can operate and recover the app from the documented workflow.
- **36 — Final acceptance.** Audit every matrix entry, step, issue and authorization;
  confirm production scope matches approval, remove temporary grants and test helpers;
  archive private evidence and delete this goal's continuation monitor.
  **Exit:** 36/36 evidenced steps, no unresolved required tasks, and user-facing final report.

## Dependencies and progress

Milestones proceed in order, but independent security, documentation, installer,
UI and synthetic-test tasks can continue when credentials or approvals block a
different path. Choose the earliest unblocked task. Never simulate a human approval
to make the percentage increase. Do not create sub-agents unless explicitly asked.

The public roadmap is the scope contract. Private execution state lives in
`private/production-goal/checkpoint.json`, with append-only history in
`private/production-goal/events.jsonl`. Preserve evidence privately and publish only
redacted summaries. Do not commit tokens, company records, QBW files or credentials.

Each step has a stable ID 01–36, status, remaining tasks, evidence paths, commit,
dependencies and blockers. Status is pending, in_progress, blocked or complete.
Report after every completed step:

`Step NN finished — C/36 complete — P%`

P is 100*C/36, rounded to one decimal. This is checklist completion, not elapsed
time or a standalone production certification. Expand tasks beneath stable step
IDs; changing milestone scope or denominator must be explained to the user.

## Resuming after interruption

1. Read this roadmap, private checkpoint, event history and native goal state.
2. Inspect branch, dirty files, processes, CI, deployed revisions and the latest
   evidence relevant to the next action. Preserve uncommitted user work.
3. Reconcile any in-flight or unknown accounting action before considering writes.
   Never rerun a completed test batch, reset a used grant or assume a crash rolled back QB.
4. Resume the earliest authorized unblocked task. Retry safe reads and deterministic
   tests where useful; do not repeat unchanged failed actions indefinitely.
5. Save checkpoint atomically after completed work and before ending a turn. Record
   the blocker, exact next action, required input and the last verified result.
6. Send a concise progress update on a completed step or meaningful blocker. The
   user's authorized WhatsApp recipient may receive a deduplicated stop/resume
   notice only if the existing channel is available; never claim delivery without a receipt.

Resume message for this Codex task:

> Resume the KB production readiness goal from docs/PRODUCTION_ROADMAP.md and
> private/production-goal/checkpoint.json. Continue the next authorized unblocked
> task, preserve completed evidence, and report completed steps out of 36.

That text is a task instruction, not a shell command. WhatsApp-to-Codex goal resume
is not assumed to exist; it must be implemented and verified before advertising it.

A recurring follow-up may continue authorized work when this task is idle. It must
respect user pause/cancellation and required approvals. It cannot force the native
goal's paused state to resume through a status-update tool. A user-controlled pause
may require the app's Resume control. Power loss, offline hosts, exhausted usage,
missing access and unavailable services cannot be bypassed by a goal or schedule.
Keep the local computer on and the app running for scheduled local work, as described
in [official scheduling documentation](https://learn.chatgpt.com/docs/automations?surface=app).

Completion means all 36 steps truly pass. Until then never mark the goal complete
because the code alone is finished, the budget is low or external input is pending.
