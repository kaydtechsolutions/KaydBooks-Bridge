# Install KaydBooks Bridge from GitHub

The first-install wizard installs missing Linux packages, builds KB v0.2.0 from
this GitHub repository, generates private credentials and a company `.qwc`, and
checks the running HTTPS/MCP services. No Codex installation is required.
It starts with read-only permissions and no sample or production posting gates.

This is a **fresh-install** tool, not an upgrade or an accounting qualification.
Use [upgrade/rollback](UPGRADE_ROLLBACK.md) for an existing deployment. The previous
release's 39-gate evidence does not qualify a new machine or company.

## 1. Prerequisites

| Device | Required before starting | Installed by the setup |
|---|---|---|
| Linux server, VM, or existing LXC | Debian 13 or Ubuntu 24.04; running systemd; root/sudo; 2 CPU cores, 4 GiB RAM, 10 GiB free (20 GiB+ disk recommended); `/dev/net/tun`; working DNS, clock and outbound internet | Git, curl, CA certificates, Python/venv, SQLite, Caddy, sudo, Tailscale, KB and its Python server/MCP/intake dependencies |
| Windows QuickBooks PC | 64-bit Windows supported by your licensed QuickBooks edition; administrator access for installation; working internet; QuickBooks license and sample company | Tailscale through winget when available; runs supplied, signed Intuit QuickBooks/Web Connector installers interactively |
| Network/account | Tailscale account and ability to join both devices to the same tailnet; MagicDNS and HTTPS certificates enabled; tailnet rules permitting Windows to reach the server on TCP 443 | Tailscale Serve private HTTPS configuration |

