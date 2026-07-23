# Runtime Operating Contract

## Run States

Valid run states:

- `created`
- `running`
- `completed`
- `failed`

Valid stage states:

- `pending`
- `running`
- `completed`
- `failed`
- `skipped`

`completed` is terminal and requires validation. `failed` is terminal. A run awaiting exception investigation or publication remains `running`, with the corresponding stage set to `running`.

## Stage Order

1. `source_staging`
2. `run_creation`
3. `archive_validation`
4. `provider_parsing`
5. `billing_preparation`
6. `deterministic_comparison`
7. `supplier_persistence`
8. `result_persistence`
9. `raw_workbook`
10. `exception_investigation`
11. `refined_workbook`
12. `publication`
13. `validation`
14. `notification`

Every stage records attempts, timestamps, counts, artifact paths, status, failure code, and retryability in `manifest/run_state.json`.

## Required Artifacts

Every run:

- `manifest/run_manifest.json`
- `manifest/run_state.json`
- `manifest/audit_manifest.json`
- `manifest/unpack_manifest.json`
- `manifest/parser_manifest.json`
- `logs/parser_warnings.json`
- `normalized/provider_lines.json`

Reconciliation:

- `evidence/billing_candidates.json`
- `logs/billing_query_log.json`
- `normalized/match_results.json`
- `manifest/persistence_manifest.json`
- `normalized/persisted_match_results.json`
- `raw-recon-report/raw-reconciliation.xlsx`
- `manifest/report_manifest.json`

When unresolved rows exist:

- `evidence/exception_input.json`
- `evidence/exception_investigation.json`
- `normalized/final_match_results.json`
- `refined-recon-report/refined-reconciliation.xlsx`

Fleet publication:

- `manifest/publication_set.json`
- `manifest/publication_receipt.json`

## Pause States

`awaiting_exception_investigation`:

- the run ID already exists;
- parsing, billing, matching, persistence, and raw workbook are complete;
- resume with `--resume-run-root` and `--investigation`;
- never create another run.

`awaiting_publication`:

- report artifacts are complete;
- native SharePoint upload is pending;
- resume with `--resume-run-root` and `--publication-receipt`;
- publication receipt URLs must target the approved site.

## Failure Codes

Canonical codes:

- `setup_incomplete`
- `source_not_found`
- `source_ambiguous`
- `binary_download_unavailable`
- `invalid_run_mode_option`
- `unsafe_archive`
- `parser_unavailable`
- `parser_failed`
- `core_reconciliation_not_available`
- `billing_query_not_available`
- `billing_query_not_read_only`
- `billing_query_scope_invalid`
- `billing_query_row_limit_exceeded`
- `core_persistence_not_available`
- `core_persistence_invalid_input`
- `investigation_invalid`
- `publication_invalid`
- `report_contract_failed`
- `validation_failed`
- `notification_failed`

Failure manifests require `run_id` or correlation ID, provider, failed stage, failure code, retryability, sanitized detail, and confirmation that accepted-resolution update was not attempted.

## Status Separation

`ReconMatchStatus` belongs to deterministic reconciliation and the raw workbook.

`agent_match_status` belongs only to the refined workbook. The investigator cannot return `auto_matched`.

`human_verified_status` is written only by a human-review process.

## Full-Run Gate

Production reconciliation is permitted only when `preflight_check.py` emits a capability manifest with every required capability enabled. Documentation or script presence is not capability evidence.

A parser-only test is not a reconciliation run. It proves deterministic extraction and source accounting only.

Equinix one-to-many relationships remain review-required unless the deterministic contract can prove the exact supported allocation.
