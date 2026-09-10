import copy
import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

from kaydbooks_bridge import hermes_profile, installer, service_isolation


@pytest.fixture
def entry():
    return {
        "command": "/opt/kaydbooks/current/bin/kaydbooks-bridge-remote-client",
        "args": ["--config", "/etc/kaydbooks-hermes/client.json"],
        "enabled": True,
        "supports_parallel_tool_calls": False,
        "tools": {
            "include": ["company_catalog_v1", "batch_status_v1"],
            "resources": False,
            "prompts": False,
        },
    }


def test_profile_preserves_enrollment_other_servers_and_narrower_access(tmp_path, entry):
    path = tmp_path / "config.yaml"
    original = {
        "model": {"provider": "openai-codex", "model": "existing-model"},
        "platforms": {"whatsapp": {"enabled": True, "allowed_users": ["existing-user"]}},
        "mcp_servers": {
            "other": {"command": "existing-command", "env": {"PRIVATE": "preserve"}},
            "kaydbooks": {
                "command": "/opt/kaydbooks/current/bin/kaydbooks-bridge-tools",
                "env": {"KAYDBOOKS_CONFIG": "/etc/kaydbooks/bridge-config.json"},
                "enabled": False,
                "tools": {"include": ["company_catalog_v1"], "exclude": ["batch_status_v1"]},
            },
        },
    }
    exact = yaml.safe_dump(original).encode()
    path.write_bytes(exact)
    assert hermes_profile.update(path, entry)
    changed = yaml.safe_load(path.read_text())
    assert changed["model"] == original["model"]
    assert changed["platforms"] == original["platforms"]
    assert changed["mcp_servers"]["other"] == original["mcp_servers"]["other"]
    kb = changed["mcp_servers"]["kaydbooks"]
    assert "env" not in kb
    assert not kb["enabled"]
    assert kb["tools"]["include"] == ["company_catalog_v1"]
    assert kb["tools"]["exclude"] == ["batch_status_v1"]
    (backup,) = list(tmp_path.glob("*.kb-backup-*"))
    assert backup.read_bytes() == exact
    assert not hermes_profile.update(path, entry)
    assert list(tmp_path.glob("*.kb-backup-*")) == [backup]
    if os.name != "nt":
        assert backup.stat().st_mode & 0o777 == 0o600
        assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    "old",
    [
        {"command": "custom-wrapper"},
        {
            "command": "/opt/kaydbooks/current/bin/kaydbooks-bridge-tools",
            "tools": {"include": ["local_only_operation"]},
        },
        {"command": "/opt/kaydbooks/current/bin/kaydbooks-bridge-tools", "enabled": "false"},
    ],
)
def test_unmigrated_customizations_leave_exact_profile_untouched(tmp_path, entry, old):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"mcp_servers": {"kaydbooks": old}}))
    exact = path.read_bytes()
    with pytest.raises(ValueError):
        hermes_profile.update(path, entry)
    assert path.read_bytes() == exact
    assert len(list(tmp_path.iterdir())) == 1


def test_profile_dry_run_and_failed_replace_preserve_original(tmp_path, entry, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text("model: existing\n")
    assert not hermes_profile.update(path, entry, dry_run=True)
    assert len(list(tmp_path.iterdir())) == 1

    def fail(*args):
        raise OSError("disk failure")

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError):
        hermes_profile.update(path, entry)
    assert path.read_text() == "model: existing\n"
    assert len(list(tmp_path.iterdir())) == 2  # original plus exact recovery backup


def test_profile_rejects_malformed_yaml_without_echoing_secrets(tmp_path, entry):
    path = tmp_path / "config.yaml"
    path.write_text("private-token: [secret-sensitive\n")
    with pytest.raises(ValueError, match="^invalid Hermes profile YAML$"):
        hermes_profile.update(path, entry)


