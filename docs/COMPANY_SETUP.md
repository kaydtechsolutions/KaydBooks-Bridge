# Configure your own company

KaydBooks Bridge code is reusable. Company names, file locations, identity claims,
master mappings, credentials and test authorizations belong in private configuration
outside every Git checkout. The project's sample-company qualification does not
authorize writes to another company's accounts.

## Create a private setup bundle

Install the package in a virtual environment; QuickBooks is unnecessary for this
offline step. Put a request JSON in an operator-controlled directory outside Git.
Replace these example values with your intended target and policy:

```json
{
  "target": {
    "company_id": "company-a",
    "company_name": "Your Company Name",
    "company_file": "C:\\Accounting\\YourCompany.qbw"
  },
  "currency": "USD",
  "max_total": "100.00"
}
```

`company_id` is a stable internal identifier, not the displayed QuickBooks name.
Currency and maximum total are operator policy; verify them against the company.

```powershell
python -m kaydbooks_bridge.onboarding init --request C:\BridgePrivate\request.json --destination C:\BridgePrivate\company-a
```

The installed `kaydbooks-bridge-setup` command is equivalent. The destination must
be new and its parent must already exist. The command refuses paths inside Git,
relative paths, symlinks and existing destinations. It restricts the new directory
to the current Windows operator (or POSIX mode 0700) before writing credentials.
If setup fails, inspect the retained directory and use a new destination; it never
deletes an existing directory or overwrites another user's configuration.

The resulting bundle contains:

- `bridge-config.json`: simulation mode, unconfirmed identity, read-only operator
  grant, approval required, placeholder aliases and no sample posting gate.
- `credentials.json`: two independently generated secrets, never printed.
- `target.json`: the operator's intended name and file path, not verified evidence.

This command creates no QuickBooks transactions, database, service, certificate,
QWC registration or global environment setting. It does not replace a running setup.
Each deployment can create its own bundle; existing multi-company configurations
remain supported. Do not reuse credentials or copy sample ListIDs to another company.

## Check configuration without connecting

```powershell
python -m kaydbooks_bridge.onboarding check --config C:\BridgePrivate\company-a\bridge-config.json --company company-a --principal operator --connector quickbooks --target C:\BridgePrivate\company-a\target.json --credentials C:\BridgePrivate\company-a\credentials.json
```

Exit code 0 means the listed configuration prerequisites are present; 1 lists
unfinished checks; 2 means invalid or inaccessible input. Results omit private
names, paths and secret values. The check does not load credentials into the
environment, create state, query QuickBooks or change grants. A file's existence
does not prove it is valid, currently open or the intended accounting company.
Likewise, a nonzero identity digest only means a binding is configured, not verified.
Checks use the explicitly selected principal and connector, so a deployment does
not need to combine every user's credentials into one file. Missing read permission,
missing credentials and identical operator/connector secrets are reported separately.

## Establish and qualify the connection

1. Open the intended company in QuickBooks and compare its name and file location
   with the private target. Native SDK helpers use the currently open company;
   `target.json` is setup intent, not an instruction to open or switch a QBW file.
   The live CompanyQuery fingerprint remains the runtime identity check.
2. Establish the private HTTPS/QBWC service using the
   [deployment runbook](DEPLOYMENT_QUALIFICATION.md) and
   [QBWC protocol](QBWC_DISCOVERY.md). Supply credentials through the documented
   private credential file/environment mechanism; never command-line password values.
3. Capture a company identity candidate and compare its claims with the intended
   company. The initializer uses CompanyName, LegalCompanyName and EIN; configure
   a different supported strong claim set if the company lacks an EIN. Keep the
   all-zero sentinel until the claims have been reviewed. The candidate exporter
   never changes the expected identity automatically.
4. Qualify a fixed read with the confirmed binding, following
   [direct SDK discovery](DIRECT_SDK.md) or the QBWC protocol. SDK discovery requires
   a confirmed binding; it cannot bootstrap an unknown identity. On the tested
   QBWC host, registration needed broader QuickBooks permission for AppLock metadata.
   The [M2 repair evidence](M2_QUALIFICATION.md#verified-repair-and-restart-qualification)
   documents this limitation; repeatedly importing read-only bootstrap variants is
   not a supported remedy. The Bridge query allowlist and QuickBooks app permission
   are separate controls.
5. Replace placeholder aliases and configure this company's own
   [account roles](ACCOUNT_ROLES.md) and [invoice masters](INVOICE_COMPATIBILITY.md)
   using verified reads. Grant only the operations assigned to each principal.
   Fresh master evidence and source review are still required to prepare and submit.
6. Run the supported read, permission, duplicate, interruption and
   [signed isolated restore](DEPLOYMENT_QUALIFICATION.md) checks for the deployment.

Setup completeness is not production readiness. Production posting remains
unavailable. A sample posting gate requires explicit operator authorization for
that sample, plus bounds and expiry; setup never creates it. Backups described in
this repository protect Bridge state/evidence, not the QuickBooks company database.
Maintain a separate QuickBooks backup procedure before accounting tests.
