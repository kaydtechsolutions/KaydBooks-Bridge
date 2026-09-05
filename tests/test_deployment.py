import json
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest
from fastapi.testclient import TestClient

from kaydbooks_bridge.config import BridgeError
from kaydbooks_bridge.deployment import QWCProfile, create_staging_app, load_secret_file


def profile_file(tmp_path: Path) -> Path:
    path = tmp_path / "profile.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "app_name": "Synthetic Bridge Qualification",
                "endpoint_url": "https://localhost:8443/qbwc",
                "support_url": "https://localhost:8443/support",
                "username": "connector-synthetic-a",
                "owner_id": "{57F3B9B0-86F1-4fcc-B1EE-566DE1813D20}",
                "file_id": "{57F3B9B0-86F1-4fcc-B1EE-566DE1813D21}",
                "run_every_seconds": 900,
                "auth_flags": 8,
            }
        ),
        encoding="utf-8",
    )
    return path


def test_stable_qwc_generation_refuses_changed_profile(tmp_path):
    profile_path = profile_file(tmp_path)
    target = tmp_path / "synthetic.qwc"
    first = QWCProfile.load(profile_path).write_stable(target)
    second = QWCProfile.load(profile_path).write_stable(target)
    assert first == second
    root = ET.fromstring(target.read_text(encoding="utf-8"))
    assert root.findtext("AuthFlags") == "0x8"

    data = json.loads(profile_path.read_text(encoding="utf-8"))
    data["file_id"] = "{57F3B9B0-86F1-4FCC-B1EE-566DE1813D22}"
    profile_path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(BridgeError, match="existing QWC differs"):
        QWCProfile.load(profile_path).write_stable(target)


def test_qwc_profile_requires_https_and_exact_callback_path(tmp_path):
    path = profile_file(tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["endpoint_url"] = "http://localhost:8443/qbwc"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(BridgeError, match="endpoint URL"):
        QWCProfile.load(path)


def test_private_secret_file_loads_only_long_kaydbooks_values(tmp_path, monkeypatch):
    path = tmp_path / "secrets.json"
    path.write_text(json.dumps({"KAYDBOOKS_QBWC_TEST_SECRET": "s" * 32}), encoding="utf-8")
    load_secret_file(path)
    assert len(__import__("os").environ["KAYDBOOKS_QBWC_TEST_SECRET"]) == 32

    path.write_text(json.dumps({"PASSWORD": "s" * 32}), encoding="utf-8")
    with pytest.raises(BridgeError, match="credential"):
        load_secret_file(path)
    monkeypatch.delenv("KAYDBOOKS_QBWC_TEST_SECRET", raising=False)


def test_staging_health_is_explicitly_read_only(tmp_path, monkeypatch):
    config_path = tmp_path / "config.json"
    state = tmp_path / "state"
    config_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "mode": "simulation",
                "state_root": str(state),
                "companies": {
                    "synthetic-company": {
                        "simulation_identity": "synthetic-company",
                        "currency": "USD",
                        "max_total": "100.00",
                        "customers": ["synthetic-customer"],
                        "items": ["synthetic-item"],
                        "sources": ["synthetic-source"],
                        "approval_required": True,
                    }
                },
                "principals": {
                    "synthetic-operator": {
                        "token_env": "KAYDBOOKS_SYNTHETIC_OPERATOR_SECRET",
                        "companies": {"synthetic-company": ["read"]},
                    }
                },
                "connectors": {
                    "connector-synthetic": {
                        "company": "synthetic-company",
                        "password_env": "KAYDBOOKS_QBWC_SYNTHETIC_SECRET",
                        "identity_fields": ["CompanyName", "LegalCompanyName", "EIN"],
                        "identity_sha256": "0" * 64,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("KAYDBOOKS_QBWC_SYNTHETIC_SECRET", "s" * 32)
    app = create_staging_app(config_path, "https://localhost:8443/qbwc")
    body = TestClient(app).get("/healthz").json()
    assert body == {"status": "ready", "mode": "read-only-discovery", "live_posting": False}
