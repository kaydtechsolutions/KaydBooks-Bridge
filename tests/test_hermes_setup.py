import json
import os
import subprocess

import pytest

from kaydbooks_bridge.config import BridgeError
from kaydbooks_bridge.hermes_setup import TOOLS, generate
from kaydbooks_bridge.onboarding import initialize
from test_onboarding import request_file


@pytest.fixture
def setup_request(tmp_path):
    bundle = tmp_path / "company"
    onboarding = request_file(tmp_path)
    onboarding_request = json.loads(onboarding.read_text())
    onboarding_request["permissions"] = [
        "read",
        "prepare",
        "validate",
        "submit",
        "post-sample",
    ]
    onboarding.write_text(json.dumps(onboarding_request))
    initialize(onboarding, bundle)
    cfg = bundle / "bridge-config.json"
    raw = json.loads(cfg.read_text())
    raw["principals"]["reviewer"] = {
        "token_env": "KAYDBOOKS_REVIEWER_SECRET",
        "companies": {"company-a": ["approve"]},
    }
    cfg.write_text(json.dumps(raw))
    cred = bundle / "credentials.json"
    values = json.loads(cred.read_text())
    values["KAYDBOOKS_REVIEWER_SECRET"] = "reviewer-" + "s" * 40
    cred.write_text(json.dumps(values))
    python = tmp_path / "Runtime with spaces" / "python.exe"
    python.parent.mkdir()
    python.write_bytes(b"offline runtime path fixture")
    request = tmp_path / "hermes-request.json"
    request.write_text(
        json.dumps(
            {
                "config": str(cfg),
                "credentials": str(cred),
                "python": str(python),
                "company": "company-a",
                "operator": "operator",
                "reviewer": "reviewer",
                "ssh_host": "windows-bridge",
                "chat_id": "123456@lid",
                "sender_ids": ["123456@lid"],
            }
        )
    )
    return request, bundle


def test_generated_launchers_keep_credentials_separate_and_posting_disabled(
    setup_request, tmp_path
):
    request, bundle = setup_request
    original = {p.name: p.read_bytes() for p in bundle.iterdir() if p.is_file()}
    root = tmp_path / "Channel with spaces"
    result = generate(request, root)
    assert result["files"] == len(list(root.iterdir())) == 9
    assert not result["posting_changed"] and not result["outbound_enabled"]
    assert original == {p.name: p.read_bytes() for p in bundle.iterdir() if p.is_file()}
    assert not (bundle / "state").exists()
    tools = json.loads((root / "tools-credentials.json").read_text())
    channel = json.loads((root / "channel-credentials.json").read_text())
    assert set(tools) == {"KAYDBOOKS_OPERATOR_SECRET"}
    assert set(channel) == {"KAYDBOOKS_OPERATOR_SECRET", "KAYDBOOKS_REVIEWER_SECRET"}
    linux = json.loads((root / "linux-channel.json").read_text())
    assert not linux["allow_outbound"]
    assert not any(value in json.dumps(linux) for value in channel.values())
    assert json.loads((root / "windows-channel.json").read_text())["outbound_batch_ids"] == []
    mcp = json.loads((root / "hermes-mcp-fragment.json").read_text())["mcp_servers"]["kaydbooks"]
    assert mcp["tools"]["include"] == TOOLS
    assert mcp["args"][-1] == '"' + str(root / "start-tools.ps1") + '"'
    assert "StrictHostKeyChecking=yes" in mcp["args"]
    assert not any(value in json.dumps(result) for value in channel.values())
    if os.name == "nt":
        # Parse generated PowerShell without executing its runtime or contacting SSH.
        for file in root.glob("*.ps1"):
            script = (
                "$tokens=$null; $errors=$null; [void][System.Management.Automation.Language.Parser]::ParseFile('"
                + str(file).replace("'", "''")
                + "',[ref]$tokens,[ref]$errors); if ($errors.Count) { exit 1 }"
            )
            subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                check=True,
                capture_output=True,
            )
    with pytest.raises(BridgeError, match="new destination"):
        generate(request, root)
    assert json.loads((root / "tools-credentials.json").read_text()) == tools


@pytest.mark.parametrize(
    "field,value",
    [
        ("reviewer", "operator"),
        ("company", "unassigned-company"),
        ("ssh_host", "host;whoami"),
        ("chat_id", "123@g.us"),
        ("sender_ids", ["999@lid"]),
        ("sender_ids", []),
    ],
)
def test_invalid_binding_creates_no_credentials(setup_request, tmp_path, field, value):
    request, _ = setup_request
    raw = json.loads(request.read_text())
    raw[field] = value
    request.write_text(json.dumps(raw))
    root = tmp_path / "invalid"
    with pytest.raises(BridgeError):
        generate(request, root)
    assert not root.exists()


def test_reused_secret_and_shell_path_are_rejected(setup_request, tmp_path):
    request, bundle = setup_request
    with pytest.raises(BridgeError, match="remote-shell"):
        generate(request, tmp_path / "bad&path")
    cred = bundle / "credentials.json"
    raw = json.loads(cred.read_text())
    raw["KAYDBOOKS_REVIEWER_SECRET"] = raw["KAYDBOOKS_OPERATOR_SECRET"]
    cred.write_text(json.dumps(raw))
    with pytest.raises(BridgeError, match="distinct"):
        generate(request, tmp_path / "invalid")
    assert not (tmp_path / "invalid").exists()
