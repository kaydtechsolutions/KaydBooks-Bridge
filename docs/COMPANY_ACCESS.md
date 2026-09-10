# Company users, roles and approval

A principal with `manage-users` may administer users only in explicitly assigned
companies. New assigned users receive read-only access unless the request specifies
roles or permissions. Loading configuration never expands
existing grants. A new setup operator includes `manage-users`; existing deployments
need an explicit operator-controlled grant to their intended administrator.

Role presets are combinable:

| Role | Permissions |
| --- | --- |
| preparer | read, prepare, validate, submit, review-source |
| approver | read, validate, approve |
| administrator | all legacy permissions; production permissions require explicit grants |

`roles` selects their union. `permissions` supplies an exact list instead; it cannot be
combined with `roles`. `deny` removes individual permissions. An empty `permissions`
or `roles` list grants nothing. This can revoke access without deleting the user or
historical audit records. Restrict `manage-users` when a user must not administer access.

Credential values are never accepted or returned. A new principal requires a unique
private `KAYDBOOKS_...` environment reference, provisioned separately by the local
operator. Editing one company's grants cannot replace an existing principal's global
credential or change another company's grants, mappings or company identity.

Use `python -m kaydbooks_bridge.access --config PRIVATE_CONFIG --company COMPANY
inspect` to review users and the configuration revision. `set-user REQUEST.json`
accepts `principal`, `expected_revision` and optional `roles`, `permissions`, `deny`,
`token_env`. The `self-approval REQUEST.json` action accepts `expected_revision` and a
boolean `allow`. The same contracts are available through `company_access_v1` with
`inspect`, `set_user` and `set_self_approval` actions. No accounting write occurs.

Self-approval defaults to false. Enabling it does not grant the approve permission or
remove the company's approval requirement. An authorized user still deliberately
approves the exact job. Current policy and permissions are rechecked on submit and
native dispatch; disabling self-approval or revoking approval permission holds the job.

Every mutation requires the reviewed configuration revision. Cooperating writers use
one configuration lock, validate a complete candidate, retain a durable audit intent
with before/after permissions and content hashes, and atomically replace the private
file. The completion event records the applied revision. Concurrent or stale requests
fail instead of overwriting a newer change. An interrupted replacement leaves either
the previous or new valid configuration; inspect current access before retrying.

Tests cover default/full and combined roles, individual restrictions, empty grants,
cross-company denial, immutable credential references, concurrency, failed replacement,
CLI/MCP routing, queued-job revocation and changing self-approval policy. These access
contracts support the separate manual-form and conversational workflow milestones.

## Explicit production permissions

`post-production` and `manage-production` are separate, explicitly named permissions.
They are not included in the administrator preset, ordinary setup, read-only defaults
or timed owner overrides. No existing principal gains them when configuration loads.
Changing either permission through `set-user` requires both `manage-users` and an
explicit `manage-production` grant in the same company. The first production
administrator must therefore be provisioned deliberately through trusted local
configuration; a legacy company administrator cannot promote itself.

Permission membership alone does not enable production posting. The current build
still rejects a production mode; reviewed enrollment and final write authorization
are separate implementation work in [the production design](production/AUTHORIZATION_DESIGN.md).

## Credential rotation and disabling principals

Local administrative CLI actions accept credential **references**, never secret values:

```text
python -m kaydbooks_bridge.access --config PRIVATE_CONFIG --company COMPANY rotate-credential REQUEST.json
python -m kaydbooks_bridge.access --config PRIVATE_CONFIG --company COMPANY disable-user REQUEST.json
```

For rotation, the request contains `principal`, `expected_revision`, and `token_env`.
Provision a new distinct secret of at least 32 characters in the private secret
store of every process serving that principal, then reload those services so the
new environment reference is available. Keep the old reference during this staging
step. Run the reviewed rotation; the configuration switches references atomically,
so the old token stops authenticating on subsequent requests even if its old
environment variable still exists. Verify the new token through an authenticated
read before removing old secret material. Do not log either value or place it in
the request JSON. On failure inspect the actual current config revision before retrying.

Disabling accepts `principal`, `expected_revision`, and boolean `disabled`.
Disabled principals cannot authenticate, use retained actor identities or activate
owner overrides. Their earlier approvals fail the current-permission check before
new dispatch. Existing jobs, grants and audit history remain intact; re-enabling is
an explicit audited action. Rotation preserves the principal's grants and approvals;
use disabling or grant revocation when authority itself must be removed.

These actions affect the principal across companies. The caller needs `manage-users`
in every assigned company, and explicit `manage-production` in each company where
the target has a production permission. A company-only administrator cannot rotate
another company's credentials through a shared user. The existing company access
MCP tool does not expose these global credential/disable actions.

All changes retain revision checks and audit intents/results. POSIX replacements
preserve the original file owner/group/mode and sync the parent directory so a
service account does not lose read access after an operator runs a change. Windows
configuration must remain within the installer-protected private directory.
