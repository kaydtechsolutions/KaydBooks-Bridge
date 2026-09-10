# Production authorization design

Status: implementation design, not an enabled feature. Baseline:
`3a7cca3d7859f316464f1eaf7cf4520f96c07dae`. Implements roadmap step 04; subsequent
steps must prove the behavior before any activation.

## Trust boundaries

The accounting database and connector identity are private server configuration.
Documents, spreadsheet cells, chat messages, model output, Composio results and
client request bodies are untrusted proposed data. None may supply an effective
principal, token, company binding, approval, production enrollment or raw qbXML.
Authentication supplies the principal; explicit grants select accessible companies.

Treat these as separate decisions:

1. An installation and exact company are qualified for a particular operation.
2. The operator enables a reviewed, bounded production enrollment for that company.
3. The authenticated actor has permission to prepare, approve or dispatch that job.
4. The exact immutable job has a valid approval under the current company policy.
5. Current masters, binding, limits, source review and durable state permit this write.

All five must hold at the final write boundary. A healthy endpoint, approved document,
administrator role, owner override or enabled schedule cannot substitute for enrollment.

## Configuration and migration contract

Introduce a versioned explicit production policy rather than accepting arbitrary
values of the existing global `mode`. Existing schema-1 simulation configurations
retain their exact sample behavior, hashes and exhausted quotas. Missing production
policy means disabled. Sample gates never authorize a production operation.

The proposed production schema has strict keys and types, with per-company:

- Environment classification and immutable enrollment ID/revision.
- Exact connector ID and confirmed company identity digest; observed Windows file
  identity/path/session evidence is separately verified where supported. A name or
  QWC ID alone cannot prove the file. Do not silently accept a different file with
  similar company fields.
- Explicit enabled state, activation/not-before and expiry times.
- Allowed operation/variant capabilities supported by this qualified release.
- Authorized preparers, approvers and dispatch actors, restricted by their actual
  company grants; enrollment may restrict grants, never create them implicitly.
- Count and decimal amount ceilings per operation and enrollment window, currency
  and maximum individual transaction exposure. Masters/transfers need explicit
  count/quantity rules where a monetary amount is not meaningful.
- Reviewed policy and release/capability fingerprints, source namespace restrictions,
  approval policy, change reason and authorizing operator evidence reference.

Do not put actual human authorization evidence or secrets in Git. A string saying
"approved" in a client payload is not evidence. Produce an operator review document,
bind its digest to an authenticated privileged change, record the audit intent and
require its current revision when applying. Enrollment is an administrative action;
it never submits accounting transactions itself.

Add explicit `post-production` and `manage-production` permissions. Do not add them
to existing grants on configuration load or implicitly to legacy role presets.
Production provisioning requests them deliberately, with exact company assignment.
Keep configurable role combinations and self-approval behavior from the agreed scope,
but separate-role approval remains the default. A policy change allowing self-approval
must be deliberate, reviewed and audited; changing policy invalidates older approval
fingerprints. Production enrollment itself cannot be self-issued by a preparer tool.

Evaluate existing `owner_access` overrides: they must not bypass production enrollment,
identity, approval, source review, quotas or durable write safeguards. General permission
membership alone does not imply the sensitive production-administration capability.

## Write entry points and enforcement map

| Existing entry point | Current boundary | Required production behavior |
| --- | --- | --- |
| Shared QBWC enqueue | `qbwc_posting.enqueue` and `_authority` call sample operation gates | Select environment explicitly; authorize actor and reserve exact durable production budget before enqueue |
| QBWC sendRequestXML | `DurableQBWCPostingService._context` and `_do_sendRequestXML`, write phase | Reload current policy; recheck all five decisions in the same DB transaction that durably records the first write handoff |
| Repeated QBWC callback | Existing phase/request/response records | Query phases may replay their exact retained request; write phases never emit an already handed-out mutation again |
| Native invoice/bill/payment/credit/refund/application paths | `sample_*_posting.post`, `gate`, final `*_write_authorized` event | Remain sample-only until each has explicit transport qualification; no production fallback through an older native route |
| Native master change | `master_posting.post`, `gate`, final `sample_master_write_authorized` | Remain sample-only until the required QBWC master path is qualified; expose an explicit unsupported result meanwhile |
| Journal memo repair | `journal_memo_repair` branch in QBWC write processing | Treat Mod as a separate mutation with exact original/EditSequence/diff, explicit authority and bounded count; never inherit Add permission automatically |
| Scheduled/automatic dispatch | `dispatch.authority`, `dispatch.require`, claims and occurrences | Same production enrollment/final gate; schedule cannot widen permission or impersonate an approver; preserve original claim on retry |
| Browser/CLI | Authenticated calls through Bridge/service adapters | Route to the same shared authorization contract; reject raw mutation XML and any caller-controlled environment override |
| Remote MCP/Hermes/Composio | Scoped tools and reviewed workflow adapters | Preserve narrow tool boundaries; preparation tools gain no accounting dispatch simply because production support exists |
| Simulation | `Bridge`/`SyntheticLedger` simulation path | Never produces a native write or a production receipt; clearly label synthetic output |
| Recovery/restore | Query-only operation recovery and paused isolated restore | Does not require permission to create a new write; authenticates read/recover, preserves original context and forbids resubmission |