def test_profile_rejects_concurrent_operator_edit(tmp_path, entry, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text("model: existing\n")
    original = hermes_profile.tempfile.NamedTemporaryFile

    def edited(*args, **kwargs):
        path.write_text("model: operator-edit\n")
        return original(*args, **kwargs)

    monkeypatch.setattr(hermes_profile.tempfile, "NamedTemporaryFile", edited)
    with pytest.raises(ValueError, match="changed during migration"):
        hermes_profile.update(path, entry)
    assert path.read_text() == "model: operator-edit\n"


def fixture_config(tmp_path):
    etc = tmp_path / "etc"
    etc.mkdir()
    for name, value in {
        "bridge-config.json": {"principals": {"whatsapp": {"token_env": "KAYDBOOKS_WHATSAPP"}}},
        "remote-policy.json": {
            "principals": {"whatsapp": {"source": "whatsapp", "tools": ["company_catalog_v1"]}}
        },
        "credentials.json": {"KAYDBOOKS_WHATSAPP": "w" * 48, "KAYDBOOKS_ADMIN": "a" * 48},
    }.items():
        (etc / name).write_text(json.dumps(value))
    return etc


def test_preparation_uses_only_scoped_token_and_unprivileged_profile_editor(tmp_path, monkeypatch):
    etc = fixture_config(tmp_path)
    scoped = tmp_path / "scoped"
    calls = []

    def run(*args, **kwargs):
        calls.append((args, kwargs))
        if args[0] == "install":
            Path(args[-1]).mkdir(exist_ok=True)

    monkeypatch.setattr(installer, "run", run)
    installer.configure_hermes_remote(
        Path("/runtime/bin/hermes"),
        tmp_path / "profile",
        "https://books.example",
        etc=etc,
        scoped=scoped,
        dry_run=True,
    )
    assert json.loads((scoped / "token.json").read_text()) == {"token": "w" * 48}
    value = json.loads((scoped / "client.json").read_text())
    assert value["url"] == "https://books.example/mcp"
    assert value["tools"] == ["company_catalog_v1"]
    probes = [args for args, _ in calls if "--check" in args]
    assert len(probes) == 1 and probes[0][:4] == ("sudo", "-u", "hermes", "-H")
    ((command, kwargs),) = [(args, kw) for args, kw in calls if "input" in kw]
    assert command[:4] == ("sudo", "-u", "hermes", "-H")
    assert "-I" in command
    assert json.loads(kwargs["input"])["dry_run"] is True
    assert "a" * 48 not in str(calls) and "w" * 48 not in str(calls)


def test_failed_authenticated_probe_never_edits_profile(tmp_path, monkeypatch):
    etc = fixture_config(tmp_path)
    calls = []

    def run(*args, **kwargs):
        calls.append((args, kwargs))
        if "--check" in args:
            raise subprocess.CalledProcessError(1, args)

    monkeypatch.setattr(installer, "run", run)
    with pytest.raises(subprocess.CalledProcessError):
        installer.configure_hermes_remote(
            Path("/runtime/bin/hermes"),
            tmp_path / "profile",
            "https://books.example",
            etc=etc,
            scoped=tmp_path / "scoped",
        )
    assert not any("input" in kwargs for _, kwargs in calls)


def test_private_replace_retains_exact_backup_on_failure(tmp_path, monkeypatch):
    path = tmp_path / "credentials.json"
    path.write_text('{"token": "previous-private-value"}\n')
    exact = path.read_bytes()
    monkeypatch.setattr(installer, "run", lambda *args, **kwargs: None)
    original = copy.copy(os.replace)

    def fail(*args):
        raise OSError("disk failure")

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError):
        installer.replace_private(path, {"token": "replacement-value"})
    assert path.read_bytes() == exact
    assert not list(tmp_path.glob("*.new-*"))
    assert next(tmp_path.glob("*.backup-*")).read_bytes() == exact
    monkeypatch.setattr(os, "replace", original)
    installer.replace_private(path, {"token": "replacement-value"})
    assert json.loads(path.read_text())["token"] == "replacement-value"


def test_gateway_override_removes_inherited_bridge_access():
    value = service_isolation.override_text(Path("/var/lib/hermes/.hermes/profiles/existing"))
    assert "EnvironmentFile=\n" in value
    assert "SupplementaryGroups=\n" in value
    assert "InaccessiblePaths=/etc/kaydbooks /var/lib/kaydbooks /var/log/kaydbooks" in value
    assert "WorkingDirectory=/var/lib/hermes/.hermes/profiles/existing\n" in value


