import json
import os
from concurrent.futures import ThreadPoolExecutor

import pytest

from kaydbooks_bridge.config import BridgeError, Config
from kaydbooks_bridge.onboarding import initialize, inspect_setup, main


def request_file(tmp_path, company="company-a"):
    company_file = tmp_path / f"{company}.qbw"
    company_file.write_bytes(b"synthetic path fixture, not a QuickBooks file")
    request = tmp_path / f"{company}-request.json"
    request.write_text(
        json.dumps(
            {
                "target": {
                    "company_id": company,
                    "company_name": f"Synthetic {company}",
                    "company_file": str(company_file),
                },
                "currency": "USD",
                "max_total": "100.00",
            }
        ),
        encoding="utf-8",
    )
    return request


def inspect(root, company="company-a"):
    return inspect_setup(
        root / "bridge-config.json",
        company,
        root / "target.json",
        root / "credentials.json",
        principal="operator",
        connector_id="quickbooks",
    )


def test_new_users_get_independent_unbound_private_bundles(tmp_path, monkeypatch):
    bundles = []
    for company in ("company-a", "company-b"):
        root = tmp_path / company
        assert initialize(request_file(tmp_path, company), root)["status"] == "created-unbound"
        config = Config.load(root / "bridge-config.json")
        credentials = json.loads((root / "credentials.json").read_text())
        for name, token in credentials.items():
            monkeypatch.setenv(name, token)
        actor = config.authenticate(credentials["KAYDBOOKS_OPERATOR_SECRET"])
        config.authorize(actor, company, "read")
        for permission in ("submit", "approve", "post-sample", "validate", "simulate"):
            with pytest.raises(BridgeError, match="permission denied"):
                config.authorize(actor, company, permission)
        assert not config.companies[company].sample_posting
        assert config.connectors["quickbooks"].identity_sha256 == "0" * 64
        report = inspect(root, company)
        assert report["checks"]["company_file_exists"]
        assert report["checks"]["credentials_available"]
        assert not report["configuration_complete"]
        assert not report["company_identity_verified"]
        assert not report["live_connection_verified"]
        assert not (root / "state").exists()
        assert str(tmp_path) not in json.dumps(report)
        assert all(token not in json.dumps(report) for token in credentials.values())
        if os.name != "nt":
            assert root.stat().st_mode & 0o777 == 0o700
        bundles.append(credentials)
    assert not set(bundles[0].values()) & set(bundles[1].values())


def test_racing_initialization_cannot_overwrite_credentials(tmp_path):
    request = request_file(tmp_path)
    root = tmp_path / "bundle"

    def attempt():
        try:
            return initialize(request, root)["status"]
        except (BridgeError, FileExistsError):
            return "refused"

    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(lambda _: attempt(), range(2))) == ["created-unbound", "refused"]
    before = (root / "credentials.json").read_bytes()
    with pytest.raises(BridgeError):
        initialize(request, root)
    assert (root / "credentials.json").read_bytes() == before


def test_checkout_and_relative_destinations_rejected(tmp_path):
    request = request_file(tmp_path)
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / ".git").write_text("gitdir: private-worktree")
    for root in (repository / "bundle", "relative-bundle"):
        with pytest.raises(BridgeError):
            initialize(request, root)


def test_acl_failure_prevents_writing_secrets(tmp_path, monkeypatch):
    def deny(_):
        raise OSError("synthetic ACL failure")

    monkeypatch.setattr("kaydbooks_bridge.onboarding.restrict_directory", deny)
    root = tmp_path / "bundle"
    assert main(["init", "--request", str(request_file(tmp_path)), "--destination", str(root)]) == 2
    assert not list(root.iterdir())


def test_invalid_policy_has_no_credentials(tmp_path):
    request = request_file(tmp_path)
    raw = json.loads(request.read_text())
    raw["currency"] = "invalid"
    request.write_text(json.dumps(raw))
    root = tmp_path / "bundle"
    with pytest.raises(BridgeError):
        initialize(request, root)
    assert not (root / "credentials.json").exists()