Ten shared contracts currently cover invoice, bill, sales receipt, check, journal,
inventory transfer, customer/supplier payment and customer/supplier credit. The
service also validates master change, customer refund and customer/supplier credit
application through other paths. All fourteen plus new account transfer/account
creation must receive a transport-specific gate and acceptance row; an operation
is not supported in production just because its validator accepts input.

## Durable limits and at-most-once handoff

Use company-local SQLite transactions to reserve an enrollment/operation budget
claim bound to job ID, source identity, payload fingerprint, approved policy revision,
request hash and intended transport. Add unique constraints for the job and native
attempt lineage. Reserve exposure before authorizing the handoff; do not free limits
merely because a response is missing, a worker exits or a client retries.

Model transitions explicitly: prepared -> validated -> approved/submitted -> reserved
-> write authorized/handed out -> posted-unverified -> verified. Held/failed/unknown
are durable outcomes, not invitations to create a new reference. Preserve the existing
job state vocabulary where possible and store production authorization separately.

Approval/submission and transaction handoff remain separate. In one final transaction:
verify current config/revision, actor and approver grants, enrollment/time, company
pause, signature/digest consistency, fresh source/master checks, collision-free
preflight, original schedule claim and unused write handoff; then record authorization,
budget claim consumption and request hash before emitting the exact request.

Enforce one writer per company and an installation ownership/lease mechanism before
supporting multiple service instances. SQLite file locking alone cannot prevent two
different restored databases on different hosts from writing the same QB company.
Fence standby installations through deployment ownership and connector revocation.

There is no general exactly-once guarantee from QuickBooks and a network callback.
Offer at-most-once mutation handoff plus query reconciliation: loss after handoff
leaves an unknown result. Query by retained native identity when available, otherwise
use bounded exact candidate lookup and verify the entire record. Zero or ambiguous
matches require investigation, not automatic resend. Never treat the add response
as independent readback.

## Revocation and emergency stop

Enrollment expiry/disable, company pause, actor or approver revocation, changed binding,
changed release capabilities or changed reviewed payload must prevent future final
authorization. Pause must be available without enabling production administration.
Reload the relevant current configuration at each write fence, not only at process start.

A mutation already handed to QuickBooks cannot be recalled. Record it as in-flight,
complete readback or reconcile without replay. Show the operator whether stop occurred
before or after handoff. Recovery queries remain available to authorized read/recover
actors while writes are disabled. Old sample recovery remains compatible.

Restoring Bridge state must create a new paused installation ownership generation.
Reconcile existing QB transactions since the backup before allowing dispatch. Copying
an old enabled configuration is never sufficient to start a writer.

## Threats and required negative tests

| Threat | Required refusal or containment |
| --- | --- |
| Document/chat prompt injection | Cannot alter company, actor, grants, source review, configuration or execution policy |
| Wrong tenant or swapped QB company file | Reject before any mutation; no company-name-only fallback |
| Compromised preparation token | Cannot approve, manage enrollment or post beyond exact grants |
| Legacy administrator/owner privilege | Cannot implicitly acquire new production activation or bypass binding/limits |
| Stale approval or changed configuration | Reject at final write boundary even after enqueue |
| Concurrent workers/replayed callbacks | One durable handoff; unknown outcome preserved |
| Lost response, restart or restored database | Reconcile read-only; never reset counters or infer rollback |
| Clock rollback or expired policy | Fail closed on invalid time/window; monotonic process measurements do not replace durable UTC validity |
| Negative/NaN/exponent/overprecision amounts | Strict decimal parsing and supported currency quantization; no float budget arithmetic |
| Notification/Composio retry | May retry authorized notification delivery, never accounting dispatch |
| Crafted XML/upload/HTTP request | Bounded parsing, authentication, origin/session protections and no arbitrary mutation endpoint |
| Secret/audit tampering | Restricted secret storage, redaction, audit verification and operational alert/hold |

## Implementation sequence and proof

1. Implement strict policy/permission/enrollment parsing and tests that default to
   disabled, deny implicit privilege growth and reject every malformed authority field.
2. Add durable schema migration and budget/authorization records without enabling a
   writer; prove schema-1 hashes, jobs, receipts and used sample quotas are unchanged.
3. Integrate shared production authorization at enqueue and final QBWC handoff, then
   qualify every operation-specific transport, including repair and master paths.
4. Extend scheduling, UI, setup and capability reporting; dynamic `live_posting` must
   describe effective company capability, not a global unconditional constant.
5. Run full synthetic adversarial/concurrency/recovery tests, then approved sample,
   restored-company and limited live qualification in the roadmap order.

This design does not authorize deployment activation. New sample batches, restored
real-company access and live activation remain the separately reviewed user decisions
defined in ENVIRONMENTS.md. Production stays disabled until those gates pass.