Proxmox is optional. On a normal Linux server, run the same Linux commands directly.
For Proxmox, first create a dedicated Debian/Ubuntu guest with these resources, make
TUN available, then run the installer **inside the guest**, not on the Proxmox host.
Follow [Tailscale's LXC instructions](https://tailscale.com/docs/features/containers/lxc/lxc-unprivileged)
if the TUN check fails. The wizard does not create or resize Proxmox guests.

The installer needs HTTPS access to GitHub, PyPI and Tailscale plus your OS package
mirrors. Selected integrations also need their vendor endpoints. It never enables
Tailscale Funnel or public accounting listeners. Follow
[Intuit's installation requirements](https://quickbooks.intuit.com/learn-support/en-us/help-article/install-products/install-quickbooks-desktop/L3Fpsoqvj_US_en_US)
for your Windows/QuickBooks version; setup checks presence, not license entitlement
or every edition-specific Windows requirement. Intuit setup handles its prerequisites
and may require a reboot. Missing winget requires a manual official Tailscale install.

## 2. Install Linux

In a root shell on the fresh Linux machine:

```sh
apt-get update
apt-get install -y ca-certificates curl
curl --fail --location --proto '=https' --tlsv1.2 \
  https://raw.githubusercontent.com/kaydtechsolutions/KaydBooks-Bridge/main/deploy/bootstrap.sh \
  -o /root/kaydbooks-bootstrap.sh
sh /root/kaydbooks-bootstrap.sh
```

The wizard asks for a company ID, currency and components. If Tailscale requires
login, open its displayed URL in your browser and approve this server. Enable HTTPS
in the tailnet if Tailscale Serve requests it, then rerun the same installation.

For explicit options (example: Enterprise sample company with three AI clients):

```sh
sh /root/kaydbooks-bootstrap.sh --yes --company company-a --currency USD \
  --edition 8 --components core,chatgpt,claude,gemini
```

| Option | Meaning |
|---|---|
| `--company company-a` | Stable lowercase company ID; use letters, digits, `_` or `-`, starting with a letter, maximum 40 characters |
| `--currency USD` | Three uppercase letters; must match the intended company's currency |
| `--edition 8` | QBWC AuthFlags: Simple Start `1`, Pro `2`, Premier `4`, Enterprise `8`; `15` permits all listed editions |
| `--components core` | Core server, operator read credential, QWC and private HTTPS only; default |
| `chatgpt` | Dedicated read credential and remote source policy; account/tunnel steps remain below |
| `claude`, `gemini` | Dedicated read credentials, source policies and client JSON fragments |
| `hermes` | Installs compiler/build tools, libatomic1, ripgrep and ffmpeg as root, then installs the official Hermes runtime as its service user and configures local read MCP; model/WhatsApp login remains below |
| `composio` | Creates a disabled policy for later exact account/tool grants; no external credentials are inferred |
| `--yes` | Uses supplied/default choices; does not bypass account logins or company authorization |

All component names may be combined with commas. A fresh installation supports one
company. Additional companies require [company setup](COMPANY_SETUP.md), separate
credentials, QWC identities and explicit policies. Choosing AI components does not
automatically install their desktop clients or create third-party accounts.

For a read-only full prerequisite check from an existing clean GitHub checkout:

```sh
python3 deploy/setup.py --check
```

Add `--components core,hermes` to include Hermes system prerequisites in the check.

Or use `sh deploy/auto-install.sh --check`; it reports a missing Python without
installing it. The bootstrap's `--check` checks only bootstrap tools. No check mode
installs packages. Normal mode installs missing packages after host checks pass.

The exact source SHA is printed and saved in `/etc/kaydbooks/installer.json`.
To resume after interruption, use the **same SHA and options**:

```sh
KB_REF=PASTE_RECORDED_COMMIT_SHA sh /root/kaydbooks-bootstrap.sh \
  --yes --company company-a --currency USD --edition 8 --components core
```

Resume preserves secrets, QWC IDs, edited company binding and databases. A different
SHA, options, hostname or an existing unmanaged installation is refused. Do not delete
the marker to force adoption. The built wheel and SHA256 receipt are retained under
`/var/cache/kaydbooks/<source-sha>/` so retries use the same artifact.

## 3. Windows setup and QWC

The Linux result prints your exact `https://HOST.TAIL.ts.net` address and export path:
`/etc/kaydbooks/export/KaydBooks-company-a.qwc`. Copy **that QWC only** to a private
folder on the QuickBooks Windows PC using an administrator-controlled SFTP/USB path.
The `.qwc` contains no password. The export directory is root-only on Linux.

Open PowerShell as Administrator on the QuickBooks PC. Replace the example hostname:

```powershell
$kbSetup = Join-Path $env:USERPROFILE 'Downloads\kaydbooks-windows-setup.ps1'
Invoke-WebRequest 'https://raw.githubusercontent.com/kaydtechsolutions/KaydBooks-Bridge/main/deploy/windows-setup.ps1' -OutFile $kbSetup
Unblock-File -LiteralPath $kbSetup
& $kbSetup -ServerUrl 'https://HOST.TAIL.ts.net' -QwcPath "$env:USERPROFILE\Downloads\KaydBooks-company-a.qwc"
```

If your organization blocks local scripts, follow its execution-policy process.
Do not disable machine policy. If Intuit software is missing, rerun with your original
installers; the helper validates their Intuit signatures before launching them:

```powershell
& $kbSetup -ServerUrl 'https://HOST.TAIL.ts.net' `
  -QuickBooksInstaller 'C:\Installers\QuickBooks.exe' `
  -WebConnectorInstaller 'C:\Installers\QBWebConnector.msi' `
  -QwcPath "$env:USERPROFILE\Downloads\KaydBooks-company-a.qwc"
```

Use `-CheckOnly` for inspection without installation. `-WhatIf` previews installation
actions. The helper checks private HTTPS and rejects QWC endpoints pointing elsewhere.
Exit `1` means failed checks, `3` means user steps remain, and `0` means no pending steps.
Presence checks cannot establish a successful accounting connection.

1. Sign Windows into the same Tailscale network and rerun the helper.
2. Back up and open the intended **sample company** as QuickBooks Company Admin.
3. In Web Connector choose **Add an Application**, select the generated QWC, and
   check its application name and company URL. Authorize only that company.
4. Retrieve `KAYDBOOKS_CONNECTOR_SECRET` privately from Linux
   `/etc/kaydbooks/credentials.json`, then enter it in Web Connector's Password field.
   Never put it in Git, prompts or screenshots.
5. Keep **Auto-Run off**. Click **Update Selected** once to collect company identity.

The first update is deliberately blocked until its identity is bound. QBWC may request
write access because its registration stamps application metadata (`IsReadOnly=false`).
KB still has no accounting posting gate and every new principal has read permission only.

## 4. Confirm company identity

On Linux as root, export the discovery candidate:

```sh
KAYDBOOKS_CONFIG=/etc/kaydbooks/bridge-config.json \
KAYDBOOKS_QBWC_BINDING_CANDIDATE=/root/company-a-binding-candidate.json \
  /opt/kaydbooks/current/bin/kaydbooks-bridge-qbwc-config \
  export-binding-candidate --connector quickbooks-company-a
cat /root/company-a-binding-candidate.json
```

Compare every returned claim with **Company → My Company** in the sample company.
Only after they match, edit that connector's `identity_sha256` in
`/etc/kaydbooks/bridge-config.json` to the candidate digest. The default fields are
CompanyName, LegalCompanyName and EIN. If they are absent, follow
[QBWC discovery](QBWC_DISCOVERY.md) to choose supported claims including a strong field;
never bind a guessed or unrelated company.

```sh
systemctl restart kaydbooks-bridge kaydbooks-remote-mcp
```

Run **Update Selected** again. Expect 100% and `Last result: OK`. Restart the Bridge
and repeat once to confirm the registration survives. No accounting transaction should
be created by these discovery checks.

## 5. Connect selected clients

**Claude Code:** merge `/etc/kaydbooks/claude-client.json` into the client's MCP
configuration on a machine connected to Tailscale. Supply `KAYDBOOKS_CLAUDE_SECRET`
through that client's private environment. The JSON references the variable without
containing the credential. Start Claude Code and inspect its MCP connection.

**Gemini CLI:** merge `/etc/kaydbooks/gemini-client.json` into the client's settings,
supply `KAYDBOOKS_GEMINI_SECRET` privately, and inspect `/mcp` in Gemini CLI.
The fragment uses `httpUrl`, `trust: false` and an explicit read-tool list.
These configurations target **Claude Code and Gemini CLI**, not an assumed integration
with their consumer web apps. Follow the clients' current
[Claude Code MCP](https://code.claude.com/docs/en/mcp) and
[Gemini CLI MCP](https://geminicli.com/docs/tools/mcp-server/) documentation for placement.

**ChatGPT:** the installer creates `KAYDBOOKS_CHATGPT_SECRET` and the server policy.
Install the official OpenAI tunnel client and finish administrator enrollment using
[Remote MCP](REMOTE_MCP.md) and the
[OpenAI Secure MCP Tunnel guide](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels).
For a direct Linux server, use `https://YOUR_MAGICDNS_NAME/mcp` as the tunnel upstream;
the runbook's loopback HTTP example applies only when that hostname resolves locally
to `127.0.1.1`. Keep the MCP Bearer credential in the local restricted authorization
file. Run tunnel `doctor`, then create/select the tunnel app in the authorized ChatGPT
workspace. Neither tunnel credentials nor app publication are fabricated by setup.

**Hermes/WhatsApp:** when selected, runtime and MCP setup are automatic. Complete the
model login and WhatsApp pairing as the installed service user:

```sh
cd /var/lib/hermes
sudo -u hermes -H /var/lib/hermes/.hermes/hermes-agent/venv/bin/hermes setup
```

Configure only the intended WhatsApp identity/channel and its sender allowlist in the
default `/var/lib/hermes/.hermes` profile, following
[Hermes setup](https://hermes-agent.nousresearch.com/docs/getting-started/installation).
Then enable `hermes-gateway` and verify its connected state. The initial MCP entry only
permits catalog and batch status reads. Batch creation, trusted confirmations and the
accounting worker require the separate [Hermes channel setup](HERMES_CHANNEL.md);
the installer does not enable a posting worker.

**Composio:** follow [Composio setup](COMPOSIO.md) to connect your account, provide its
private API credential, and grant exact account/tool/version combinations. The generated
policy is disabled until reviewed. Source tools and company permissions must also be
explicitly granted; selecting Composio alone provides no external execution authority.

**Optional OCR:** Python intake libraries are installed, but the optional Node 22 and
local OCR model/runtime setup remains in [document extraction](DOCUMENT_EXTRACTION.md).
Until completed, scanned-document OCR is not ready. Typed/core workflows are separate.

For every AI client, verify a company catalog read and a wrong-company refusal. The
server advertises its full versioned tool contract; execution is constrained separately
by source policy and company permissions. Listing a tool does not authorize its use.

## 6. Final verification and troubleshooting

If an installation from commit `ef0715e` stopped during Hermes setup with
`failed to query metadata of symlink /root/.venv: Permission denied`, or asks for a
`sudo` password for `hermes` while installing Node/build tools, press Ctrl+C. Its
core services and credentials are already installed. That revision inherited the
root shell's directory and omitted Hermes system dependencies. New installers use
`/var/lib/hermes`, install OS dependencies as root, and run the vendor bootstrap
without a terminal or account-setup wizard. No service-account sudo grant is needed.

Resume that existing installation from the service home using its recorded commit
and **the same options you originally selected**. For the example selection below:

```sh
apt-get update
apt-get install -y build-essential libatomic1 python3-dev libffi-dev pkg-config ripgrep ffmpeg
cd /var/lib/hermes
KB_REF=ef0715e14e9fa9fccc1417527b0cf89b9a335a62 sh /root/kaydbooks-bootstrap.sh \
  --yes --company company-a --currency USD --edition 8 \
  --components core,chatgpt,hermes,composio
```

Pinning the original commit keeps resume validation and the cached wheel intact;
changing directory works around the old launch bug without resetting credentials,
company binding or installation metadata. Do not delete `installer.json` or give
the Hermes user access to `/root`.

On Linux:

```sh
/opt/kaydbooks/current/bin/python -m kaydbooks_bridge.installer --verify
kaydbooks-doctor
systemctl --no-pager status kaydbooks-bridge kaydbooks-remote-mcp caddy tailscaled
```

The installer verifier checks ready health responses, production posting disabled,
unknown-route refusal, unauthenticated MCP refusal and, for selected AI principals,
initialization, the tool contract, assigned-company reads, cross-company refusal and
dispatch refusal. Core-only installations explicitly skip authenticated AI checks.
The doctor checks service status and SQLite integrity; enabled optional services must
also pass. Neither command posts accounting or certifies QuickBooks readback.

If a service fails, inspect `journalctl -u SERVICE -n 80 --no-pager` locally. If HTTPS
fails, check Tailscale login, Serve status, certificate permission, DNS, tailnet access
rules and system time. If services and loopback health are ready but the MagicDNS URL
does not resolve, inspect `tailscale dns status` and `/etc/resolv.conf` before restarting
installation. Correct the resolver/MagicDNS configuration; a ready local service does
not prove private DNS is working. The verifier reports DNS failure explicitly and
stops before authenticated probes.

If Tailscale DNS is enabled but `/etc/resolv.conf` still lists public resolvers,
restart `tailscaled` and retry the HTTPS health URL. For a Proxmox LXC running its
own Tailscale client, prevent Proxmox from overwriting the working resolver again:

```sh
touch /etc/.pve-ignore.resolv.conf
systemctl restart tailscaled
cat /etc/resolv.conf
# Replace YOUR_MAGICDNS_NAME with this container's full Tailscale DNS name.
curl -fsS --max-time 15 https://YOUR_MAGICDNS_NAME/healthz
```

Run these commands inside the LXC. The ignore file is the
[documented Proxmox DNS workaround](https://tailscale.com/docs/reference/troubleshooting/containers/proxmox#resolvconf-within-lxc);
it leaves DNS management to the container. Do not edit Tailscale's generated resolver
file manually. Repeat the HTTPS check after the next container restart. Once health
works, resume a partial installation using its original commit and options above.

If credentials/company checks fail, review only the intended
company in `/etc/kaydbooks/bridge-config.json` and `/etc/kaydbooks/remote-policy.json`.
Do not loosen access rules merely to make a check green.

Completion has separate levels: Linux checks PASS; Windows/QWC binding PASS; selected
client connections PASS; and accounting qualification PASS. To test an actual bounded
sample write, first configure exact masters, separate submit/approval principals and
the explicit sample gates using [sample posting](SAMPLE_POSTING.md). Require independent
readback, duplicate blocking and fresh [deployment qualification](DEPLOYMENT_QUALIFICATION.md)
evidence. Production posting remains disabled in this release.
