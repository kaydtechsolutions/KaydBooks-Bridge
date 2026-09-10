# Bounded unattended sample qualification

An operator may explicitly delegate approval of up to five exact sample transactions to a dedicated automation principal. This opt-in facility supports one bill, sales receipt, journal, customer payment and supplier payment. It does not approve invoices or master changes, start a worker, enable production posting, or change existing human approvals.

Add `sample_qualification` only to a principal with exactly `read` and `approve` for one company, no owner status, and a different preparer. Keep its credential in a private secret file. The grant requires:

- `id`, `company`, `connector`, nonzero bound `identity_sha256`, and `preparer` identifiers.
- Boolean `enabled`, epoch `activated_at` and `expires_at` (maximum eight hours), and the operator's explicit `authorization`.
- One to five `entries`, each containing an allowed `operation`, the exact `payload`, `source_namespace`, and `source_reference`. Operations cannot repeat.
- Optionally, payments may name an exact `allocation_job`; their single allocation must use `txn_id: "$verified-prerequisite"`. The bridge substitutes only the saved transaction ID of that preparer's verified invoice or bill, with a matching company receipt and bridge dispatch provenance.

Each operation still requires an active sample gate with quota one, fresh real master evidence, matching company identity, source review, validation, preview, normal submission authority, duplicate checks and independent transaction readback. The preparer cannot approve and the approver cannot submit. Approval audit records explicitly identify delegated automation, the grant digest, job fingerprint and operation. An entry cannot authorize another job after being bound once.

Grant scope is checked at approval, submission, and final dispatch. Expiry or disabling the grant blocks further dispatch. Keep normal approval required and self-approval disabled. After completion, set `enabled` to `false`; an unattended runner must also disable its grant when it stops. Preserve uncertain attempts for read-only recovery, never recreate or resend them. Expiration provides a fail-closed limit if a runner crashes before cleanup.

This is an administrative configuration contract, not an end-user permission shortcut. Replacing company identity, widening payloads, renewing a window or resetting quotas requires its own explicit operator authorization; routine refresh of evidence does not.
