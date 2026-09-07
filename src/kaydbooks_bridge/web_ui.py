"""Same-origin browser facade over the shared company contracts."""

import base64
import json
import os
import time
from pathlib import Path
from urllib.parse import urlsplit

from . import documents
from .config import BridgeError, Config, identifier, strict_keys
from .direct_sdk import discover
from .qbwc import DurableQBWCDiscoveryService
from .service import Bridge, validate_payload
from .validation import canonical, digest

OPERATIONS = {
    "master.change": ("Customer, supplier or item change", None),
    "invoice.create": ("Sales invoice", "invoice_check"),
    "bill.create": ("Supplier bill", "bill_check"),
    "customer-payment.create": ("Customer payment", "payment_check"),
    "supplier-payment.create": ("Supplier payment", "supplier_payment_check"),
    "customer-credit.create": ("Customer credit", "credit_check"),
    "supplier-credit.create": ("Supplier credit", "supplier_credit_check"),
    "customer-refund.create": ("Customer refund record", "refund_check"),
    "customer-credit.apply": ("Apply customer credit", "application_check"),
    "supplier-credit.apply": ("Apply supplier credit", "supplier_application_check"),
}


def catalog(config_path, token, company=None):
    config = Config.load(config_path)
    actor = config.authenticate(token)
    assigned = config.principals[actor]["companies"]
    result = {
        "principal": actor,
        "companies": [name for name, grants in assigned.items() if grants],
        "production_posting": False,
    }
    if company is None:
        return result
    policy = config.authorize(actor, company, "read")
    grants = assigned[company]
    result.update(
        company=company,
        currency=policy.currency,
        sources=list(policy.sources),
        permissions=list(grants),
        approval_required=policy.approval_required,
        self_approval=policy.allow_self_approval,
        connectors=[c.id for c in config.connectors.values() if c.company == company],
        operations={k: v[0] for k, v in OPERATIONS.items()},
        choices={
            "customers": list(policy.customers),
            "items": list(policy.items),
            "bill_vendors": list(policy.bill_masters.get("vendors", {})),
            "bill_items": list(policy.bill_masters.get("items", {})),
            "expenses": list(policy.bill_masters.get("expenses", {})),
            "terms": list(policy.bill_masters.get("terms", {})),
            "payment_customers": list(policy.payment_masters.get("customers", {})),
            "deposits": list(policy.payment_masters.get("deposits", {})),
            "methods": list(policy.payment_masters.get("methods", {})),
            "payment_vendors": list(policy.supplier_payment_masters.get("vendors", {})),
            "banks": list(policy.supplier_payment_masters.get("banks", {})),
        },
        report_entities=[
            {"id": native, "label": alias}
            for alias, native in {
                **policy.invoice_masters.get("customers", {}),
                **policy.payment_masters.get("customers", {}),
                **policy.bill_masters.get("vendors", {}),
                **policy.supplier_payment_masters.get("vendors", {}),
            }.items()
        ],
        report_items=[
            {"id": native if isinstance(native, str) else native["list_id"], "label": alias}
            for alias, native in {
                **policy.invoice_masters.get("items", {}),
                **policy.bill_masters.get("items", {}),
            }.items()
        ],
        master_account_roles=list(policy.account_roles),
    )
    from .native_reports import FIXED_ACCRUAL, FIXED_COLUMNS, REPORTS

    result["reports"] = {
        name: {
            "date_mode": value[2],
            "fixed_accrual": name in FIXED_ACCRUAL,
            "fixed_columns": name in FIXED_COLUMNS or value[0] != "GeneralSummary",
        }
        for name, value in REPORTS.items()
    }
    return result


def check_masters(bridge, token, company, operation, connector_id, payload):
    if operation not in OPERATIONS:
        raise BridgeError("operation unavailable")
    config, _, policy, _ = bridge._context(token, company, "validate")
    connector = config.connectors.get(connector_id)
    if connector is None or connector.company != company:
        raise BridgeError("select the company's exact connector")
    payload = validate_payload(operation, payload, policy)
    if operation == "master.change":
        from .master_checks import read

        result = read(bridge, token, company, connector_id, payload["kind"], payload=payload)
        return {
            "evidence": result["reference"],
            "payload_sha256": digest(payload),
            "result": result,
        }
    run = str(time.time_ns())[-15:]
    result = discover(
        DurableQBWCDiscoveryService.from_path(bridge.config_path),
        token,
        connector_id,
        os.environ.get(connector.password_env, ""),
        run,
        **{OPERATIONS[operation][1]: payload},
    )
    # Changed configuration or revoked credentials cannot expose a stale successful check.
    latest, _, current, _ = bridge._context(token, company, "validate")
    if latest.connectors.get(connector_id) != connector or current != policy:
        raise BridgeError("company settings changed during the check")
    return {
        "evidence": {"transport": "direct-sdk", "connector": connector_id, "id": run},
        "payload_sha256": digest(payload),
        "result": result,
    }


