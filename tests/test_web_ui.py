"""Browser routes exercise shared permissions, source retention and retry contracts."""
# ruff: noqa: F811

import base64
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kaydbooks_bridge import web_ui
from test_bridge import TOKENS, setup  # noqa: F401

ORIGIN = "https://localhost:8443"


@pytest.fixture
def client(setup):
    app = FastAPI()
    web_ui.install(app, setup[1], ORIGIN + "/qbwc")
    return TestClient(app, base_url=ORIGIN)


def call(client, action, parameters=None, company="company-a", principal="preparer-a", **kwargs):
    return client.post(
        "/api/ui",
        json={"action": action, "company": company, "parameters": parameters or {}},
        headers={"Authorization": "Bearer " + TOKENS[principal], **kwargs},
    )


def prepare(client, setup, key="browser-one"):
    return call(
        client,
        "prepare",
        {
            "request_key": key,
            "namespace": "synthetic-intake",
            "operation": "invoice.create",
            "payload": setup[3]["payload"],
        },
    )


def test_assets_and_no_credential_persistence(client):
    for path in ("/app", "/app/app.js", "/app/app.css"):
        response = client.get(path)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert client.get("/app/bridge-config.json").status_code == 404
    source = client.get("/app/app.js").text
    assert "innerHTML" not in source and "localStorage" not in source
    assert "sessionStorage" not in source and "eval(" not in source


def test_catalog_auth_company_isolation_and_no_secrets(client):
    assert client.post("/api/ui", json={}).status_code == 400
    initial = call(client, "catalog", company=None).json()
    assert initial["companies"] == ["company-a"]
    response = call(client, "catalog").json()
    assert response["currency"] == "USD" and len(response["operations"]) == 10
    assert "master.change" in response["operations"]
    assert call(client, "catalog", company="company-b").status_code == 400
    assert all(token not in json.dumps(response) for token in TOKENS.values())
    assert "token_env" not in json.dumps(response) and "company_file_env" not in json.dumps(
        response
    )


def test_origin_host_and_arbitrary_commands_denied(client):
    assert call(client, "catalog", Origin="https://attacker.example").status_code == 400
    assert call(client, "catalog", Host="attacker.example").status_code == 400
    for command in ("shell", "sql", "qbxml", "simulate", "delete"):
        assert call(client, command).status_code == 400
    assert call(client, "status", {"sql": "DELETE FROM jobs"}).status_code == 400
    assert (
        client.post(
            "/api/ui", content="{" * 30, headers={"Authorization": "Bearer " + TOKENS["preparer-a"]}
        ).status_code
        == 400
    )


def test_manual_retry_review_and_source_are_shared(client, setup):
    result = prepare(client, setup)
    assert result.status_code == 200, result.text
    job = result.json()
    assert prepare(client, setup).json()["id"] == job["id"]
    source = call(client, "source", {"job_id": job["id"]}).json()
    original = json.loads(base64.b64decode(source["content_base64"]))
    assert original["payload"] == setup[3]["payload"]
    assert call(client, "validate", {"job_id": job["id"]}).status_code == 200
    assert call(client, "submit", {"job_id": job["id"]}).status_code == 400
    assert call(client, "approve", {"job_id": job["id"]}, principal="approver-a").status_code == 200
    assert call(client, "submit", {"job_id": job["id"]}).status_code == 200
    assert call(client, "post-sample", {"job_id": job["id"]}).status_code == 400
    status = call(client, "status").json()
    assert (
        status["total_jobs"] == 1
        and status["jobs"][0]["ref_number"] == original["payload"]["ref_number"]
    )
    assert (
        call(
            client, "source", {"job_id": job["id"]}, company="company-b", principal="operator-b"
        ).status_code
        == 400
    )
    assert setup[0].audit(TOKENS["operator-a"], "company-a")["valid"]


def test_current_permissions_rechecked_after_login(client, setup):
    assert call(client, "catalog").status_code == 200
    data = setup[2]
    data["principals"]["preparer-a"]["companies"]["company-a"] = ["read"]
    setup[1].write_text(json.dumps(data))
    assert prepare(client, setup).status_code == 400
    assert call(client, "status").json()["jobs"] == []


def test_table_column_inspection_binds_owner_and_format(client, setup):
    from test_tabular import capture

    document_id = capture(
        setup, [["one", "IMPORT-1", "2026-09-07", "customer-a", "item-a", "5.00"]]
    )
    params = {"document_id": document_id, "format": {"delimiter": ","}}
    result = call(client, "table-columns", params)
    assert result.status_code == 200 and result.json()["row_count"] == 1
    assert result.json()["headers"][0] == "Row"
    assert call(client, "table-columns", params, principal="operator-a").status_code == 400
    assert (
        call(client, "table-columns", {**params, "format": {"path": "secrets.json"}}).status_code
        == 400
    )


def test_body_limit_checked_before_action(client):
    response = client.post(
        "/api/ui",
        content=" " * (6 * 1024 * 1024 + 1),
        headers={"Authorization": "Bearer " + TOKENS["preparer-a"]},
    )
    assert response.status_code == 400 and "too large" in response.text