@pytest.mark.parametrize(
    "value",
    ["/root", "/var/lib/hermes/.hermes/../outside", "/var/lib/hermes/.hermes/a\nExecStart=/bin/sh"],
)
def test_profile_paths_reject_escaping_or_unit_injection(value):
    with pytest.raises(installer.InstallError):
        service_isolation.profile_path(value)


def migration_fixture(tmp_path, monkeypatch):
    calls = []
    state = {"gateway": "active", "worker": "inactive", "enabled": "disabled"}
    path_type = Path

    def mapped(value):
        return tmp_path / str(value).lstrip("/")

    runtime = mapped("/var/lib/hermes/.hermes/hermes-agent/venv/bin/hermes")
    runtime.parent.mkdir(parents=True)
    runtime.touch()
    monkeypatch.setattr(service_isolation, "Path", mapped)
    monkeypatch.setattr(service_isolation, "profile_path", lambda p: path_type(p))
    monkeypatch.setattr(service_isolation.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(
        service_isolation,
        "service_state",
        lambda unit, option="is-active": (
            state["enabled"]
            if option == "is-enabled"
            else state["worker"]
            if "worker" in unit
            else state["gateway"]
        ),
    )

    def run(*args, **kwargs):
        calls.append(args)
        if args[:2] == ("systemctl", "stop"):
            state["gateway"] = "inactive"
        if args[:2] == ("systemctl", "start"):
            state["gateway"] = "active"

    def write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value if isinstance(value, str) else json.dumps(value))

    monkeypatch.setattr(service_isolation, "run", run)
    monkeypatch.setattr(service_isolation, "replace_private", write)
    monkeypatch.setattr(
        service_isolation,
        "configure_hermes_remote",
        lambda *a, **kw: calls.append(("configure", kw.get("dry_run", False))),
    )
    monkeypatch.setattr(
        service_isolation, "remove_bridge_group", lambda u: calls.append(("remove", u))
    )
    monkeypatch.setattr(service_isolation, "verify_user", lambda u: calls.append(("verify", u)))
    return state, calls, mapped("/etc/kaydbooks/isolation-hermes.json")


def test_interrupted_migration_resumes_and_restores_previously_running_gateway(
    tmp_path, monkeypatch
):
    state, calls, journal = migration_fixture(tmp_path, monkeypatch)

    def failure(user):
        raise installer.InstallError("filesystem access still present")

    monkeypatch.setattr(service_isolation, "verify_user", failure)
    with pytest.raises(installer.InstallError):
        service_isolation.migrate("/var/lib/hermes/.hermes", "https://books.example")
    assert state["gateway"] == "inactive"
    intent = json.loads(journal.read_text())
    assert intent["restart"] and not intent["complete"]
    assert calls.index(("configure", True)) < calls.index(
        ("systemctl", "stop", "hermes-gateway.service")
    )
    assert ("systemctl", "start", "hermes-gateway.service") not in calls
    monkeypatch.setattr(service_isolation, "verify_user", lambda u: None)
    service_isolation.migrate("/var/lib/hermes/.hermes", "https://books.example")
    assert state["gateway"] == "active"
    assert json.loads(journal.read_text())["complete"]


@pytest.mark.parametrize("worker,enabled", [("active", "disabled"), ("inactive", "enabled")])
def test_active_or_enabled_worker_blocks_before_any_mutation(
    tmp_path, monkeypatch, worker, enabled
):
    state, calls, journal = migration_fixture(tmp_path, monkeypatch)
    state.update(worker=worker, enabled=enabled)
    with pytest.raises(installer.InstallError, match="legacy worker"):
        service_isolation.migrate("/var/lib/hermes/.hermes", "https://books.example")
    assert not calls and not journal.exists()


def test_migration_does_not_activate_previously_inactive_gateway(tmp_path, monkeypatch):
    state, calls, journal = migration_fixture(tmp_path, monkeypatch)
    state["gateway"] = "inactive"
    service_isolation.migrate("/var/lib/hermes/.hermes", "https://books.example")
    assert state["gateway"] == "inactive"
    assert ("systemctl", "start", "hermes-gateway.service") not in calls
    assert json.loads(journal.read_text())["complete"]
