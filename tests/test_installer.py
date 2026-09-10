"""First-install safety, credential persistence and real configuration compatibility."""

import json
import os
from pathlib import Path

import pytest

from kaydbooks_bridge.config import Config
from kaydbooks_bridge.deployment import QWCProfile, load_secret_file
from kaydbooks_bridge.installer import (
    InstallError,
    claim_install,
    client_config,
    options,
    private_write,
    provision,
    valid_hostname,
)
from kaydbooks_bridge.remote_mcp import load_policy


@pytest.fixture
def bundle(tmp_path):
    cfg = options("company-a", "USD", 8, "core,chatgpt,claude,gemini,hermes,composio")
    etc = tmp_path / "etc"
    claim_install(etc, cfg, "a" * 40)
    return provision(etc, tmp_path / "state", cfg, "books.example.ts.net"), cfg


def test_generated_bundle_loads_and_grants_only_reads(bundle, monkeypatch):
    path, _ = bundle
    config = Config.load(path / "bridge-config.json")
    credentials = json.loads((path / "credentials.json").read_text())
    for key in credentials:
        monkeypatch.setenv(key, "placeholder")
    load_secret_file(path / "credentials.json")
    assert len(set(credentials.values())) == len(credentials)
    for name, principal in config.principals.items():
        token = os.environ[principal["token_env"]]
        assert config.authenticate(token) == name
        config.authorize(name, "company-a", "read")
        for forbidden in ("approve", "submit", "post-sample", "manage-users"):
            with pytest.raises(ValueError):
                config.authorize(name, "company-a", forbidden)
    assert config.connectors["quickbooks-company-a"].identity_sha256 == "0" * 64
    assert not config.companies["company-a"].sample_posting
    policy = load_policy(path / "remote-policy.json")
    assert {r["source"] for r in policy["principals"].values()} == {
        "claude",
        "gemini",
        "chatgpt",
        "whatsapp",
    }
    profile = QWCProfile.load(path / "profile.json")
    assert profile.username == "quickbooks-company-a"
    assert profile.endpoint_url == "https://books.example.ts.net/qbwc/company-a"
    assert all(value not in profile.render() for value in credentials.values())


def test_rerun_preserves_ids_secrets_and_edited_binding(bundle):
    path, cfg = bundle
    before = {f.name: f.read_bytes() for f in path.iterdir()}
    claim_install(path.parent, cfg, "a" * 40)
    assert provision(path.parent, path.parent.parent / "state", cfg, "books.example.ts.net") == path
    assert before == {f.name: f.read_bytes() for f in path.iterdir()}
    with pytest.raises(InstallError, match="hostname changed"):
        provision(path.parent, path.parent.parent / "state", cfg, "other.example.ts.net")
    with pytest.raises(InstallError, match="differs"):
        claim_install(path.parent, cfg, "b" * 40)
    with pytest.raises(InstallError, match="differs"):
        claim_install(path.parent, {**cfg, "company": "company-b"}, "a" * 40)
    assert before == {f.name: f.read_bytes() for f in path.iterdir()}


def test_existing_unmanaged_install_is_never_adopted(tmp_path):
    etc = tmp_path / "etc"
    etc.mkdir()
    (etc / "credentials.json").write_text("retained")
    with pytest.raises(InstallError, match="existing private"):
        claim_install(etc, options("company-a", "USD", 8, "core"), "a" * 40)
    assert (etc / "credentials.json").read_text() == "retained"


def test_exclusive_secret_creation(tmp_path):
    path = tmp_path / "secret"
    private_write(path, "original")
    with pytest.raises(FileExistsError):
        private_write(path, "replacement")
    assert path.read_text() == "original"
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    "company,currency,edition,components",
    [
        ("../escape", "USD", 8, "core"),
        ("company-a", "USD\nBAD=1", 8, "core"),
        ("a" * 41, "USD", 8, "core"),
        ("company-a", "USD", 16, "core"),
        ("company-a", "USD", 8, "core,shell"),
    ],
)
def test_invalid_input_before_mutation(company, currency, edition, components):
    with pytest.raises(InstallError):
        options(company, currency, edition, components)


