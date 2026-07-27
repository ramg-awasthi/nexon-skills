# Runtime Operating Contract

## Run States

Run states are `created`, `running`, `completed`, and `failed`.

`completed` requires validation. `failed` is terminal. A run awaiting billing
query execution, core persistence, exception investigation, or publication
remains `running` with that stage set to `running`.

## Stage Order

1. source staging
2. run creation
3. archive validation
4. provider parsing
5. billing preparation
6. deterministic comparison
7. atomic supplier and result persistence
8. raw workbook
9. exception investigation
10. refined workbook
11. publication
12. validation
13. notification

## Source Artifacts

Manual SharePoint intake uses:

- unchanged SharePoint Intake MCP capability and probe envelopes;
- a transient encrypted `prepared_download` envelope and ephemeral private key
  deleted before redemption;
- a durable sanitized `download_receipt.json`;
- the locally staged package and its SHA-256.

The preparation envelope, private key, and decrypted one-time ticket are never
run artifacts.

Provider API intake uses its download/provenance manifest and checksum instead.

## Run Artifacts

Every run contains:

- `manifest/run_manifest.json`
- `manifest/run_state.json`
- `manifest/audit_manifest.json`
- `manifest/parser_manifest.json`
- `manifest/unpack_manifest.json`
- `logs/parser_warnings.json`
- `normalized/provider_lines.json`

Fleet manual intake also freezes a sanitized
`manifest/source_download_receipt.json`. It contains no endpoint, ticket,
credential, URL, site ID, drive ID, or item ID.

Reconciliation adds applicable query, candidate, persistence, match, report,
exception, and workbook artifacts.

## Pause States

`awaiting_billing_query`:

- `manifest/billing_mcp_plan.json` freezes every bounded query request;
- the supervisor calls `recon_db_read_query` once per request;
- every unchanged response is supplied in chunk order;
- the runtime verifies environment, run ID, query/parameter hashes, row count,
  row limit, table identity, and response shape before creating candidates;
- the request and temporary receipts are disposed after successful resume,
  retaining only hashes and a sanitized query log.

`awaiting_core_persistence`:

- `manifest/database_persistence_request.json` freezes one hash-bound
  lifecycle transaction;
- the supervisor calls `recon_db_persist_run` once;
- the runtime verifies environment, run ID, payload hash, persisted rows, and
  persistence manifest before generating reports;
- the request and temporary receipt are disposed after successful resume,
  retaining only hashes and committed persistence artifacts.

`awaiting_exception_investigation`:

- unresolved input is frozen;
- only known line IDs may be returned;
- resume the same run with the investigation artifact.

`awaiting_publication`:

- `manifest/publication_set.json` freezes local paths, relative paths, and
  checksums;
- native SharePoint upload and optional source move are pending;
- create a sanitized native `publication_receipt.json`;
- re-index and re-download the exact result artifacts through the same MCP
  index/prepare/fetch flow;
- resume with every result-space download receipt.

Completion requires exact equality across publication set, native receipt, and
MCP re-download paths/checksums.

## Status Separation

The raw workbook preserves current `ReconMatchStatus` values. Agent status and
human verification fields exist only in the refined report.

A parser-only test is not a reconciliation run. It cannot claim customer
matching, billing comparison, persistence, raw/refined reconciliation
workbooks, or publication completion.

## Full-Run Gate

Resolve config intent against capability first: disabled optional features are
skipped, while enabled unavailable or required unavailable features block.
Stop with `core_reconciliation_not_available` when the resulting policy has a
blocker. SharePoint Intake MCP must pass capability, probe, index, preparation,
binary fetch, and result re-download checks. Native SharePoint must separately
pass move and upload checks. Nexon Recon Database MCP must pass capability and probe
checks and advertise read queries plus core persistence when enabled.

Equinix one-to-many behavior remains provider evidence, not a reason to weaken
global cardinality rules.

## Failure Codes

Representative controlled failures:

- `sharepoint_mcp_required`
- `sharepoint_mcp_invalid`
- `intake_preparation_invalid`
- `intake_preparation_expired`
- `intake_preparation_disposal_failed`
- `intake_download_failed`
- `source_download_receipt_required`
- `download_receipt_invalid`
- `source_not_found`
- `source_ambiguous`
- `unsafe_archive`
- `provider_api_not_available`
- `core_reconciliation_not_available`
- `investigation_invalid`
- `publication_invalid`

Failure detail must be sanitized and must never contain preparation content,
endpoint, ticket, Graph identity, credential, or customer-sensitive SQL
parameter values.

For reference-only parser validation, SharePoint Intake MCP
`status=sharepoint_folder_not_found` maps to `source_not_found`. It is an
optional fixture gap, not a connectivity failure, and must not trigger folder
creation or failure notification.