def manual(
    bridge,
    token,
    company,
    request_key,
    namespace,
    operation,
    payload,
    master_evidence=None,
    revision=None,
):
    identifier(request_key)
    if operation not in OPERATIONS:
        raise BridgeError("operation unavailable")
    _, _, policy, _ = bridge._context(token, company, "prepare")
    validate_payload(operation, payload, policy)
    content = canonical({"source": "manual-entry", "operation": operation, "payload": payload})
    source = documents.capture(
        bridge,
        token,
        company,
        namespace,
        "manual-" + digest([request_key, content])[:56],
        "application/json",
        base64.b64encode(content.encode()).decode(),
    )
    confidence = dict.fromkeys(documents.fields(payload), 1)
    if revision is not None:
        strict_keys(revision, {"parent_id", "parent_fingerprint", "reason"})
        parent = bridge.status(token, company, revision["parent_id"])
        if parent["operation"] != operation:
            raise BridgeError("a correction cannot change operation")
        return documents.revise(
            bridge,
            token,
            company,
            **revision,
            document_id=source["document_id"],
            idempotency_key=request_key,
            payload=payload,
            confidence=confidence,
            master_evidence=master_evidence,
        )
    return documents.prepare(
        bridge,
        token,
        company,
        source["document_id"],
        request_key,
        payload,
        confidence,
        master_evidence,
        operation=operation,
    )


def action(bridge, token, company, action, parameters):
    contracts = {
        "status": set(),
        "dispatch-status": set(),
        "dispatch-create": {"profile_id", "specification"},
        "dispatch-cancel": {"profile_id"},
        "dispatch-tick": set(),
        "job": {"job_id"},
        "preview": {"job_id"},
        "check": {"operation", "connector_id", "payload"},
        "prepare": {"request_key", "namespace", "operation", "payload"},
        "validate": {"job_id"},
        "approve": {"job_id"},
        "submit": {"job_id"},
        "post-sample": {"job_id"},
        "reconcile-sample": {"job_id"},
        "review-source": {"job_id", "fingerprint", "confirmed_values"},
        "pause": {"paused"},
        "table-columns": {"document_id", "format"},
        "source": {"job_id"},
        "master-lookup": {"connector_id", "kind", "list_id"},
    }
    if action not in contracts:
        raise BridgeError("browser action unavailable")
    strict_keys(
        parameters,
        contracts[action],
        {"master_evidence", "revision"} if action == "prepare" else set(),
    )
    if action.startswith("dispatch-"):
        from . import dispatch

        method = {
            "dispatch-status": dispatch.status,
            "dispatch-create": dispatch.create,
            "dispatch-cancel": dispatch.cancel,
            "dispatch-tick": dispatch.tick,
        }[action]
        return method(bridge, token, company, **parameters)
    if action == "status":
        result = bridge.status(token, company)
        _, _, _, store = bridge._context(token, company, "read")
        with store.transaction() as db:
            payloads = {
                row["id"]: json.loads(row["payload"])
                for row in db.execute("SELECT id,payload FROM jobs ORDER BY rowid DESC LIMIT 100")
            }
        result["total_jobs"] = len(result["jobs"])
        for job in result["jobs"]:
            if job["id"] in payloads:
                job["ref_number"] = payloads[job["id"]].get("ref_number")
                job["txn_date"] = payloads[job["id"]].get("txn_date")
        result["jobs"] = result["jobs"][-100:][::-1]
        return result
    if action == "job":
        result = bridge.status(token, company, **parameters)
        from .extraction import schema

        _, _, _, store = bridge._context(token, company, "read")
        with store.transaction() as db:
            schema(db)
            observed = db.execute(
                "SELECT e.result FROM extraction_jobs j JOIN document_extractions e ON e.id=j.extraction_id WHERE j.job_id=?",
                (result["id"],),
            ).fetchone()
            if observed and store.verify_audit(db):
                result["source_observations"] = json.loads(observed[0])
        return result
    if action == "preview":
        return bridge.preview(token, company, **parameters)
    if action == "source":
        job = bridge.status(token, company, parameters["job_id"])
        _, _, _, store = bridge._context(token, company, "read")
        document_id = job["source"]["original_values"].get("document_id")
        with store.transaction() as db:
            documents.schema(db)
            row = db.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone()
            if row is None or not store.verify_audit(db):
                raise BridgeError("retained source unavailable")
        return {
            "content_base64": base64.b64encode(row["bytes"]).decode(),
            "media_type": row["media_type"],
            "reference": row["reference"],
            "document_id": row["id"],
        }
    if action == "table-columns":
        from . import tabular

        _, actor, _, store = bridge._context(token, company, "prepare")
        strict_keys(parameters["format"], set(), {"sheet", "delimiter"})
        with store.transaction() as db:
            documents.schema(db)
            row = db.execute(
                "SELECT * FROM documents WHERE id=? AND owner=?",
                (parameters["document_id"], actor),
            ).fetchone()
            if row is None or not store.verify_audit(db):
                raise BridgeError("owned intact source document required")
            headers, rows = tabular.table(row["bytes"], row["media_type"], parameters["format"])
        return {"headers": headers, "sample_rows": [r[1] for r in rows[:5]], "row_count": len(rows)}
    if action == "check":
        return check_masters(bridge, token, company, **parameters)
    if action == "master-lookup":
        from .master_checks import read

        return read(bridge, token, company, **parameters)
    if action == "prepare":
        return manual(bridge, token, company, **parameters)
    if action in {"validate", "approve", "submit"}:
        return bridge.action(token, company, parameters["job_id"], action)
    if action == "pause":
        return bridge.pause(token, company, parameters["paused"])
    if action == "review-source":
        from .source_review import review

        return review(
            bridge,
            token,
            company,
            parameters["job_id"],
            parameters["fingerprint"],
            parameters["confirmed_values"],
        )
    job = bridge.status(token, company, parameters["job_id"])
    modules = {
        "master.change": "master_posting",
        "invoice.create": "sample_posting",
        "bill.create": "sample_bill_posting",
        "customer-payment.create": "sample_payment_posting",
        "supplier-payment.create": "sample_supplier_payment_posting",
        "customer-credit.create": "sample_credit_posting",
        "supplier-credit.create": "sample_supplier_credit_posting",
        "customer-refund.create": "sample_refund_posting",
        "customer-credit.apply": "sample_application_posting",
        "supplier-credit.apply": "sample_supplier_application_posting",
    }
    if job["operation"] not in modules:
        raise BridgeError("sample operation unavailable")
    from importlib import import_module

    module = import_module("kaydbooks_bridge." + modules[job["operation"]])
    return (module.reconcile if action == "reconcile-sample" else module.post)(
        bridge, token, company, job["id"]
    )


