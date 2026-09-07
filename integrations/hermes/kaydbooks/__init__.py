"""Hermes plugin: consume exact confirmations before the LLM handles the message."""

import asyncio
import logging

from .client import confirmation_event, rpc, settings

log = logging.getLogger(__name__)
_tasks = set()


async def _confirm(config, parameters):
    try:
        await asyncio.to_thread(rpc, config, "confirm", parameters)
        log.info("KaydBooks operator confirmation recorded: %s", parameters["event_id"])
    except Exception:
        # No accounting retry and no unsolicited outbound error message.
        log.exception("KaydBooks confirmation needs operator attention")


def incoming(*, event, **kwargs):
    raw = getattr(event, "raw_message", None)
    if not isinstance(raw, dict) or not str(raw.get("body", "")).startswith("/kb-confirm"):
        return None
    try:
        config = settings()
        parameters = confirmation_event(event, config)
        if parameters is not None:
            task = asyncio.get_running_loop().create_task(_confirm(config, parameters))
            _tasks.add(task)
            task.add_done_callback(_tasks.discard)
    except Exception:
        log.exception("KaydBooks confirmation rejected")
    # Never let failed/untrusted confirmation text fall through to the model.
    return {"action": "skip", "reason": "KaydBooks exact confirmation handled separately"}


def register(ctx):
    ctx.register_hook("pre_gateway_dispatch", incoming)
    ctx.register_system_prompt_section(
        "kaydbooks-reviewed-entry",
        """
For KaydBooks data entry use only the configured kaydbooks MCP tools and authorized company alias.
First read company_catalog_v1 for the selected company. Use an exact value from its sources
as the capture namespace; never substitute the company ID or filename for a source namespace.
Capture the original uploaded bytes using a stable lowercase upload reference matching
[a-z][a-z0-9_-]{0,63}. This internal upload ID is separate from the unchanged invoice ref_number.
Reuse that upload reference on retries. Inspect the content and clarify missing or ambiguous fields.
For captured JSON containing company, operation and payload, prefer qbwc_entry_v1 prepare_upload
with exactly document_id and connector_id from the catalog. It parses the saved source, checks
QuickBooks, prepares and validates. Repeat the same call after pending=true until ready_for_review.
Do not reconstruct the payload, invent confidence or call prepare separately for this path.
It does not post or send messages. Inspect its review, then use batch_preview_v1 only for validated
jobs when the operator requested review. For an existing verified result, report status only.
Before preparing any retry, call qbwc_entry_v1 find with operation and ref_number.
It returns owned matching job IDs, payloads and states. Compare the exact source payload,
then call status with the existing id. A verified match is already posted: report it,
do not prepare it again or request another confirmation. Multiple matches need clarification.
On duplicate-key/source/reference conflict, use find; never change identifiers to evade it.
Use the catalog mappings; never invent QuickBooks IDs, amounts or extraction certainty.
Use qbwc_entry_v1 check, wait for Web Connector evidence, then prepare and validate each entry.
For check, parameters contain exactly operation, connector_id from catalog.connectors, and payload.
Do not include document_id, namespace or reference in check. Repeat the same check after pending=true.
For prepare, pass operation, document_id, idempotency_key, payload, confidence and the returned
check evidence as master_evidence. validate/preview/status/submit/dispatch/recover take only job_id.
Build confidence using the exact confidence_schema returned by check: flat leaf paths only,
zero-based dot indices such as lines.0.amount, no brackets and no container keys like lines.
Scores must be finite numbers 0-1 reflecting extraction certainty, never automatic approval.
An ok=false tool result is a rejection: correct its stated fields or report the hold; never treat it as success.
Use batch_preview_v1 for the exact validated jobs. The trusted worker sends that immutable preview.
The operator must type the exact /kb-confirm reply in their configured WhatsApp direct chat.
Never create or simulate a confirmation event, read channel signing secrets, call the private
channel RPC, use a terminal to bypass confirmation, or approve on behalf of the operator.
Use batch_status_v1 to report progress. Queued is not posted; only verified readback is success.
Never retry an unknown accounting write. Message-delivery failures never authorize reposting.
""",
    )
