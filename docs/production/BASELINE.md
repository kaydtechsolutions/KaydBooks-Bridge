# Production baseline inventory

Inspected 2026-09-11 against main `3a7cca3d7859f316464f1eaf7cf4520f96c07dae`.
This inventory is not production qualification. Read the machine-readable
[source inventory](baseline-source.json) and [acceptance matrix](acceptance-matrix.json).

## Evidence checked

- Git fetch confirmed the current main SHA above. Work proceeds on
  `codex/production-readiness`, preserving unrelated user files.
- GitHub returned completed/success for CI run 34528448636 at qualified head
  `19f067c1e66d231a670c617fa23ec824d4eed0ee`, whose tree became this baseline.
  CI defines eight Linux/Windows Python 3.10–3.13 jobs plus lint/build, browser,
  offline intake and both installer jobs. Historical CI success is not a new
  production candidate test result.
- A fresh read-only deployment inspection found Bridge, remote MCP, Hermes,
  Caddy and Tailscale active. The configuration still uses simulation mode;
  the completed sample automation grant is disabled.
- Fresh content comparison of all 126 files in the deployed `kaydbooks_bridge`
  package against baseline source passed after normalizing CRLF to LF on both
  sides. This check includes Python, PowerShell, JavaScript and web assets; it
  does not claim installed dependency or operating-system identity.
- Private sample acceptance reports 39/39 passed. Its six transaction receipts
  each record one attempt, one handoff and independent readback with duplicate
  denial; the service master record is additional sample evidence. These existing
  records are historical and must not be recreated by this goal.
- Backup/restore, migration and installed alignment artifacts are retained privately.
  They describe their tested scope and are not evidence of real-company recovery.

## Code and product mapping

| Capability | Current implementation | Evidence and remaining production gap |
| --- | --- | --- |
| Production enrollment | `config.py` rejects every mode except simulation | No production policy or production permission exists; design and implement explicitly |
| Shared QBWC writes | `qbwc_contracts.py`, `qbwc_posting.py`, `qbwc.py`, `store.py` | Ten registered shared transaction contracts; sample gates and receipts, no live production qualification |
| Preparation | `service.py`, operation validators | Fourteen operation keys including master change, refund and credit application; variants remain incompletely qualified |
| Independent receipt checks | Operation evidence/receipt modules | Sample evidence varies by family; require every production variant and failure boundary |
| Service/inventory documents | Invoice, bill, sales receipt, credit modules | Basic and selected mixed/adjustment samples exist; currency/site/adjustment combinations still need work |
| Payments/refunds/applications | Payment/refund/application modules | Selected sample allocations verified; broader original discovery and variants need qualification |
| Journal/check/inventory transfer | `journal_entries.py`, `checks.py`, `inventory_transfers.py` | Basic sample effects verified; not account transfers |
| Account transfer | No validation or shared QBWC contract | Required by fourteen-entry scope; implement and qualify |
| Chart-of-accounts creation | `account_lookup.py`, `qbwc_accounts.py` read accounts only | Account creation remains required; lookup is not a write implementation |
| Master creation/update | `master_records.py`, `master_posting.py`, native master adapter | Selected records qualified; transport/type matrix must be completed for QBWC-first product |
| Batch entry | `tabular.py`, `hermes_batches.py`, `batch_preflight.py` | CSV/XLSX supporting contracts exist; full requested batch workflow remains incomplete |
| Reports | `native_reports.py`, `qbwc_reports.py` | 24 registered report names, including site inventory; registration alone is not evidence for all 24 |
| Cash flow | No report registration | Required by first-release scope; implement/schema-verify and reconcile |
| Access/approvals | `access.py`, `config.py`, `service.py`, `source_review.py` | Company isolation and revision contracts exist; defaults conflict across historical docs and need explicit production design |
| Browser/manual intake | `web_ui.py`, web assets | Shared forms and review exist; complete agreed variants and realistic operator acceptance |
| Documents/spreadsheets | `documents.py`, `extraction.py`, `tabular.py` | Historical printed-English and row-mapping checks; preserve source review and qualify complete production workflows |
| Scheduling | `dispatch.py`, `workflows.py` | Bounded sample schedules exist; reuse durable claims while designing production policy |
| Hermes WhatsApp | `hermes_channel.py`, `hermes_qbwc.py`, `hermes_setup.py` | Installed service active and historical connection evidence; new workflow/reconnect/identity tests still required |
| ChatGPT/MCP | `remote_mcp.py`, `hermes_tools.py` | Narrow authenticated tools exist; preparation is distinct from accounting dispatch |
| Composio | `composio.py`, deployment integration | Base integration retained; excluded Google connections stay disabled |
| Installation | `installer.py`, `deploy/`, `onboarding.py` | Linux/Windows CI and sample install evidence; broader clean install, migration and production onboarding remain |
| Backup/recovery | `operations.py`, `store.py`, operation recovery paths | Signed Bridge restore exists; separate QB backup, restored-job reconciliation and operational drill required |

The source inventory lists every top-level Bridge module, SHA, declared docstring,
60 decorated routes/tools, 14 validated operations, ten shared contracts, 24 report
names and all test files. AST presence is labelled separately from test or deployment
evidence. Non-shared operation paths must be inspected individually before any new
write authorization is wired in.

## Stale or conflicting statements to resolve

1. `CAPABILITIES.md` still describes Hermes adapters/reports as planned despite later
   implemented adapters and private sample evidence. Preserve its evidence discipline,
   but update these historical claims during capability/documentation work.
2. `capabilities.py` mixes deployment-unverified fields with eight global sample
   qualification labels. A production capability response must distinguish this
   installation's readiness from general historical product evidence.
3. `FIRST_RELEASE_SCOPE.md` says full permissions by default for new assigned users;
   `AUTO_INSTALL.md` and v0.2 acceptance describe read-only installation defaults.
   Document the different entry points and enforce deliberate production enrollment.
4. `DATA_ENTRY_READINESS.md` retains unfinished account transfer, account creation
   and batch entry requirements; these are explicit tasks, not exclusions.
5. Site inventory report registration exists, while broad site/currency report
   qualification remains unfinished. Cash flow is absent entirely.
6. `docs/v020-acceptance.json` describes an older qualification and CI run. It is
   retained history, not a substitute for the later private Test1 evidence or a
   production acceptance report. Do not rewrite historical receipts to look current.
7. Historical scope numbering contains 43 gates: 41 included and two tax exclusions.
   The new 36-step goal tracks work completion separately from those gate counts.

## Next actions

Use every included historical gate and all fourteen entry types in the acceptance
matrix. Define the environment and budget assumptions, then design an explicit
production authorization boundary covering every write path. No deployment setting
was changed and no accounting job was submitted by this inventory.
