# KaydBooks Bridge v0.2.0 deployment

v0.2.0 uses one fresh, unprivileged Linux LXC for KaydBooks Bridge, Remote MCP and
the WhatsApp-only Hermes worker. Codex, QuickBooks Desktop and Web Connector run on
the same physical Windows accounting computer. Proxmox is a separate physical server;
the qualified operator path reaches it through the existing `kaydbooks-proxmox` SSH
alias and reaches the KaydBooks LXC through `kaydbooks-lxc`. The Windows computer is
not a Proxmox VM.

```text
Physical Windows: Codex + QuickBooks + QBWC
          |
          | HTTPS 443 over Tailscale
          v
Tailscale Serve -> Caddy 127.0.0.1:8088
                    |-- Bridge/QBWC 127.0.0.1:8080
                    `-- Remote MCP 127.0.0.1:8000/mcp

Separate physical Proxmox host -- unprivileged KaydBooks Linux LXC
```

## Install

Create a Debian 13 or Ubuntu 24.04 unprivileged LXC with at least two CPU cores,
4 GiB RAM and 20 GiB disk. Enable `/dev/net/tun`, install Tailscale from its signed
package repository, join the intended tailnet and confirm the final MagicDNS name.

Build the wheel on a trusted workstation, transfer the wheel and `deploy/` directory,
then verify the exact SHA-256 during installation:

```sh
sha256sum kaydbooks_bridge-0.2.0-py3-none-any.whl
sudo sh deploy/install.sh kaydbooks_bridge-0.2.0-py3-none-any.whl EXPECTED_SHA256
```

Place private `bridge-config.json`, `credentials.json`, `remote-policy.json` and
`composio-policy.json` in `/etc/kaydbooks`. Keep them outside Git, owned by
`root:kaydbooks`, mode `0640`. Replace the hostname placeholders in
`/etc/kaydbooks/bridge.env`, validate Caddy, and start the services:

```sh
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl enable --now tailscaled caddy kaydbooks-bridge kaydbooks-remote-mcp \
  hermes-gateway kaydbooks-hermes-worker
sudo tailscale serve --bg --https=443 http://127.0.0.1:8088
sudo /usr/local/sbin/kaydbooks-doctor
```

The hardened Caddy configuration disables its admin API. After a validated Caddyfile
change, use `sudo systemctl restart caddy`; `systemctl reload caddy` is unavailable by
design.

Only loopback ports 8000, 8080 and 8088 may listen. Tailscale owns private port 443.
An unknown Caddy path must return 404 and unauthenticated `/mcp` must return 401.
Use the optional outbound-only OpenAI Secure MCP Tunnel unit documented in
`REMOTE_MCP.md` for ChatGPT developer-mode testing. Leave it disabled until a private
tunnel ID and runtime key have been created through the signed-in Platform account.

## Hermes profile migration

Install or restore the approved Hermes Agent runtime at
`/usr/local/lib/hermes-agent`, then retain the existing paired WhatsApp session in one
profile owned by the dedicated `hermes` account. The supplied systemd unit selects the
`osman-khalid-agent` profile. Change both `HERMES_HOME` and `WorkingDirectory` together
if a different approved profile is used. Keep every other Hermes messaging platform
disabled.

Hermes filters the environment inherited by stdio MCP subprocesses. Configure the
profile's `mcp_servers.kaydbooks.env` block with the KaydBooks config path, credentials
path, token environment-variable name and a `${KAYDBOOKS_HERMES_SAMPLE_SECRET}`
reference. Put that variable's value in `/etc/kaydbooks/hermes-mcp.env`, owned by
`root:hermes` with mode `0640`; never commit that file. The optional systemd
`EnvironmentFile` entry loads it only for the gateway process.

The Hermes-managed Node runtime must resolve at `$HERMES_HOME/node`. A migrated profile
may use a profile-local symlink to a root-owned preserved runtime elsewhere under
`/var/lib/hermes`. Keep the paired session in the profile path recorded by
`platforms.whatsapp.extra.session_path`, and verify that the Node bridge listens only on
`127.0.0.1:3000`. Give the `hermes` account access to KaydBooks state through the
`kaydbooks` supplementary group; state directories use mode `2770`, not world access.
The doctor checks the WhatsApp health status and the persistent local KaydBooks MCP
child without printing channel identities or credentials.

## Company onboarding

Create a private config and one connector per company. Give each connector a distinct
username, secret, identity digest and callback path `/qbwc/<company-id>`. Generate one
stable `.qwc` file per company with that path. Import each file into Web Connector on
the physical Windows computer while the correct QuickBooks company is open. Complete
read-only CompanyRet binding before enabling any bounded sample gate.

New principals default to `read`. Use explicit role grants for ordinary work. A
designated owner may activate unrestricted company access only with a reason, a reviewed
config revision and an expiry of eight hours or less; each use is appended to that
company's audit chain.

## Qualification

Run the Linux doctor, authenticated MCP initialize/tools checks, per-company wrong-route
tests, a Web Connector read-only cycle, restart recovery, backup and isolated restore.
An accounting write is qualified only through the documented bounded sample gate,
followed by independent QuickBooks readback and duplicate-action refusal. Record results
in `docs/v020-acceptance.json`; do not infer a pass from implementation alone.