def install(app, config_path, endpoint_url):
    from fastapi import Request
    from fastapi.responses import FileResponse, JSONResponse
    from starlette.concurrency import run_in_threadpool

    origin = urlsplit(endpoint_url)
    expected_origin = origin.scheme + "://" + origin.netloc
    assets = Path(__file__).with_name("web")

    @app.get("/app")
    def page():
        return FileResponse(
            assets / "index.html", media_type="text/html", headers=security_headers()
        )

    @app.get("/app/{asset}")
    def asset(asset: str):
        if asset not in {"app.js", "app.css"}:
            return JSONResponse({"error": "not found"}, status_code=404)
        return FileResponse(
            assets / asset,
            media_type="text/javascript" if asset.endswith(".js") else "text/css",
            headers=security_headers(),
        )

    # Set concrete annotations because Request is intentionally an optional import.
    async def command(request):
        try:
            if (
                str(request.url).split("/api/ui", 1)[0] != expected_origin
                or request.headers.get("origin", expected_origin) != expected_origin
            ):
                raise BridgeError("use this workspace's own secure address")
            header = request.headers.get("authorization", "")
            if not header.startswith("Bearer ") or len(header) > 512:
                raise BridgeError("Bridge access key required")
            token = header[7:]
            Config.load(config_path).authenticate(token)
            content = bytearray()
            async for chunk in request.stream():
                content.extend(chunk)
                if len(content) > 6 * 1024 * 1024:
                    raise BridgeError("browser request too large")
            values = json.loads(content)
            strict_keys(values, {"action", "parameters"}, {"company"})
            name = values["action"]
            params = values["parameters"]
            company = values.get("company")
            if name == "catalog":
                strict_keys(params, set())
                result = await run_in_threadpool(catalog, config_path, token, company)
            else:
                if not isinstance(company, str):
                    raise BridgeError("choose a company first")
                if name in {
                    "native_report_v1",
                    "company_access_v1",
                    "table_intake_v1",
                    "capture_document_v1",
                    "extract_document_v1",
                    "prepare_extraction_v1",
                }:
                    from .hermes_tools import Tools

                    result = await run_in_threadpool(
                        Tools(config_path, token).call, name, company, params
                    )
                else:
                    result = await run_in_threadpool(
                        action, Bridge(config_path), token, company, name, params
                    )
            return JSONResponse(result, headers=security_headers())
        except BridgeError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400, headers=security_headers())
        except (ValueError, TypeError, KeyError):
            return JSONResponse(
                {"error": "invalid browser request"}, status_code=400, headers=security_headers()
            )

    command.__annotations__["request"] = Request
    app.add_api_route("/api/ui", command, methods=["POST"])


def security_headers():
    return {
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
    }