@pytest.mark.parametrize(
    "host",
    [
        "localhost",
        "evil.com",
        "host.tail.ts.net:443",
        "host.tail.ts.net\nBAD=1",
        "http://host.tail.ts.net",
    ],
)
def test_bad_hostname(host):
    with pytest.raises(InstallError):
        valid_hostname(host)


def test_client_fragments_reference_secrets_without_including_them():
    for client in ("claude", "gemini"):
        config = client_config(client, "books.example.ts.net")["mcpServers"]["kaydbooks"]
        assert (
            config["headers"]["Authorization"] == f"Bearer ${{KAYDBOOKS_{client.upper()}_SECRET}}"
        )
    assert (
        client_config("gemini", "books.example.ts.net")["mcpServers"]["kaydbooks"]["trust"] is False
    )


def test_core_only_has_no_remote_access(tmp_path):
    cfg = options("company-a", "USD", 8, "core")
    etc = tmp_path / "etc"
    claim_install(etc, cfg, "a" * 40)
    path = provision(etc, tmp_path / "state", cfg, "books.example.ts.net")
    assert load_policy(path / "remote-policy.json")["principals"] == {}


def test_symlink_private_root_refused(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(real, target_is_directory=True)
    except OSError:
        pytest.skip("symlink permission unavailable")
    with pytest.raises(InstallError):
        claim_install(link, options("company-a", "USD", 8, "core"), "a" * 40)
    assert list(real.iterdir()) == []


def test_shell_syntax():
    import shutil
    import subprocess

    shell = shutil.which("bash")
    if shell is None:
        pytest.skip("bash not installed")
    source = Path(__file__).resolve().parents[1]
    for script in ("deploy/auto-install.sh", "deploy/bootstrap.sh"):
        subprocess.run([shell, "-n", script], cwd=source, check=True)


def test_generated_principals_pass_real_mcp_boundary(bundle, monkeypatch, capsys):
    from fastapi.testclient import TestClient

    from kaydbooks_bridge import installer
    from kaydbooks_bridge.remote_mcp import create_app

    path, _ = bundle
    credentials = json.loads((path / "credentials.json").read_text())
    for key, value in credentials.items():
        monkeypatch.setenv(key, value)
    base = "https://books.example.ts.net"
    app = create_app(path / "bridge-config.json", path / "remote-policy.json", base)
    with TestClient(app, base_url=base) as client:

        def request(url, body=None, token=None):
            # Caddy owns these routes; the actual MCP app owns everything below.
            if url.endswith("/healthz"):
                return 200, b'{"status":"ready","live_posting":false}'
            if url.endswith("/not-a-kb-route"):
                return 404, b""
            headers = {"Accept": "application/json, text/event-stream"}
            if token:
                headers["Authorization"] = f"Bearer {token}"
            response = (
                client.get(url, headers=headers)
                if body is None
                else client.post(url, json=body, headers=headers)
            )
            return response.status_code, response.content

        monkeypatch.setattr(installer, "request", request)
        assert installer.verify(base, path)
    output = capsys.readouterr().out
    for name in ("chatgpt", "claude", "gemini", "whatsapp"):
        assert f"PASS {name}: assigned read" in output
        assert f"PASS {name}: wrong-company denial" in output
        assert f"PASS {name}: dispatch denied" in output
    assert all(secret not in output for secret in credentials.values())


def test_verification_rejects_unready_health_and_enabled_posting(tmp_path, monkeypatch):
    from kaydbooks_bridge import installer

    cfg = options("company-a", "USD", 8, "core")
    etc = tmp_path / "etc"
    claim_install(etc, cfg, "a" * 40)
    path = provision(etc, tmp_path / "state", cfg, "books.example.ts.net")

    def bad_health(url, body=None, token=None):
        if "health" in url:
            return 200, b'{"status":"broken","live_posting":true}'
        return (401 if url.endswith("/mcp") else 404), b"{}"

    monkeypatch.setattr(installer, "request", bad_health)
    assert not installer.verify("https://books.example.ts.net", path)


def test_interrupted_bundle_never_publishes_partial_secrets(tmp_path, monkeypatch):
    from kaydbooks_bridge import installer

    etc = tmp_path / "etc"
    cfg = options("company-a", "USD", 8, "core")
    claim_install(etc, cfg, "a" * 40)
    original = installer.private_write

    def interrupted(path, value):
        if path.name == "credentials.json":
            raise OSError("simulated disk error")
        original(path, value)

    monkeypatch.setattr(installer, "private_write", interrupted)
    with pytest.raises(OSError):
        provision(etc, tmp_path / "state", cfg, "books.example.ts.net")
    assert sorted(p.name for p in etc.iterdir()) == ["installer.json"]
    monkeypatch.setattr(installer, "private_write", original)
    assert (
        provision(etc, tmp_path / "state", cfg, "books.example.ts.net") / "credentials.json"
    ).is_file()


def test_hermes_bootstrap_does_not_inherit_root_working_directory(tmp_path, monkeypatch):
    from kaydbooks_bridge import installer

    caller = tmp_path / "private-root"
    caller.mkdir()
    monkeypatch.chdir(caller)
    calls = []

    def command(*args, **kwargs):
        calls.append((args, kwargs))
        if args[0] == "curl":
            Path(args[-1]).write_text("#!/bin/sh\nexit 0\n")

    monkeypatch.setattr(installer, "run", command)
    monkeypatch.setattr(installer, "missing_packages", lambda packages: list(packages))
    # The stub checks launch isolation, not the third-party runtime installation.
    with pytest.raises(InstallError, match="runtime location differs"):
        installer.install_hermes()
    (sudo,) = [(args, kwargs) for args, kwargs in calls if args[0] == "sudo"]
    assert sudo[0][:5] == ("sudo", "-u", "hermes", "-H", "bash")
    assert sudo[1]["cwd"] == "/var/lib/hermes"
    assert sudo[1]["stdin"] == installer.subprocess.DEVNULL
    assert sudo[1]["start_new_session"] is True
    assert "--skip-setup" in sudo[0]
    install_index = next(
        i for i, (args, _) in enumerate(calls) if args[:2] == ("apt-get", "install")
    )
    sudo_index = next(i for i, (args, _) in enumerate(calls) if args[0] == "sudo")
    assert install_index < sudo_index
    assert set(installer.HERMES_PACKAGES) <= set(calls[install_index][0])
    assert Path.cwd() == caller


def test_missing_packages_checks_installed_status_not_just_package_presence(monkeypatch):
    from kaydbooks_bridge import installer

    monkeypatch.setattr(installer.shutil, "which", lambda name: "/usr/bin/dpkg-query")
    statuses = {
        "build-essential": (0, "install ok installed"),
        "libatomic1": (0, "deinstall ok config-files"),
        "ffmpeg": (1, ""),
    }

    def query(args, **kwargs):
        code, output = statuses[args[-1]]
        return installer.subprocess.CompletedProcess(args, code, output)

    monkeypatch.setattr(installer.subprocess, "run", query)
    assert installer.missing_packages(list(statuses)) == ["libatomic1", "ffmpeg"]


@pytest.mark.parametrize("components", ["core", "core,hermes"])
def test_check_includes_selected_component_dependencies(tmp_path, monkeypatch, components):
    from kaydbooks_bridge import installer

    checked = []

    def inspect(packages):
        checked.extend(packages)
        return []

    monkeypatch.setattr(installer, "missing_packages", inspect)
    installer.preflight(tmp_path, components=components)
    assert set(installer.PACKAGES) <= set(checked)
    assert ("build-essential" in checked) == ("hermes" in components)
