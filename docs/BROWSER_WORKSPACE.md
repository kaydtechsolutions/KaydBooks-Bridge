# Browser workspace

The installed HTTPS service serves its browser workspace at `/app`. Sign in with
the private Bridge access key and explicitly select an assigned company. Keys stay
in page memory and are cleared on sign-out/reload. No company is selected implicitly.
Every API request authenticates again and checks current company permissions.

## Available workflows

- Overview lists recent documents, state, reference and date. Open a document to
  review its exact payload, retained source, approval and saved transaction identity.
- New document provides forms for the nine currently implemented invoice, bill,
  receipt/payment, credit, refund and credit-application contracts. A native master
  check precedes preparation. Editing a field invalidates that check. Decimal line
  multiplication uses integer arithmetic and the same half-up cents rule as the service.
- Review uses shared validation, approval, queueing and controlled sample dispatch.
  Approval and queueing are separate actions. A browser cannot bypass sample limits,
  paused state, required approval, current permissions or native preflight checks.
  Unknown outcomes offer reconciliation instead of a resend action.
- Corrections preserve source/lineage and require a new check. Uncertain extracted
  fields must each be confirmed against the original; changing values requires correction.
- Spreadsheet intake retains CSV/XLSX bytes, reads explicit separators/worksheets,
  uses form-based defaults and explicit column mappings, previews row errors, and
  checks selected rows individually before preparing drafts. A stable dataset and
  row identity prevent duplicate preparation after reorder/retry. No import posts.
- Reports show native headings, rows, labels, totals, dates, basis and evidence hashes.
  Available company mappings populate entity/item filters. Unsupported requests are
  rejected by the same fixed native report contract used by CLI/MCP.
- User access supports combined roles, exact permission lists, revocation and explicit
  self-approval policy. Updates use the reviewed configuration revision; stale changes
  are rejected. New credential provisioning remains private company setup.

Original files download as attachments. Source text and report labels are rendered
as text, never HTML. The API exposes an allowlist of actions, checks its own origin,
limits bodies to 6 MB and disables caching. Static scripts/styles ship inside the wheel;
there are no third-party scripts or external fonts. Production posting stays disabled.

The browser covers current contracts, not unimplemented operation variants. Original
transaction references currently require exact native transaction IDs. Master creation/
updates, broader currencies/adjustments and Hermes conversation remain
separate acceptance work. M4-02 remains partial until the complete release operation
matrix and convenient transaction selection are available.

PDF/photo/scan upload now uses the [offline extraction workflow](DOCUMENT_EXTRACTION.md),
retaining observations and holding every unreviewed field.

## Qualification

API tests cover authentication, company/owner isolation, current revocation, source
retention, retries, approval boundaries, request size and rejected arbitrary commands.
Real headless browser tests cover all nine form payloads, draft correction, exact cents,
edit invalidation, spreadsheet errors/retry, access revocation and untrusted report cells.
These browser tests use a synthetic HTTP service and explicitly substituted native reads.

Run them after installing the browser:

```powershell
uv sync --frozen --extra dev
uv run playwright install chromium
$env:KAYDBOOKS_BROWSER_TESTS = '1'
uv run pytest tests/test_web_browser.py tests/test_web_ui.py -q
```

CI also runs these tests in Chromium on Linux. The normal suite skips browser launches
unless explicitly enabled. Native sample qualification additionally created and validated
one USD5 service draft, rendered a complete 16-row P&L over verified TLS, and inspected
desktop/mobile layouts with no page errors. The installed service retained the draft,
remained paused and performed zero accounting writes. Private screenshots and proofs
remain outside the repository.

Native qualification discovered QuickBooks' abbreviated same-month date heading
(`September 1 - 7, 2026`). The report parser now expands that exact shape and checks
both endpoints; wrong dates are still rejected. Earlier rejected read evidence remains
retained and was independently validated after the fix.
