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
- stable OwnerID/FileID, an Enterprise-only `AuthFlags` value, mandatory
  `IsReadOnly=true`, and optional unattended access in `qwc-profile.json`;
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
Automatic review prevented deletion of the superseded trusted certificate, and that
deletion was not retried. A live `SslStream` check proved that Windows accepts the
hostname and chain and that the endpoint presents the active v2 certificate, not the
superseded certificate. The old trust entry therefore does not prevent qualification
and must not be removed as part of this run. Its exact private fingerprint evidence is
retained for a separately authorized hygiene task if one is ever required.

The QWC fields follow Intuit's [Web Connector Programmer's Guide](https://static.developer.intuit.com/qbSDK-current/doc/pdf/QBWC_proguide.pdf).
The installed Web Connector major version matches Intuit's current
[QuickBooks Desktop 2024 Web Connector line](https://developer.intuit.com/app/developer/qbdesktop/docs/get-started/get-started-with-quickbooks-web-connector).
Company identity comes only from the read-only `CompanyQuery` response documented in
the official [CompanyQuery schema](https://static.developer.intuit.com/qbSDK-current/common/newosr/qbsdk/json/CompanyQueryRs.json?v=13).

## Verified repair and restart qualification

The operator-approved broader QuickBooks permission resolved AppLock registration.
The old stamped OwnerID/FileID pair collided while QBWC had no registered application;
one fresh stable registration was created, preserving the endpoint, credentials and
confirmed Bridge company binding. Existing stamps and certificates were not removed.

Two actual manual updates passed, the second after bridge service restart. Each
persisted authenticate, sendRequestXML, receiveResponseXML and closeConnection, closed
with progress 100 and no error, and matched the operator-confirmed company identity.
Audit integrity passed. Raw XML and exact deployment details remain private.

QuickBooks application permission is broader than read-only; the Bridge independently
enforces query-only discovery and disabled accounting posting. Personal data and
unattended access were not granted. Keep Auto-Run off for qualification. No additional
manual update is needed for this milestone. Interrupted real callbacks and production
posting remain unqualified. The sections below preserve the repair history.

## Permission conflict: repair proposal history

The original strict read-only import steps below are historical and cannot resolve
the installed QBWC AppLock failure. Actual logs show SDK 3263 under IsReadOnly=true
and successful metadata registration under false. Intuit's linked programmer guide
also documents QBWC stamping FileID into company metadata. Its older IsReadOnly table
contains contradictory wording; do not use that sentence to reverse the flag's
observed behavior or claim a successful read-only import.

The prepared private repair proposal retains the existing connector IDs and endpoint
and requests broader QuickBooks read/write application permission. That permission
is not restricted to metadata by QuickBooks. The Bridge independently stays query-only
and posting-disabled. No personal data or unattended access is required, and no
schedule is requested. Explicit operator agreement is required because this changes
the original QuickBooks-enforced read-only constraint. The proposal is unimported and
has a `.pending` extension. Do not remove certificates or repeat bootstrap replacement.
Actual callbacks and binding must pass after approval before calling the repair fixed.

## Original operator steps (superseded for this installed QBWC)

1. In QuickBooks, close the currently open company. Open a dedicated synthetic test
   company as its QuickBooks Admin. Confirm that it is the approved M2 target. Do not
   continue while any real company is open.
2. Start the private service with `start-service.ps1` if the health URL is unavailable.
3. Open QuickBooks Web Connector and choose **Add an Application**. Import
   `KaydBooks-Bridge-M2-ReadOnly.qwc` from the private stage directory.
4. In the QuickBooks authorization dialog, authorize only the currently open synthetic
   company and choose the option that requires that company to remain open. Do not grant
   unattended access. The QuickBooks dialog may describe broad SDK access; the staged
   service itself enforces query-only requests.
5. Copy the connector password locally from `credentials.json` into the Web Connector
   password cell. Do not send it through chat or place it in Git. Clear the clipboard.
6. Select **KaydBooks Bridge M2 Read-Only** and click **Update Selected** once.

An exact-path private operator guide and clipboard helpers are generated in the private
stage. The password helper reads the credential only when run and does not contain it.

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

## Authorization failures happen before callbacks

QBWC1039 with QuickBooks reporting that an application has not accessed the company
before means QuickBooks rejected the initial application authorization. No service
callback or `CompanyRet` exists to inspect in that case. QuickBooks must be running
with the dedicated synthetic company open as that company's QuickBooks Admin before
the QWC is imported. The QWC's `IsReadOnly=true` asks the QuickBooks request processor
for read-only access. The Bridge independently enforces read-only discovery and has no
posting hook; these are separate controls.

Web Connector 34 may then fail a first-time read-only import with QBWC1039 and SDK
status 3263. Its own import routine tries to create a `FileID` data-extension definition
in the company, and QuickBooks correctly rejects that metadata write under read-only
authorization. This happens before the web service receives a callback.

The attempted bootstrap-to-read-only replacement is NOT qualified. Do not repeat it.
Actual R3 evidence shows that even with matching AppUniqueName on both files, QBWC
attempted AppLock DataExtDefAdd again and QuickBooks rejected it with status 3263.
Changing IDs or removing/reimporting applications did not resolve this limitation.
The earlier claim that AppUniqueName would prevent metadata writes was incorrect.

Keep Auto-Run off, the password blank, and Bridge posting disabled. Preserve the
existing registration and logs. Further work must establish supported QBWC metadata
permission requirements or a direct SDK read-only discovery route. A direct SDK test
would not qualify QBWC callbacks. Broader QuickBooks permissions are not equivalent
to QuickBooks-enforced read-only access and must not be silently substituted.


## Separate direct SDK diagnostic

The installed RequestProcessor2 COM runtime can be used independently of QBWC.
Intuit documents read-only and no-personal-data authorization in its
[SDK Programmer Guide](https://static.developer.intuit.com/resources/QBSDK_ProGuide.pdf).
The actual local interop getters require a session ticket. The private diagnostic
sets preferences before BeginSession and checks them before ProcessRequest.
It opens only the currently open, operator-confirmed sample in single-user mode,
then requests HostQuery/CompanyQuery. Exact responses and dispatch intent stay in
private evidence, with binding unconfirmed. Do not interpret direct SDK success as
QBWC callback qualification or expose this diagnostic as a production interface.

Operator authorization, if prompted: select access only while the sample company
is open; verify read-only access and no personal data. No Web Connector password,
QWC import, unattended access, or write permission is needed for this diagnostic.
