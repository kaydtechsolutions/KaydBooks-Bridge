# Capability matrix and evidence rules

Reviewed public references: 2026-09-05. Installed/deployment details belong in a
private discovery directory outside Git. Public descriptions below do not authorize
or prove any integration in a customer's installation.

Availability values: `available`, `disabled`, `unavailable`, `unverified`.
Separately record implementation and evidence level: planned, source-inspected,
synthetic-tested, real integration-tested, production-enabled. A source registration
or successful `--help` is not an integration test.

## Hermes

The candidate product is Nous Research Hermes Agent. Local read-only discovery found
an executable, recorded its version privately, inspected command help and registration
code, and requested the CLI tool inventory. Configuration, accounts and secrets are
not copied into this repository. Runtime credentials and a Bridge-specific profile
are not yet verified; no bridge adapter is enabled in Hermes.

The [official tools reference](https://hermes-agent.nousresearch.com/docs/reference/tools-reference/)
documents skills, files/vision, memory, delegation, cron, messaging, browser and
conditional Kanban tools. It also documents MCP extension tools and states that
availability varies by platform, credentials and enabled toolsets. Installed tool
schemas must be compared with an adapter's pinned contract before use.

| Capability | Evidence to collect privately | Bridge state / current alternative |
| --- | --- | --- |
| Chat | Product/version, active platform and clarification behavior | Planned; explicit CLI company |
| Documents/vision | Enabled file/vision tools, model/dependencies, real extraction fixtures | Planned; structured synthetic intake |
| Skills/tools | Skills and MCP registration, tool schemas and selected permissions | Planned; authenticated CLI/service |
| Scheduling | `cronjob` schema, scheduler runtime, owner/timezone and available actions | Planned; manual queued simulation |
| Notifications | Messaging schema, authorized channels/recipient IDs, delivery receipts | Planned; local status/audit |
| Memory | Provider, storage, provenance, profile/tenant separation and grants | Planned; no memory-derived authority |
| Delegation | `delegate_task` limits, child tool grants and identity propagation | Planned; duplicate submissions tested at service boundary |
| Kanban | Conditional tool registration, board scope, worker permissions | Planned; canonical job status list |
| Reports | No built-in QuickBooks reporting interface established | Planned; no accounting reports promised |
| Browser/desktop | Driver/browser availability and capability-specific workflow approval | Disabled in Bridge; no GUI posting |

Every Hermes deployment capability remains `unverified` for Bridge until its private
evidence proves version, enablement, dependencies and scoped permissions. A toolset
may be enabled for general use without being authorized to access accounting data.

## QuickBooks Desktop and inherited library

The [Intuit SDK Programmer's Guide](https://static.developer.intuit.com/resources/QBSDK_ProGuide.pdf)
documents HostQuery product/version/country and supported qbXML versions, CompanyQuery
company information, distinct transaction/list queries and report requests. Company
names and QWC OwnerID/FileID alone are not proof of the connected company identity.
The exact company-file/session binding and independently queried evidence must be
designed and qualified against an authorized test installation.

| Area | Evidence / limitation | Bridge state |
| --- | --- | --- |
| SOAP callbacks, sessions, qbXML parser/builders | Inherited tests, including fakes; in-memory sessions cannot be the durable queue | Transport unit/synthetic tested |
| Host/Company/Preferences discovery | Requires actual QBWC/SDK session, product, country, version negotiation and company binding | Planned; connection unverified |
| Invoice creation/read-back | Narrow synthetic amount-only invoice; no SDK request emitted by Bridge | Synthetic-tested; real support unverified |
| Other transactions | Generic builder names do not establish schema or edition support | Planned; unavailable through Bridge |
| Reports | Validate each report, basis, filters, dates, permissions and totals separately | Planned; unverified |
| Inventory sites, taxes, multicurrency, landed cost | Edition/country/version and operation-specific support must be established; no blanket qbXML assumption | Planned; unverified |
| GUI fallback | No workflow approved or implemented; cannot bypass core duplicate checks | Disabled |

Intuit's API-reference pages returned a client-side loading page during inspection;
they were not treated as successfully read schemas. The simulation's identity string,
`sim-` transaction IDs and normalized JSON are internal test artifacts, not SDK fields.
No production support claims should be inferred from them.

Inherited status-code aliases also need source verification: do not interpret the
library's labels for 3100/3260 as authoritative operation-support or permission codes.
Bridge currently does not classify live responses using these aliases. M2 must audit
these meanings against the actual Intuit reference and test returned severity/status.

## Private inventory record for M2

For each company, capture operator/time, product and exact version/commit, interface
schema/version, enabled features, dependencies, permissions, evidence locator/digest,
status and alternative. QuickBooks adds edition/year/release/country, QBWC and SDK
versions, supported qbXML versions, company binding, master/query/transaction/report
allowlists, observed statuses and synthetic round-trip evidence. Avoid dumping whole
configuration, environment variables, logs or company response bodies into Git or PRs.
