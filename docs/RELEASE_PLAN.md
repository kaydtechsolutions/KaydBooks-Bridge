# Incremental release plan

The operator requested small versioned releases so usable work can be tested
without waiting for the entire roadmap. The active KB v0.1.0 target is now
**upload to Hermes -> exact operator confirmation -> Bridge/Web Connector entry
-> mini result report in the operator's Hermes WhatsApp chat**, using the eight
selected entry types. The [workflow specification and milestone scorecard](HERMES_DATA_ENTRY_PILOT.md)
take priority over broader roadmap work. Current verified acceptance: **5/15 (33%)**.
No release has been published by creating this plan.

| Version | Proposed scope |
| --- | --- |
| KB v0.1.0 | Hermes upload, human confirmation, Web Connector entry and operator WhatsApp result; sales receipt, invoice, credit memo, customer payment, bill, journal, inventory transfer and check |
| KB v0.1.1, v0.1.2 | Fixes within the pilot's supported scope |
| KB v0.2.0 and later 0.x versions | Additional data-entry types, delivered in independently verified groups |
| KB v1.0.0 | Agreed daily workflow qualified: selected data entry, customer balances/statements, daily reports and authorized Hermes/WhatsApp delivery |
| KB v1.0.1 and later patches | Compatible bug fixes |
| KB v1.1.0 and later minor versions | Compatible new features |

Use [Semantic Versioning](https://semver.org/): major versions for incompatible
public-contract changes, minor versions for compatible features, patches for fixes.
Version zero identifies initial development. Every released artifact must retain
its original contents; corrected artifacts receive a new version. Python package
version, application display, release tag, artifact hashes and release notes must
identify the same candidate. The current package remains `0.1.0.dev1`; no version
bump or tag is implied by this proposal.

## Earliest pilot boundary

Initial qualification uses the explicitly confirmed sample company, operator-reviewed
entry through Hermes, non-tax USD transactions and the documented simple-inventory settings.
The installation design supports separately configured company files; each needs its
own identity, mappings, authorization and qualification. See the
[multi-company installation guide](INSTALL_HERMES_DATA_ENTRY.md).
Each of the eight paths must have an installed sample receipt, exact accounting or
stock-effect evidence, duplicate refusal and a valid audit. Existing production
posting restrictions remain. Tax, unqualified variants, real customer messaging,
refund/credit application migration and the remaining broader data-entry types
are outside this pilot scope, and stay on the roadmap.

Before a pilot is called ready, record:

- Exact supported fields and limitations for each included entry type.
- Installed tests of included workflows, with recoverable held outcomes clearly
  separated from confirmed failures and no uncertain write resubmission.
- The tested package/commit, clean installation or upgrade evidence, private setup,
  startup/shutdown instructions and Web Connector registration instructions.
- A current Bridge backup/isolated restore and guidance distinguishing it from a
  QuickBooks company backup.
- A short operator walkthrough, release notes, known issues and support/recovery
  steps. Actual bill-interruption qualification remains an unresolved known issue;
  it cannot be described as passed.

The existing 41-gate M3–M7 checklist remains the broad roadmap. Its 13 completed
gates do not constitute the pilot's readiness percentage. Track pilot requirements
separately; narrowing the release does not mark broader
gates complete. The fourteen requested data-entry types remain tracked in
[data-entry readiness](DATA_ENTRY_READINESS.md).

Publishing a release, merging the PR and using production accounting data remain
separate actions requiring the operator's explicit authorization. Development,
local candidate builds and authorized sample qualification may continue.


## Selected entry-type progress

| Entry type | Current state |
| --- | --- |
| Invoice | Basic installed Web Connector sample qualification passed |
| Credit memo | Basic installed Web Connector sample qualification passed |
| Customer payment | Basic installed Web Connector sample qualification passed |
| Bill | Basic installed Web Connector sample qualification passed |
| Sales receipt | Basic installed Web Connector sample qualification passed |
| Journal | Implemented/tested; saved sample memo repair and reconciliation pending |
| Inventory transfer | Implemented/tested; live source/destination verification pending |
| Check | Implemented/tested; installed master check passed, write pending |

Five of eight selected entry types (62.5%) have a basic installed sample qualification.
This measures entry types only, not feature variants, remaining pilot operational
checks or total release readiness. Supplier payments and bill credits remain
implemented and sample-qualified additional features. Prioritize live qualification of the three remaining
selected types before broader roadmap expansion.
