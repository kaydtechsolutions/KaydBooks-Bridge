"""Replace one Hermes MCP entry as the unprivileged Hermes account.

PyYAML is supplied by the Hermes runtime. Never execute that user-writable
runtime as root. Input contains paths and tool names, never a credential.
"""

import copy
import json
import os
import sys
import tempfile
import time
from pathlib import Path


def merge(data, entry):
    if not isinstance(data, dict):
        raise ValueError("Hermes profile must be a mapping")
    updated = copy.deepcopy(data)
    servers = updated.setdefault("mcp_servers", {})
    if not isinstance(servers, dict):
        raise ValueError("Hermes MCP servers must be a mapping")
    previous = servers.get("kaydbooks")
    if previous is not None:
        if not isinstance(previous, dict) or previous.get("command") not in {
            "/opt/kaydbooks/current/bin/kaydbooks-bridge-tools",
            "/opt/kaydbooks/current/bin/kaydbooks-bridge-remote-client",
        }:
            raise ValueError("custom KaydBooks MCP entry requires explicit migration")
        # Preserve a deliberately disabled integration and any narrower tool filter.
        if type(previous.get("enabled", True)) is not bool:
            raise ValueError("Hermes MCP enabled flag must be boolean")
        entry = copy.deepcopy(entry)
        entry["enabled"] = previous.get("enabled", True)
        prior_tools = previous.get("tools", {})
        if not isinstance(prior_tools, dict):
            raise ValueError("Hermes MCP tools must be a mapping")
        include = prior_tools.get("include")
        if include is not None:
            if not isinstance(include, list) or any(not isinstance(x, str) for x in include):
                raise ValueError("Hermes MCP tool filter must be a list of names")
            if not set(include) <= set(entry["tools"]["include"]):
                raise ValueError("existing tools need remote policy migration first")
            entry["tools"]["include"] = include
        if "exclude" in prior_tools:
            exclude = prior_tools["exclude"]
            if not isinstance(exclude, list) or any(not isinstance(x, str) for x in exclude):
                raise ValueError("Hermes MCP exclusions must be a list of names")
            entry["tools"]["exclude"] = exclude
    servers["kaydbooks"] = entry
    return updated


def update(path, entry, *, dry_run=False):
    import yaml

    path = Path(path)
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("Hermes profile path must not contain symlinks")
    if path.exists() and path.stat().st_size > 1_048_576:
        raise ValueError("Hermes profile exceeds size limit")
    original = path.read_bytes() if path.exists() else None
    try:
        data = yaml.safe_load(original) if original is not None else {}
    except yaml.YAMLError:
        raise ValueError("invalid Hermes profile YAML") from None
    updated = merge({} if data is None else data, entry)
    if dry_run or updated == data:
        return False
    encoded = yaml.safe_dump(updated, sort_keys=False).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if original is not None:
        backup = path.with_name(path.name + ".kb-backup-" + str(time.time_ns()))
        fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(original)
            stream.flush()
            os.fsync(stream.fileno())
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        # Do not overwrite a concurrent operator edit.
        if (path.read_bytes() if path.exists() else None) != original:
            raise ValueError("Hermes profile changed during migration")
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return True


def main():
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        raise SystemExit("Run the Hermes profile editor as hermes, never root")
    try:
        value = json.load(sys.stdin)
        changed = update(value["path"], value["entry"], dry_run=value.get("dry_run", False))
    except (ValueError, OSError, KeyError, TypeError):
        raise SystemExit("Hermes profile migration failed; existing profile retained") from None
    print(json.dumps({"profile_changed": changed}))


if __name__ == "__main__":
    main()
