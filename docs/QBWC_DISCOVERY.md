# Durable QBWC discovery

Status: implemented and synthetic-tested. No real QuickBooks Desktop or Web Connector
session has been run. Live posting remains disabled, and this service can emit only a
fixed read-only HostQuery/CompanyQuery batch.

## Protocol basis

The implementation follows Intuit's [QuickBooks Web Connector Programmer's Guide](https://static.developer.intuit.com/resources/QBWC_proguide.pdf).
The guide defines the callback order and parameters, says the first `sendRequestXML`
call supplies HostQuery, CompanyQuery and PreferencesQuery results in `strHCPResponse`,
and says a negative `receiveResponseXML` result leads QBWC through `getLastError` and
session termination. It also distinguishes an empty company-file return, which selects
the currently open file, from a configured pathname.

The [QuickBooks SDK Programmer's Guide](https://static.developer.intuit.com/resources/QBSDK_ProGuide.pdf)
documents HostQuery, CompanyQuery and PreferencesQuery as read-only queries without
filters. HostQuery returns product/version/country, supported qbXML versions, login mode
and company-file mode. The adapter verifies observed country and the negotiated qbXML
version before accepting company evidence. It does not infer transaction or report
support from those values. The official [CompanyQuery response schema](https://static.developer.intuit.com/qbSDK-current/common/newosr/qbsdk/json/CompanyQueryRs.json?v=13)
was checked for the allowed CompanyRet claims and country/version availability; the
adapter rejects a callback version below the documented CompanyQuery minimum.

## Private connector configuration

Each QBWC username is a connector identity mapped to exactly one configured Bridge
company. Passwords and optional company-file paths are environment references. The
path may help QBWC open a file, but it is stored only as a digest and never establishes
identity. RDP usernames, Windows users, QWC display names, CompanyName alone and file
paths alone are not binding evidence.

`identity_fields` selects at least three supported scalar CompanyRet claims and must
include a claim stronger than display/fiscal names. `identity_sha256` is the SHA-256 of:

```json
{"claims":{"CompanyName":"Synthetic Company A","EIN":"00-0000001","LegalCompanyName":"Synthetic Company A LLC"},"schema":"qbdesktop-company-identity-v1"}
```

The checked-in hash is synthetic. Provision the expected claims and digest from an
authorized, independently reviewed test-company inventory and retain the source values
only in private configuration/evidence storage. Two Bridge companies cannot share one
expected identity digest, and connectors for one company cannot disagree about its
identity definition.

## Durable callback behavior

- Authentication creates an opaque ticket in that company's private SQLite database.
  An exact repeated authenticate callback returns the active ticket; another connector
  for that company receives `busy` until close, disconnect or expiry.
- The first send callback persists the HCP payload, hashed callback file path, country
  and qbXML version before validation. A supplied HCP company mismatch blocks the
  session before any Bridge request is returned.
- The exact HostQuery/CompanyQuery request and random response correlation IDs are
  committed before return. A restart or identical repeated send returns those exact
  read-only bytes. A changed callback context blocks the session.
- The exact response is committed with its callback hash and result. The response must
  contain exactly one correlated successful Host response and one Company response,
  one record each. Missing, ambiguous, unsupported, cross-session or mismatched evidence
  returns `-1` and remains blocked.
- An identical repeated response returns the stored result. A different response for a
  completed request revokes a previously verified session and blocks it. No callback
  can turn discovery into a write or enqueue an accounting transaction.
- Tickets expire at an inclusive deadline. Disconnect returns `done` without asking
  QBWC to try another path. Close and duplicate close are durable. Sessions and callback
  evidence cannot be deleted through ordinary SQL; callback rows and Bridge audit events
  are append-only.

Do not adapt the safe read-only request replay rule to writes. A request whose write
outcome is uncertain must be held and reconciled against QuickBooks before any retry.

## Real integration-test prerequisite

The remaining M2 qualification requires an authorized Windows host with a supported
QuickBooks Desktop installation, QBWC, a dedicated synthetic test company, and a staged
TLS endpoint/QWC file. Grant the application read access only, configure the connector's
private expected CompanyRet digest, and have an operator initiate an update. Record the
QuickBooks edition/release/country, QBWC version, observed supported qbXML versions,
callback transcript digests, and binding result outside Git. No accounting write access
is needed for this test.
