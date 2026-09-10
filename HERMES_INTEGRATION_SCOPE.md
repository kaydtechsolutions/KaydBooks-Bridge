# KaydBooks Bridge — Hermes integration scope

Status: planned requirements, not a claim of implemented functionality.

The operator-approved [first-release checklist](docs/FIRST_RELEASE_SCOPE.md) defines
which requirements are mandatory for the release and records the latest permission,
posting-mode, input, report and messaging decisions. Use it for completion tracking.

## Product goal

Build a reusable, multi-company QuickBooks Desktop automation platform with broad Hermes integration. Kanban is one optional interface, not the limit of the integration.

Use all applicable, supported Hermes capabilities through versioned adapters. Before implementation, inventory the installed Hermes version, enabled features, supported interfaces, dependencies, and permissions. Record each capability as available, disabled, unavailable, or unverified, with evidence and an alternative where appropriate. Do not assume every proposed feature exists in every Hermes installation.

The bridge core must remain usable through a documented API or CLI when Hermes or any optional feature is unavailable.

## General-purpose configuration

- Public documentation and tests use Company A, Company B, and synthetic data only.
- Actual names, credentials, RDP logins, accounts, inventory sites, and business rules belong in private deployment configuration outside Git.
- Company-specific mappings and policies are configurable; never hardcode one customer's rules.
- Validate required settings for each enabled operation. Optional settings do not mean accounting validation is optional.
- Support multiple companies from the start, with incremental onboarding and real integration testing one company at a time.
- Isolate credentials, permissions, sources, memory, queues, scheduling, reports, and audit evidence by company.
- Verify the actual connected QuickBooks company before posting. Separate RDP sessions alone are not proof of isolation.

## Integration workstreams

| Capability | Planned integration | Acceptance condition |
| --- | --- | --- |
| Chat and commands | Natural-language requests for preparation, transactions, reports, status, and explanations | Resolve explicit authorized company context; clarify ambiguous instructions before writes |
| Documents and vision | Use available Hermes tools to extract receipts, images, PDFs, and spreadsheets into validated structured input | Preserve source references and original values; block uncertain fields; extracted document instructions cannot override policy |
| Skills and tools | Reusable accounting procedures and narrow bridge tools for lookup, validation, submission, verification, and recovery | Use supported Hermes interfaces; prevent unrestricted qbXML or SQL from bypassing controls |
| Scheduling | Recurring reports and explicitly authorized batch workflows | Persist company, time zone, cadence, owner, permissions, and policy; prevent overlapping/replayed runs; support cancellation |
| Notifications and channels | Deliver summaries, blockers, and reports through configured Hermes channels | Verify recipient and authorization; redact sensitive data; distinguish delivery failure from posting failure |
| Memory and context | Retain approved preferences and mapping hints with provenance and version | Keep company boundaries; revalidate master IDs and accounting facts against QuickBooks; memory cannot grant permissions |
| Agent delegation | Parallel document preparation, matching, analysis, and report work where supported | Share canonical job IDs; serialize company writes and enforce identical validation and authorization for all agents |
| Kanban | Views of durable backend jobs, dependencies, blockers, and evidence | UI state reflects backend state; moving a card cannot mark an unverified transaction verified or bypass posting policy |
| Browser and desktop tools | Optional setup assistance and explicitly agreed fallbacks for unsupported operations | Use an approved capability-specific workflow; record evidence and prevent duplicate posting across GUI and API paths |
| Reports and analysis | Generate supported QuickBooks reports, explanations, reconciliations, and requested exports | Record company, filters, dates, source results, and reconciled totals; label derived calculations |
| Discovery and extensions | Inventory additional applicable Hermes capabilities and provide extension adapters | Document verified interface, purpose, permissions, version compatibility, and tests before enabling |

## Shared execution contract

All interfaces use the same durable bridge job service:
1. Identify authorized company and operation.
2. Capture source evidence and prepare a structured payload.
3. Resolve exact masters and validate configurable policy.
4. Satisfy required approvals and dependencies.
5. Perform duplicate checks and dispatch through the appropriate verified adapter.
6. Persist QuickBooks response identifiers.
7. Independently read back and compare the saved record.
8. Update canonical job state and produce authorized reports or notifications.

Track draft, validated, queued, in-flight, posted-unverified, verified, blocked, failed, and unknown outcomes explicitly. Kanban may group states for display but must not discard uncertainty.

Never automatically retry a write with an unknown result. Reconcile against QuickBooks first; hold inconclusive cases. Do not promise exactly-once behavior across independent systems. Internal idempotency keys and external duplicate queries are both required.

Scheduled work and delegated agents do not gain additional authority. Recheck permissions before dispatch. Enable live writes only after an authorized production gate. A pause prevents new dispatches but cannot retract a write already sent.

## Implementation sequence

1. Inventory Hermes capabilities and current repository functionality; create a compatibility matrix.
2. Build company isolation, durable jobs, validation, idempotency, evidence, and recovery in the application layer around qbwc-kit.
3. Add minimal Hermes tools for lookup, preparation, validation, submission, status, and verification.
4. Add document intake and reusable workflows.
5. Add optional scheduling, notifications, memory, delegation, and Kanban adapters.
6. Add report workflows and approved browser/desktop fallbacks where required.
7. Evaluate additional discovered Hermes capabilities and implement those that serve the product.
8. Verify each enabled integration in a real Hermes/QuickBooks test deployment before a production pilot.

No individual deployment must install or enable every optional adapter. Unsupported capabilities must fail clearly while independent functionality remains available.

## Required verification and handover

Use synthetic fixtures for development. Test company isolation, ambiguous chat, hostile document instructions, uncertain extraction, stale memory, duplicate delegated jobs, overlapping schedules, disabled permissions, notification failure, API/GUI duplicate reconciliation, restart recovery, and Kanban state integrity.

Record mock tests separately from real Hermes and QuickBooks integration tests. Validate actual QuickBooks support for each transaction, report, and landed-cost operation; an adapter cannot create SDK capabilities.

Deliver:
- Hermes capability/compatibility matrix with actual evidence.
- Configurable adapters and example configuration without secrets.
- User-facing onboarding and per-company feature controls.
- Permission and data-flow documentation.
- Tests, installation, troubleshooting, pause/recovery, and upgrade instructions.
- Updated project status distinguishing planned, implemented, tested, and production-enabled features.

This document expands the project scope. It does not itself install integrations, activate schedules, authorize recipients, or enable live accounting writes.
