# M2 QuickBooks qualification runbook

Status: host and service prerequisites verified; real QuickBooks callback pending.
Live posting is disabled. The qualification service can return only a correlated
`HostQueryRq` and `CompanyQueryRq` after an operator-confirmed binding is configured.

## Verified staging prerequisites

The qualification host has QuickBooks Enterprise 2024 release 21 and QuickBooks Web
Connector 34 installed and running in the current Windows session. The current open
QuickBooks window did not identify itself as a sample or test company, so no connector
was imported and no QuickBooks callback was initiated against it.

The private stage is under `%LOCALAPPDATA%\KaydBooksBridge\M2Qualification`, outside
Git, with an inherited-access-disabled ACL for the current Windows operator. It contains:

- generated connector credentials in `credentials.json`;
- simulation-only Bridge configuration with an unconfirmed all-zero identity sentinel;
- stable OwnerID/FileID and an Enterprise-only `AuthFlags` value in `qwc-profile.json`;
- `KaydBooks-Bridge-M2-ReadOnly.qwc`, which points to the local HTTPS endpoint;
- a 30-day localhost TLS leaf certificate and private key; the leaf certificate is in
  the current user's trusted root store;
- `start-service.ps1`, which starts the service on `https://localhost:8443`.

The endpoint passed a Windows trust-chain HTTPS request, health check, WSDL fetch and a
manual authenticate/close callback probe. That probe did not involve QuickBooks and is
not a real integration test. The private configuration uses no company-file path and
never treats one as identity.

The initial certificate-generation command produced a CA-capable localhost certificate.
The service now uses a separately generated, trusted `CA:FALSE` server certificate.
Automatic review prevented deletion of the superseded trusted certificate. Before the
connector import, the Windows operator must open
`superseded-cert-cleanup.json`, match its exact thumbprint in the current user's Trusted
Root store, remove only that superseded certificate, and retain the active v2 certificate.
The superseded private key and certificate files identify the item and can be deleted
after its trust-store entry is removed.

The QWC fields follow Intuit's [Web Connector Programmer's Guide](https://static.developer.intuit.com/qbSDK-current/doc/pdf/QBWC_proguide.pdf).
The installed Web Connector major version matches Intuit's current
[QuickBooks Desktop 2024 Web Connector line](https://developer.intuit.com/app/developer/qbdesktop/docs/get-started/get-started-with-quickbooks-web-connector).
Company identity comes only from the read-only `CompanyQuery` response documented in
the official [CompanyQuery schema](https://static.developer.intuit.com/qbSDK-current/common/newosr/qbsdk/json/CompanyQueryRs.json?v=13).

## Minimal operator steps

1. Remove the exact superseded certificate identified by
   `superseded-cert-cleanup.json`; verify `https://localhost:8443/healthz` still opens
   without a certificate warning.
2. In QuickBooks, close the currently open company. Open a dedicated synthetic test
   company as its QuickBooks Admin. Confirm that it is the approved M2 target. Do not
   continue while any real company is open.
3. Start the private service with `start-service.ps1` if the health URL is unavailable.
4. Open QuickBooks Web Connector and choose **Add an Application**. Import
   `KaydBooks-Bridge-M2-ReadOnly.qwc` from the private stage directory.
5. In the QuickBooks authorization dialog, authorize only the currently open synthetic
   company and choose the option that requires that company to remain open. Do not grant
   unattended access. The QuickBooks dialog may describe broad SDK access; the staged
   service itself enforces query-only requests.
6. Copy the connector password locally from `credentials.json` into the Web Connector
   password cell. Do not send it through chat or place it in Git. Clear the clipboard.
7. Select **KaydBooks Bridge M2 Read-Only** and click **Update Selected** once.

The first update is expected to stop with `company binding is not operator-confirmed`.
That is the fail-closed candidate-capture step: HCP evidence is persisted before the
service returns any Bridge qbXML request. Export it to a new private file:

```powershell
$stage = Join-Path $env:LOCALAPPDATA 'KaydBooksBridge\M2Qualification'
$env:KAYDBOOKS_CONFIG = Join-Path $stage 'bridge-config.json'
$env:KAYDBOOKS_QBWC_BINDING_CANDIDATE = Join-Path $stage 'binding-candidate.json'
uv run --frozen kaydbooks-bridge-qbwc-config export-binding-candidate --connector connector-synthetic
```

The operator must compare the private claims with the already-confirmed synthetic
company. Only after that review may `identity_sha256` be changed from the sentinel to
the candidate digest. Candidate capture never edits configuration and never trusts the
first company to connect. Restart the service and click **Update Selected** once more to
qualify HostRet, CompanyRet, binding, duplicate callbacks and close/recovery evidence.

If the observed identity differs, retain the evidence, keep the sentinel or prior
expected digest, close the session, and investigate. Never switch the expected binding
to make an unexpected company pass.