def test_missing_file_and_credentials_are_pending_not_live_verified(tmp_path):
    root = tmp_path / "bundle"
    request = request_file(tmp_path)
    initialize(request, root)
    (tmp_path / "company-a.qbw").unlink()
    (root / "credentials.json").write_text("{}")
    report = inspect(root)
    assert {"company_file_exists", "credentials_available"} <= set(report["pending"])
    assert not report["live_connection_verified"]


def test_explicit_company_target_mismatch_rejected(tmp_path):
    root = tmp_path / "bundle"
    initialize(request_file(tmp_path), root)
    target = json.loads((root / "target.json").read_text())
    target["company_id"] = "company-b"
    (root / "target.json").write_text(json.dumps(target))
    with pytest.raises(BridgeError, match="must match"):
        inspect(root)


def test_check_never_loads_credentials_or_creates_state(tmp_path, monkeypatch, capsys):
    root = tmp_path / "bundle"
    initialize(request_file(tmp_path), root)
    monkeypatch.setenv("KAYDBOOKS_OPERATOR_SECRET", "original-environment-value")
    assert (
        main(
            [
                "check",
                "--config",
                str(root / "bridge-config.json"),
                "--company",
                "company-a",
                "--principal",
                "operator",
                "--connector",
                "quickbooks",
                "--target",
                str(root / "target.json"),
                "--credentials",
                str(root / "credentials.json"),
            ]
        )
        == 1
    )
    result = json.loads(capsys.readouterr().out)
    assert result["pending"]
    assert os.environ["KAYDBOOKS_OPERATOR_SECRET"] == "original-environment-value"
    assert not (root / "state").exists()


def test_malformed_json_does_not_echo_private_data(tmp_path, capsys):
    request = tmp_path / "request.json"
    request.write_text("SECRET-COMPANY-WITH-BROKEN-JSON")
    assert main(["init", "--request", str(request), "--destination", str(tmp_path / "bundle")]) == 2
    assert "SECRET-COMPANY" not in capsys.readouterr().err


def test_other_principal_secrets_are_not_required(tmp_path):
    root = tmp_path / "bundle"
    initialize(request_file(tmp_path), root)
    path = root / "bridge-config.json"
    config = json.loads(path.read_text())
    config["principals"]["separate-reviewer"] = {
        "token_env": "KAYDBOOKS_SEPARATE_REVIEWER_SECRET",
        "companies": {"company-a": ["read", "approve"]},
    }
    path.write_text(json.dumps(config))
    assert inspect(root)["checks"]["credentials_available"]


def test_revoked_read_and_reused_secrets_do_not_pass(tmp_path):
    root = tmp_path / "bundle"
    initialize(request_file(tmp_path), root)
    config_path = root / "bridge-config.json"
    config = json.loads(config_path.read_text())
    config["principals"]["operator"]["companies"]["company-a"] = []
    config_path.write_text(json.dumps(config))
    creds = root / "credentials.json"
    values = json.loads(creds.read_text())
    values["KAYDBOOKS_CONNECTOR_SECRET"] = values["KAYDBOOKS_OPERATOR_SECRET"]
    creds.write_text(json.dumps(values))
    report = inspect(root)
    assert {"principal_read_granted", "credentials_distinct"} <= set(report["pending"])


def test_unknown_connector_or_principal_is_rejected(tmp_path):
    root = tmp_path / "bundle"
    initialize(request_file(tmp_path), root)
    for principal, connector in (("unknown", "quickbooks"), ("operator", "unknown")):
        with pytest.raises(BridgeError, match="explicit principal"):
            inspect_setup(
                root / "bridge-config.json",
                "company-a",
                root / "target.json",
                principal=principal,
                connector_id=connector,
            )
