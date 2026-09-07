# Install the Hermes data-entry system for multiple companies

Target: **KB v0.1.0**. The full upload, confirmation, posting and WhatsApp workflow
has passed controlled sample tests, and the per-company deployment walkthrough has
passed. Final clean-candidate installation and recovery qualification remains open. The commands below install
the current development candidate; they do not turn on production posting.
There is no published v0.1.0 installer yet.

A local bundle can be built with `tools/build_pilot_bundle.py`; it includes the
Windows wheel, separate Hermes plugin, setup guides and a SHA-256 manifest.
See [bundle instructions](../integrations/hermes/PILOT_BUNDLE.md). Its clean Windows
environment installation, module imports, capabilities command and dependency
consistency check passed. A signed snapshot/isolated restore retained 71 jobs,
the completed batch confirmation and both provider receipts, with valid audit,
database integrity and paused state. The restored copy never started a service.
These checks do not close the outstanding end-to-end interruption qualification.

## Where each component runs

| Computer | Components |
| --- | --- |
| Windows accounting computer | QuickBooks Desktop, QuickBooks Web Connector, Bridge service and private company state |
| Existing Linux Hermes host | Hermes Agent, its WhatsApp gateway and the configured connection to Windows Bridge tools |
| Operator's phone/chat | Uploads, review/confirmation and result messages through Hermes |

Web Connector connects to Bridge on Windows. Hermes connects to Bridge's tool
interface; the `/qbwc` SOAP endpoint is not an MCP endpoint. The current MCP adapter
uses stdio. A private remote stdio connection (for example a fixed Windows-side
launcher reached over SSH) must be configured and tested for the actual hosts.
Do not copy a live Windows SQLite database to Linux or create two independent
services dispatching the same company's jobs. No remote MCP URL is available merely
by installing the current package.

## 1. Install the development package on Windows

QuickBooks Desktop and Web Connector must already be installed. Use Python 3.12
and Git. Choose a new checkout folder; do not overwrite an existing installation.
Run in PowerShell:

```powershell
git clone --branch codex/foundation https://github.com/kaydtechsolutions/KaydBooks-Bridge.git C:\KaydBooks-Source
Set-Location C:\KaydBooks-Source
py -3.12 -m venv C:\KaydBooks-Runtime
& C:\KaydBooks-Runtime\Scripts\python.exe -m pip install '.[server,hermes,intake]'
& C:\KaydBooks-Runtime\Scripts\kaydbooks-bridge.exe capabilities
git rev-parse HEAD
```

Record the commit and package version. `codex/foundation` is the evolving PR branch,
not a stable release. Final pilot installation will use a verified, versioned artifact.
The Web Connector architecture does not require a separate developer SDK for clients;
legacy Hermes tools using native SDK reads still need migration before this workflow
can use those legacy tools without an SDK. The pilot's seven-tool allowlist uses
QBWC instead; see [reviewed channel setup](HERMES_CHANNEL.md).

## 2. Create one private company bundle per QBW file

Create `C:\BridgePrivate` outside the checkout. In that folder, create
`company-a-request.json` with your actual company name and QBW path:

```json
{
  "target": {
    "company_id": "company-a",
    "company_name": "Your first company",
    "company_file": "C:\\Accounting\\FirstCompany.qbw"
  },
  "currency": "USD",
  "max_total": "100.00"
}
```

These are examples, not fixed repository values. Use the company's actual currency
and your intended limit. Then run:

```powershell
& C:\KaydBooks-Runtime\Scripts\kaydbooks-bridge-setup.exe init --request C:\BridgePrivate\company-a-request.json --destination C:\BridgePrivate\company-a
& C:\KaydBooks-Runtime\Scripts\kaydbooks-bridge-setup.exe check --config C:\BridgePrivate\company-a\bridge-config.json --company company-a --principal operator --connector quickbooks --target C:\BridgePrivate\company-a\target.json --credentials C:\BridgePrivate\company-a\credentials.json
```

An initial check reporting missing prerequisites is expected: onboarding creates
private credentials and an unbound simulation configuration. It does not install
a service, create TLS certificates, register QWC or enable accounting writes.

Repeat with `company-b`, a new request and a new destination for each file.
Do not reuse company A's identity, ListIDs, credentials or database for company B.
The bundles are preparation inputs: a shared running Bridge deployment needs each
company and its unique principal/connector names deliberately added to its private
configuration. Copying several initializer outputs together is not a merge command.
See [company setup](COMPANY_SETUP.md) for the supported fields and checks.

## 3. Bind each file and register Web Connector

For each company, in turn:

1. Open the intended QBW file and compare its actual name and location with the
   private target. Back up that QuickBooks company separately from Bridge state.
2. Configure the private Bridge HTTPS service, certificate trust and connector
   credential using the [deployment guide](DEPLOYMENT_QUALIFICATION.md) and
   [QBWC protocol](QBWC_DISCOVERY.md). Keep the accounting endpoint private.
3. Generate and register the reviewed company-specific `.qwc` profile. Use schema 2,
   `access_mode: "bridge-gated"`, `is_read_only: false`, a unique connector name and
   unique stable OwnerID/FileID values. Generate it with
   `kaydbooks-bridge-qbwc-profile generate-qwc`. Never reuse another company's QWC.
   Bridge policy, approval, company binding, pause and posting gates still control
   every accounting write.
4. In Web Connector, click **Add an Application**, choose that company's QWC file,
   review the QuickBooks access prompt, and enter the privately generated connector
   password. Run **Update Selected** for a read-only identity check.
