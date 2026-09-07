# Install the Hermes data-entry system for multiple companies

Target: **KB v0.1.0**. The full upload, confirmation, posting and WhatsApp workflow
is still being implemented. The commands below install the current development
foundation; they do not turn on production posting or complete channel integration.
There is no published v0.1.0 installer yet.

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
can satisfy that installation boundary.

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
3. Generate and register the reviewed company-specific `.qwc` profile. Use stable
   registration identifiers and unique connector names; do not import the project's
   sample repair files for another company. The current general QWC generator is
   read-only qualification tooling, not finished write-enabled onboarding.
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

Retain the existing Hermes profile, model settings and WhatsApp session. Supply the
Linux host location, Windows connection address, and intended operator chat so the
private connection can be configured without putting them in Git. Bridge credentials
stay in the trusted Windows launcher; the agent must not receive an approver secret.

Hermes accepts stdio `command`/`args` MCP entries with tool filtering. The final
launcher must expose only this workflow's approved tools, disable parallel calls,
and use the same Windows company configuration and durable state as Web Connector.
Validate discovery with `hermes mcp test kaydbooks` after the entry is configured.
This is a connection check, not evidence of successful accounting or WhatsApp delivery.
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
