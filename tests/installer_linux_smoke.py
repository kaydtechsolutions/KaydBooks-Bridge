"""Disposable GitHub runner only: install real packages/services and test resume.

Tailscale enrollment is substituted by a local TLS proxy; this is not a tailnet test.
Never run this on a developer workstation or an existing deployment.
"""

import http.client
import json
import os
import ssl
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from kaydbooks_bridge import installer

HOST = "kb-ci.fixture.ts.net"


class PrivateTLSFixture(BaseHTTPRequestHandler):
    def exchange(self):
        size = int(self.headers.get("Content-Length", "0"))
        assert 0 <= size <= 1_048_576
        connection = http.client.HTTPConnection("127.0.0.1", 8088, timeout=15)
        try:
            headers = {k: v for k, v in self.headers.items() if k.lower() != "connection"}
            connection.request(self.command, self.path, self.rfile.read(size), headers)
            result = connection.getresponse()
            data = result.read()
            self.send_response(result.status)
            self.send_header("Content-Type", result.getheader("Content-Type", "application/json"))
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        finally:
            connection.close()

    do_GET = exchange
    do_POST = exchange

    def log_message(self, *_):
        pass


def hermes_working_directory_smoke():
    """Launch a vendor-script substitute through real sudo from a private root cwd."""
    original_run, original_cwd = installer.run, Path.cwd()

    def command(*args, **kwargs):
        if args[0] == "curl" and "https://hermes-agent.nousresearch.com/install.sh" in args:
            Path(args[-1]).write_text(
                "#!/bin/sh\nset -eu\n"
                'test "$(id -un)" = hermes\n'
                'test "$HOME" = /var/lib/hermes\n'
                'test "$PWD" = /var/lib/hermes\n'
                "test -w .\n"
                'printf "PASS Hermes bootstrap runs in its service home\\n"\n'
            )
            return None
        return original_run(*args, **kwargs)

    installer.run = command
    os.chdir("/root")
    try:
        try:
            installer.install_hermes()
        except installer.InstallError as exc:
            # The substitute checks privilege/cwd isolation, not the vendor runtime.
            assert "runtime location differs" in str(exc)
        else:
            raise AssertionError("fixture unexpectedly found an installed Hermes runtime")
    finally:
        os.chdir(original_cwd)
        installer.run = original_run


def main():
    if os.environ.get("GITHUB_ACTIONS") != "true" or os.geteuid() != 0:
        raise SystemExit("Only run as root on a disposable GitHub Actions runner")
    assert not Path("/etc/kaydbooks").exists()
    original_run = installer.run
    original_subprocess = subprocess.run
    original_which = installer.shutil.which

    def command(*args, **kwargs):
        if args[0] == "tailscale":
            assert args[1] == "serve"
            return None
        if args == ("systemctl", "enable", "--now", "tailscaled"):
            return None
        return original_run(*args, **kwargs)

    def process(args, **kwargs):
        if args == ["tailscale", "status", "--json"]:
            return subprocess.CompletedProcess(
                args, 0, json.dumps({"BackendState": "Running", "Self": {"DNSName": HOST}})
            )
        return original_subprocess(args, **kwargs)

    installer.run = command
    subprocess.run = process
    installer.shutil.which = lambda name: (
        "/fixture/tailscale" if name == "tailscale" else original_which(name)
    )
    with tempfile.TemporaryDirectory(prefix="kb-ci-tls-") as directory:
        cert, key = Path(directory) / "cert.pem", Path(directory) / "key.pem"
        original_subprocess(
            [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-keyout",
                str(key),
                "-out",
                str(cert),
                "-days",
                "1",
                "-subj",
                f"/CN={HOST}",
                "-addext",
                f"subjectAltName=DNS:{HOST}",
            ],
            check=True,
            capture_output=True,
        )
        with Path("/etc/hosts").open("a") as hosts:
            hosts.write(f"\n127.0.0.1 {HOST}\n")
        os.environ["SSL_CERT_FILE"] = str(cert)
        server = ThreadingHTTPServer(("127.0.0.1", 443), PrivateTLSFixture)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            source = Path(__file__).resolve().parents[1]
            revision = installer.source_revision(source)
            settings = installer.options("company-a", "USD", 8, "core,claude,gemini,chatgpt")
            installer.apply_install(source, settings, revision, installer.PACKAGES)
            etc = Path("/etc/kaydbooks")
            config = json.loads((etc / "bridge-config.json").read_text())
            config["connectors"]["quickbooks-company-a"]["identity_sha256"] = "a" * 64
            (etc / "bridge-config.json").write_text(json.dumps(config))
            before = {
                name: (etc / name).read_bytes()
                for name in (
                    "bridge-config.json",
                    "credentials.json",
                    "initial/profile.json",
                    "export/KaydBooks-company-a.qwc",
                )
            }
            installer.apply_install(source, settings, revision, [])
            assert before == {name: (etc / name).read_bytes() for name in before}
            hermes_working_directory_smoke()
            original_run("sudo", "-u", "caddy", "test", "-w", "/var/log/caddy/kaydbooks-access.log")
            for service in ("caddy", "kaydbooks-bridge", "kaydbooks-remote-mcp"):
                original_run("systemctl", "is-active", service)
            print(
                "PASS installed services, HTTPS/MCP verification and credential/binding/QWC preservation"
            )
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    main()
