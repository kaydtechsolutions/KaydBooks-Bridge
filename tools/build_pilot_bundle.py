"""Bundle a built wheel, Hermes plugin and setup guides without private state."""

import argparse
import hashlib
import json
import subprocess
import zipfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    wheel = args.wheel.resolve()
    if (
        not wheel.is_file()
        or wheel.suffix != ".whl"
        or not wheel.name.startswith("kaydbooks_bridge-")
    ):
        parser.error("a built KaydBooks wheel is required")
    files = {f"windows/{wheel.name}": wheel}
    for name in ("__init__.py", "client.py", "worker.py", "plugin.yaml"):
        files[f"hermes/kaydbooks/{name}"] = root / "integrations/hermes/kaydbooks" / name
    for name in (
        "INSTALL_HERMES_DATA_ENTRY.md",
        "HERMES_CHANNEL.md",
        "HERMES_TOOLS.md",
        "HERMES_DATA_ENTRY_PILOT.md",
        "COMPANY_SETUP.md",
        "DEPLOYMENT_QUALIFICATION.md",
        "QBWC_DISCOVERY.md",
        "DATA_ENTRY_READINESS.md",
        "QBWC_JOURNALS_CHECKS_TRANSFERS.md",
        "RELEASE_PLAN.md",
    ):
        files[f"docs/{name}"] = root / "docs" / name
    files["LICENSE"] = root / "LICENSE"
    files["README.md"] = root / "integrations/hermes/PILOT_BUNDLE.md"
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=root, text=True))
    contents = {name: path.read_bytes() for name, path in files.items()}
    manifest = {
        "source_commit": revision,
        "source_dirty": dirty,
        "development_candidate": True,
        "files": {name: hashlib.sha256(data).hexdigest() for name, data in contents.items()},
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Refuse overwrite so an already reviewed artifact cannot silently change.
    with zipfile.ZipFile(args.output, "x", zipfile.ZIP_DEFLATED) as bundle:
        for name, data in contents.items():
            bundle.writestr(name, data)
        bundle.writestr("manifest.json", json.dumps(manifest, indent=2) + "\n")
    with zipfile.ZipFile(args.output) as bundle:
        assert bundle.testzip() is None
        for name, digest in manifest["files"].items():
            assert hashlib.sha256(bundle.read(name)).hexdigest() == digest
    print(
        json.dumps(
            {
                "artifact": str(args.output.resolve()),
                "files": len(contents),
                **{
                    k: manifest[k]
                    for k in ("source_commit", "source_dirty", "development_candidate")
                },
            }
        )
    )


if __name__ == "__main__":
    main()
