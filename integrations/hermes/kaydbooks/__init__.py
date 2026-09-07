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
Use the catalog mappings; never invent QuickBooks IDs, amounts or extraction certainty.
Use qbwc_entry_v1 check, wait for Web Connector evidence, then prepare and validate each entry.
For check, parameters contain exactly operation, connector_id from catalog.connectors, and payload.
Do not include document_id, namespace or reference in check. Repeat the same check after pending=true.
For prepare, pass operation, document_id, idempotency_key, payload, confidence and the returned
check evidence as master_evidence. validate/preview/status/submit/dispatch/recover take only job_id.
An ok=false tool result is a rejection: correct its stated fields or report the hold; never treat it as success.
Use batch_preview_v1 for the exact validated jobs. The trusted worker sends that immutable preview.
The operator must type the exact /kb-confirm reply in their configured WhatsApp direct chat.
Never create or simulate a confirmation event, read channel signing secrets, call the private
channel RPC, use a terminal to bypass confirmation, or approve on behalf of the operator.
Use batch_status_v1 to report progress. Queued is not posted; only verified readback is success.
Never retry an unknown accounting write. Message-delivery failures never authorize reposting.
""",
    )