5. Compare the returned company identity, confirm its binding, and map the company's
   own customers, accounts, items and inventory sites using verified reads.
6. Qualify supported operations under that company's authorization before enabling
   them. Until production support and authorization are complete, keep real files
   read-only. Never relabel a production file as a sample to bypass the gate.

Start with one company at a time. Multi-company configuration does not prove
unattended switching between QBW files. Do not promise simultaneous posting to all
files; verify the actual QuickBooks/Web Connector session behavior for that host.

## 4. Connect the existing Linux Hermes installation

For an already configured company and separate reviewer, generate the private
connection files instead of writing the Windows launchers by hand. Create an
operator-controlled request outside Git:

```json
{
  "config": "C:\\BridgePrivate\\company-a\\bridge-config.json",
  "credentials": "C:\\BridgePrivate\\company-a\\credentials.json",
  "python": "C:\\KaydBooks-Runtime\\Scripts\\python.exe",
  "company": "company-a",
  "operator": "operator",
  "reviewer": "channel-reviewer",
  "ssh_host": "kaydbooks-windows",
  "chat_id": "123456789@lid",
  "sender_ids": ["123456789@lid"]
}
```

Use the actual native WhatsApp direct-chat identity, configured SSH host alias and
existing principal names. The reviewer must already have this company's approval
permission and a distinct credential; this command does not grant permissions.
The operator needs read, prepare, validate, submit and post-sample permissions for
the current sample pilot. Independent approval must be enabled.

```powershell
& C:\KaydBooks-Runtime\Scripts\kaydbooks-bridge-setup.exe hermes --request C:\BridgePrivate\hermes-request.json --destination C:\BridgePrivate\company-a-channel
```

The new directory has nine files: tools/channel launchers, separate tool/channel
credential files, signing credentials, Windows/Linux channel configuration, an MCP
fragment, and installation instructions. The tools credential file contains only
the selected operator token; the reviewer token stays on Windows. Keep the whole
directory private. Copy only `linux-channel.json` to the trusted Linux channel
service and restrict it to mode 0600. Merge the MCP fragment into the actual
operator profile without replacing unrelated settings. Set
`KAYDBOOKS_HERMES_CONFIG` to the Linux channel file for both gateway and worker.
Both generated channel configurations start with outbound delivery disabled.

Use the **physical paths visible to Windows SSH**. In packaged desktop environments,
a virtualized AppData path can point to a different directory outside the desktop
process. The generated launchers reference the existing config and state; they do
not copy databases, start services, register QWC or enable posting. Destination
reuse is rejected so an existing installation cannot be silently overwritten.

For separate company/channel bindings, generate separate directories and use
separate configured gateway profiles/workers. The current confirmation hook has
one company binding per profile. It does not automatically route one chat across
all company files. Verify each actual QBW connection separately.

Retain the existing Hermes profile, model settings and WhatsApp session. Supply the
Linux host location, Windows connection address, and intended operator chat so the
private connection can be configured without putting them in Git. Bridge credentials
stay in the trusted Windows launcher; the agent must not receive an approver secret.

Hermes accepts stdio `command`/`args` MCP entries with tool filtering. The final
launcher must expose only this workflow's approved tools, disable parallel calls,
and use the same Windows company configuration and durable state as Web Connector.
Validate discovery with `hermes mcp test kaydbooks` after the entry is configured.
This is a connection check, not evidence of successful accounting or WhatsApp delivery.
The actual pilot SSH/MCP connection is now qualified against the same Windows state;
overall v0.1.0 acceptance is **14/15 (93%)**. The final candidate/recovery check remains open.
See the [official Hermes MCP reference](https://hermes-agent.nousresearch.com/docs/reference/mcp-config-reference/)
and the [current Bridge tool inventory](HERMES_TOOLS.md).

If WhatsApp is already paired, reuse it. If it is not paired, Hermes documents
`hermes whatsapp` for pairing and `hermes gateway` for running its gateway. Identify
the operator's allowed account/chat and retain session credentials privately. See
the [official WhatsApp guide](https://hermes-agent.nousresearch.com/docs/user-guide/messaging/whatsapp/).
Pairing alone does not implement Bridge confirmation or result delivery.

## 5. Acceptance walkthrough before real use

In the authorized sample, upload a small batch, inspect the exact preview, confirm
it, let Web Connector process it, and compare every result with QuickBooks readback.
Check that the intended WhatsApp chat receives the same verified/held/rejected
counts. Repeat the same upload/confirmation and prove it creates no duplicates.
Test correction after preview, wrong-company selection, an interrupted connection
and a failed notification separately. Notification retries must never repost entries.

Record this walkthrough for each enabled entry type and company deployment. A
successful sample walkthrough does not authorize production data entry. The active
[release checklist](HERMES_DATA_ENTRY_PILOT.md) identifies unfinished integration.

## 6. Verify one isolated deployment

Create one private JSON request containing the paths to that company's config,
target, credentials, QWC and generated Hermes bundle, plus its company, connector,
operator and reviewer identifiers. Run:

```powershell
& C:\KaydBooks-Runtime\Scripts\kaydbooks-bridge-setup.exe qualify --request C:\BridgePrivate\company-a-walkthrough.json
```

The check is offline and prints booleans only. It validates company-file presence,
identity binding, distinct credentials and roles, the eight selected entry mappings
and gates, company-specific QWC identity, exact Hermes tool exposure, state/database
binding, audit continuity, retained QBWC connection evidence and paused posting.
It does not print private names, paths or secrets and performs no accounting write.
