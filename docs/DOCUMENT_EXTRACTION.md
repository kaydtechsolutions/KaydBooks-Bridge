# Offline PDF, photo and scan intake

The optional intake module reads printed English PDF, PNG and JPEG sources using
local PDFium/Pillow decoding and Tesseract.js 7.0.0 with Node 22 and the local English
trained-data package. No document, image or extracted text is sent to a model API.
Dependency installation downloads code/model files; extraction uses only local files.

## Workflow and review

1. Capture the original bytes in an explicitly assigned company and source namespace.
2. `extract_document_v1` returns immutable page text, OCR lines/confidence, decoder/model
   versions and hashes, plus observed alternatives for references, dates, parties,
   currencies and totals. It retains conflicting values and does not guess date locale,
   master identity, line arithmetic or transaction operation.
3. Review the original document and select the intended operation and company mappings.
   Enter its fields through the browser form or the narrow preparation tool, then run
   the existing exact master check. Original transaction IDs remain explicit where required.
4. `prepare_extraction_v1` binds the owned extraction and exact extraction hash to a
   new draft. Every field stays below the trusted threshold and blocks validation until
   an authorized source reviewer confirms each value. These routing confidence values
   are not calibrated probabilities. Raw OCR confidence is retained separately.
5. Validation, approval, queueing and deliberate sample posting remain separate shared
   service operations. Neither extraction nor source preparation posts accounting or
   changes permission/policy. Corrections use the existing immutable revision workflow.

The browser's **Upload document** workflow displays the retained observations alongside
the forms. The saved draft can reopen its observations and download the original.
Selecting a customer alias is a human mapping decision, not a conclusion made by OCR.

## Bounds and failure handling

Sources are at most 4 MB. PDFs are limited to four pages and bounded page geometry;
each decoded image is limited to 12 million pixels. Encrypted, malformed, oversized,
animated or unsupported sources are held for manual review. Output text and output
files are bounded. Decoding runs in a separate process without Bridge credentials;
OCR workers have deadlines and their own watchdog. Original source bytes survive failure.

Even text PDFs are rasterized for OCR; embedded PDF text is retained as untrusted
diagnostic evidence. Source text never selects executables, package paths, URLs, models,
company, operation, permissions or dispatch settings. Page text/labels render as text
in the browser, not HTML. Content is not interpreted as instructions.

Extraction is cached by source, owner, company and versioned runtime/model fingerprints.
The current source hash, audit and permissions are checked; revocation during decoding
denies the result. Extraction and job links are immutable and included in signed Bridge
snapshots. A retry recovers retained observations rather than rerunning OCR unnecessarily.

## Installation

Install the Python wheel with the `intake` extra. In a private directory outside the
checkout, copy `ocr/package.json` and `ocr/package-lock.json`, then run `npm ci
--ignore-scripts` there. Configure these absolute paths for the service process:

- `KAYDBOOKS_OCR_NODE`: Node 22 executable.
- `KAYDBOOKS_OCR_MODULES`: that private directory's `node_modules` folder.

The English trained-data dependency is pinned in the lockfile. Its local path is
fixed within the package; runtime extraction cannot fall back to a remote language URL.
Do not put credentials or company paths in the public repository. The browser and
the `extract_document_v1` / `prepare_extraction_v1` MCP tools use the same implementation.

## Retained qualification corpus

`tests/fixtures/intake` contains synthetic, reproducible sources and expected observations:

| Source | Evidence |
| --- | --- |
| clean-invoice.pdf | Exact reference, ISO date and USD10 total observed |
| clean-scan.png | The same printed content through image OCR |
| skewed-photo.jpg | Skewed/compressed image retains those observations |
| image-only-scan.pdf | Raster PDF with no usable embedded text |
| ambiguous-values.pdf | Both totals/dates/customer alternatives retained for review |
| embedded-instructions.pdf | Instruction text retained; no authority or accounting changes |

The corpus was rendered and visually inspected. Real local OCR tests check observations,
cache recovery, source/owner/company boundaries, no credential inheritance, unsupported
PDF limits, revocation and mandatory exact field review. A real browser test covers
upload through source review and validation against a synthetic service; its native
master read is explicitly substituted. This is not a universal OCR accuracy claim:
handwriting, unsupported languages and illegible layouts require manual entry/review.

Installed sample qualification separately uploaded a PDF, retained its observations,
performed a native master check and saved a draft. Validation correctly refused the
unreviewed fields. The fixture's party mapping still requires review, so that draft was
left held and unposted. No accounting records were written.

For local tests, install the `dev` and `intake` extras, the pinned OCR packages and Chromium.
Set the two runtime paths plus `KAYDBOOKS_OCR_TESTS=1` and `KAYDBOOKS_BROWSER_TESTS=1`,
then run `pytest tests/test_extraction.py tests/test_web_browser.py`. CI qualifies the
same corpus with local dependencies on Linux; Windows local qualification also passes.
