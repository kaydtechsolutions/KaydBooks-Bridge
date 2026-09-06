# Draft corrections and retained history

Corrections create a new immutable job in the same canonical transaction lineage.
They never edit an earlier job's payload or source. Captured document bytes remain
immutable; each extraction/review is bound to its own fingerprint. The correction may
reuse the captured document or reference a newly captured, owned source document.

`revise_document_v1` requires the parent ID and exact fingerprint, a correction reason,
source document ID, a new idempotency key, corrected payload and confidence for every
field. Current matching master evidence is required whenever the operation requires
it. The operation and owner are inherited from the parent; callers cannot turn a
correction into another operation or another user's transaction.

Only the current draft, validated or queued job can be corrected, before any dispatch
attempt or transaction identity exists. The parent becomes superseded, loses its usable
approval and cannot be validated, previewed, submitted, changed or dispatched. Original
approval events remain in the immutable audit. The successor starts as a draft with no
approval or attempt, and must pass fresh source review, validation and approval policy.

Canonical source and business-reference reservations cover the whole lineage, including
prior corrected references. Re-importing the current extraction returns the current job;
re-importing superseded content conflicts. An exact retry of the same correction key
returns its existing successor. Stale parents, conflicting business references and
competing concurrent corrections are rejected. The database retains parent/root IDs,
revision number and reason; lineage and canonical-key reservations are append-only.

A dispatched, unknown, posted-unverified or verified transaction is never editable or
resubmittable through this workflow. Use receipt reconciliation for an uncertain outcome.
A maximum of 100 revisions prevents unbounded lineage growth. Administrative database
edits are not a supported revision interface.

CLI: `revise-document JOB_ID REQUEST.json` after the normal private `--config` and
explicit `--company` options. The JSON has `parent_fingerprint`, `reason`, `document_id`,
`idempotency_key`, `payload`, `confidence` and optional `master_evidence`. It uses the
same contract as the MCP tool and `documents.revise` service API.

Qualification covers original-byte/payload retention, new approval, invalid old previews,
idempotent re-imports, multiple revisions, collisions, ownership/permissions, concurrency,
SQL immutability and refusal to revise dispatched/posted work. A supplier-credit correction
requires new exact evidence. Installed-package sample qualification corrected an invoice
draft quantity from one to two, rejected the old master proof/preview, retained the original,
and left the successor validated without posting any accounting transaction.
