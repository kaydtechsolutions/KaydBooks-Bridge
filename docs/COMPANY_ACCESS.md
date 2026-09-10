# Company users, roles and approval

A principal with `manage-users` may administer users only in explicitly assigned
companies. New assigned users receive a concrete list of all supported permissions
unless the request specifies roles or permissions. Loading configuration never expands
existing grants. A new setup operator includes `manage-users`; existing deployments
need an explicit operator-controlled grant to their intended administrator.

Role presets are combinable:

| Role | Permissions |
| --- | --- |
| preparer | read, prepare, validate, submit, review-source |
| approver | read, validate, approve |
| administrator | all currently supported permissions |

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
