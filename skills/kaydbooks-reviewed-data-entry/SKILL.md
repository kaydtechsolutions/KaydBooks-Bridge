---
name: kaydbooks-reviewed-data-entry
description: Validate, dispatch, reconcile, and report an explicitly reviewed QuickBooks Desktop batch through KaydBooks Bridge and QuickBooks Web Connector. Use for live or qualification data entry that requires source-order processing, duplicate prevention, at-most-once writes, and independent QuickBooks readback. Do not use for unreviewed document extraction or unsupported direct UI posting.
---

# KaydBooks Reviewed Data Entry

Complete the authorized batch while preserving the reviewed accounting intent and producing evidence for every saved record.

## Required inputs

Resolve these before enabling posting:

- exact source or reviewed batch file and a cryptographic hash;
- target Bridge company, connector, and QuickBooks company-file path;
- private runtime configuration and credentials outside Git;
- explicit authorization covering the requested records and any master creation;
- source sequence and the expected number of QuickBooks financial entries;
- confirmed exceptions such as intentional reference reuse or an approved existing master-name spelling.

Do not infer accounting mappings from similar names. Read exact QuickBooks masters and bind the reviewed values to stable ListIDs. Keep the original reviewed input unchanged; record approved corrections in the authorization envelope.

## Route the work

Read [references/reviewed-batch-workflow.md](references/reviewed-batch-workflow.md) before staging or dispatching a live batch.

Read [references/qbxml-readback-and-recovery.md](references/qbxml-readback-and-recovery.md) when implementing or diagnosing callbacks, saved-field comparison, payments, discounts, balance checks, interrupted sessions, or uncertain outcomes.

Read [references/reporting-and-delivery.md](references/reporting-and-delivery.md) when the user requests a completion report, image, or channel delivery.

## Non-negotiable invariants

- Bind every callback to the configured company file and verified company identity.
- Process financial transactions by source-document prefix. Keep multiple legs from one source together. Insert a required master creation immediately before its first dependent transaction without counting it as an extra financial entry.
- Perform a fresh exact-reference duplicate query immediately before each write. An existing reference blocks posting unless the authorization explicitly identifies the accepted existing record and permits a distinct transaction with the same reference.
- Persist the exact write request and handout fence before returning qbXML to Web Connector. Never hand out the same write twice.
- Treat a handed-out write with no conclusive response as uncertain. Reconcile by read-only reference lookup and exact TxnID readback. Never resend it and never change its reference to bypass duplicate protection.
- A successful Add response is evidence of creation, not final verification. Query the returned TxnID or ListID independently and compare every explicitly supplied field.
- Advance to the next source only after readback and required balance effects pass.
- Pause the company on any mismatch, uncertainty, or completion.
- Keep QuickBooks data, source documents, mappings, credentials, raw requests/responses, and report evidence outside Git. Commit only generic code, tests, and reusable documentation.

## Completion standard

Finish only when each expected financial entry is verified or held with conclusive evidence explaining why safe completion is impossible. Report completion percentage, verified count, current source, blocker, and posting state during a live run.

The final report must list every financial entry with status and QuickBooks TxnID, identify separately created masters by ListID, state all retained exceptions, show the requested reconciliations, confirm posting is paused, and distinguish channel send acknowledgment from recipient delivery or read receipt.
