# Runtime Operating Contract

## Authority

The immutable snapshot supplies the `nexon-recon` command, packaged settings,
dependencies, and provider code. Skills describe policy; they are not an
executable source. The agent must not search for or execute loose Python files,
provide a config path, install packages, or replace packaged behavior during a
run.

## Run States And Stage Order

Run states are `created`, `running`, `completed`, and `failed`. A pause remains
`running` with its current stage marked `running`. `completed` requires final
validation; `failed` is terminal.

Stage order:

1. source staging and DB run start for manual-upload reconciliation
2. run creation from the authoritative started run ID when a DB run start exists
3. archive validation
4. provider parsing and source accounting
5. parsed-output publication and source move
6. billing-candidate request/response handoff
7. deterministic comparison
8. core persistence or audited report-only skip
9. temporary pre-reconciliation report and verified publication
10. bounded exception investigation for genuine uncertain invoice rows
11. refined report after required investigation
12. supplier financial-audit report
13. refined verification before final publication
14. final publication
15. validation
16. notification when enabled

## Durable And Transient Artifacts

Manual SharePoint intake durably retains unchanged capability/probe envelopes,
a sanitized download receipt, the run-start request/receipt for
reconciliation, the staged source, and its SHA-256. The encrypted preparation,
ephemeral private key, and decrypted ticket are transient and must not become
run artifacts.

Provider API intake durably retains only the staged source file and sanitized
provider provenance manifest. The manifest must bind one exact provider account,
billing period, invoice identity, parser contract, byte count, checksum, and
single-invoice selection scope. Provider API credentials, tokens, URLs,
endpoints, and authorization headers remain outside run artifacts.

Every run contains its run, audit, parser, unpack, warning, normalized-line,
runtime-identity, and frozen-settings artifacts. Reconciliation also exposes a
SharePoint-facing parsed phase under `01_Parsed-Output/`, moves the original upload
into the result run folder under `00_Source-Invoice/`, then later records the
billing-candidate contract identity, sanitized query receipt, matching
evidence, the temporary `02_Pre-Reconciliation/` diagnostic checkpoint,
`03_Reconciled-Output/` reports, investigation evidence when applicable, and
publication verification.

Runtime-created run roots contain these top-level directories:
`00_Source-Invoice/`, `01_Parsed-Output/`, `02_Pre-Reconciliation/`,
`03_Reconciled-Output/`, `04_Financial-Audit/`, and `Metadata/`. Supporting
manifests, normalized JSON, logs, evidence, and internal report copies live
under `Metadata/`.

## Parsed Publication Pause

`awaiting_parsed_publication` means provider parsing is complete and the
runtime has frozen a small parsed artifact set before DB matching begins. The
set contains `01_Parsed-Output/raw_parsed_invoice.csv` and
`01_Parsed-Output/parser_manifest.json`.

The supervisor prepares upload sessions only for that frozen set through
`recon_sp_prepare_result_uploads`. Only frozen metadata is sent:
`local_path`, `relative_path`, `sha256`, and `size_bytes`. The supervisor saves
`structuredContent.result` as the compact parsed upload-session receipt and
runs `nexon-recon upload-result-artifacts` with that receipt and the frozen
`parsed_publication_set.json`. The runtime fetches the full upload session from
the MCP receipt route, streams bytes through `/mcp/artifact/...`, and writes the
small parsed publication receipt. The
SharePoint MCP upload receipt is the server-side verification, so the
supervisor must not re-index or re-download parsed artifacts for SHA checks.
The supervisor resumes with `--parsed-publication-receipt`. For manual-upload
runs, the runtime then emits `awaiting_source_move`; the supervisor calls
`recon_sp_move_source` with the unchanged runtime request so the original upload
is moved into the result run folder under `00_Source-Invoice/`. The supervisor writes only
the MCP response `structuredContent.data` object as the source-move receipt; it
must not write the full MCP envelope with top-level `schema_version`,
`operation`, `status`, `data`, or `error`. The supervisor then resumes with
`--source-move-receipt`. Billing candidate preparation must not begin until
parsed publication and the source move are complete. After the move succeeds,
the upload folder is ready for another intake; retry or resume of the accepted
run must use the result run folder, discoverable through `recon_sp_index_results`,
not by re-indexing the upload folder.

## Billing-Candidate Pause

`awaiting_billing_candidates` means:

- `Metadata/manifest/billing_candidate_plan.json` contains the frozen request used by
  `nexon-recon billing-candidates`;
- the request is built only by the deterministic runtime and includes typed
  provider accounts, invoice-derived effective periods, normalized line
  identifiers, mapping version, and idempotency key;
- the supervisor gets a scoped session with
  `recon_db_prepare_billing_candidates`, then runs
  `nexon-recon billing-candidates --plan ... --session ... --output ...`
  exactly once and does not paste invoice lines into MCP arguments;
- the runtime command owns MCP job polling and paginated result download, so the
  supervisor treats heartbeat lines as progress and waits for that command
  result instead of splitting or retrying the request;
- the same run resumes with `--billing-candidate-response`;
- the runtime validates the response schema, environment, run ID, mapping
  version, schema contract/fingerprint, input hash, candidate identities, and
  per-line associations before matching;
- temporary request and response files are disposed according to run workspace
  hygiene, retaining only sanitized hashes and audit data.

The agent never writes core billing SQL. Provider identifier precedence and
physical schema mappings live in versioned, tested Database MCP code/config.
The MCP may return the complete provider-and-period population. Fleet preserves
that response unchanged; the deterministic runtime decides which rows are
invoice-linked and which are only diagnostic population.

## Run Start

Manual-upload reconciliation must start the DB run through
`recon_db_start_run` before local run creation. The request is prepared only by
`nexon-recon lifecycle-mcp prepare-run-start` from the attested download
receipt. The supervisor sends the unchanged request object to the MCP, saves the
unchanged receipt, and starts `nexon-recon run` with both files.

The local run ID comes from the authoritative run-start receipt. If the response
response does not match environment, provider, source identity, run purpose,
source move mode, or `can_run=true`, the run stops before parsing.

## Report-Only Persistence

`core_persistence` and `accepted_resolution_update` follow the active runtime
policy. When disabled, both stages are recorded as `skipped`; the run still
performs billing lookup, deterministic matching, temporary pre-reconciliation
publication, exception investigation where needed, refined report generation,
final publication, and validation. No
persistence request is produced and no database write tool is called.

If persistence is enabled in a separately approved future policy, it must use
its own explicit policy-controlled resume contract. It must never be inferred
from Database MCP availability alone.

## Pre-Reconciliation Publication

After deterministic comparison, the runtime freezes
`Metadata/manifest/pre_reconciliation_publication_set.json` and pauses at
`awaiting_pre_reconciliation_publication`. Its user-visible artifact is the
temporary E2E diagnostic
`02_Pre-Reconciliation/pre-reconciliation.<locked format>`. It may contain the full
provider-and-period comparison population and must never be labelled refined.

Use the existing `recon_sp_prepare_result_uploads` and
`nexon-recon upload-result-artifacts` flow with frozen metadata only. The
supervisor must not read or print its rows, upload tokens, session tokens, or
artifact URLs. Resume with `--pre-reconciliation-publication-receipt` only.
Required exception investigation begins only after this publication is
verified.

## Exception Investigation

`awaiting_exception_investigation` returns `exception_input_manifest`, which
points to `Metadata/evidence/exception_input.json` and references runtime-emitted files
under `Metadata/evidence/exception_batches/`. Each batch contains at most 100 genuine
uncertain invoice rows, 20 embedded candidate records per row, and 512 KiB
serialized. If a true count exceeds 20, the runtime preserves it in
`candidate_counts`, identifies the line in `candidate_overflow_lines`, and
emits an empty `candidates_by_line` entry. That bounded representation is valid;
the investigator must retain `multi_match`, `needs_review`, or `no_match` and
must not suggest a candidate for the overflow line. Broad unassociated Billing
System Only rows and deterministic zero-net exclusions are not investigation
input. The investigator may return evidence for only the current batch.
Additional database lookup is allowed only through bounded
`recon_db_read_query` with:

- a declared investigation case and run ID;
- a known unresolved line-ID subset;
- schema-qualified read-only SQL;
- named parameters, row/time limits, and a finite query budget;
- no writes, DDL, wildcard projection, comments, or `SELECT INTO`;
- a sanitized audited receipt.

The exception manifest emits immutable `investigation_query_rounds`, current
shared `remaining_query_rounds`, and `query_round_budget_scope="run"`. Every
batch carries the same run scope and current shared balance, not an independent
allowance. The supervisor decrements one central balance; a new batch never
resets it. The investigation receipt manifest records top-level
`diagnostic_query_rounds_used`, which must equal the total executed across all
batches and must not exceed the initial run allowance.

This diagnostic operation may refine uncertain invoice evidence but may not
replace the core candidate operation, invent invoice rows, change source facts
or deterministic matches, or write human-review fields. Batch inputs and
receipts remain internal artifacts, not user-facing reports. Every per-batch
receipt binds `contract_version=1`, the run ID, batch ID, and the exact batch
line set. Using the runtime-owned `investigation_receipt_template`, the
supervisor builds a small manifest with one `{batch_id,path,sha256}` reference
per expected receipt and resumes with that manifest through `--investigation`.

## Refined Verification Pause

After the refined report and financial audit are generated, the runtime pauses
at `awaiting_refined_verification` before freezing final publication. Its compact
input manifest binds parser, candidate, matching, aggregation, refined-report,
and financial-audit artifacts by path and SHA-256. The supervisor verifies
source coverage, report lineage, match decisions, billing evidence, and finance
controls from those actual files.

Frozen run evidence is the starting point, not a prohibition on diagnosis. If
the review finds a specific discrepancy, the supervisor may use the existing
`recon_db_read_query` at most the emitted `diagnostic_query_rounds`, scoped to
the affected provider, invoice period, line/circuit identifier, and rows. It
must save the sanitized receipt under the run root and reference only its path,
SHA-256, and purpose in the compact verification receipt. It must not re-run
the broad candidate lookup or use free-form/broad SQL.

The receipt has five mandatory check outcomes. `passed` advances to final
publication. A `warning` records a narrow historical-baseline difference and
never hides a failed check. `failed` preserves artifacts and blocks
publication; verification never edits a report to make it pass.

## Publication Pause

`awaiting_publication` occurs only after required investigation batches are
accepted and freezes local paths, result-relative paths, and checksums for
final evidence, `03_Reconciled-Output/<supplier-invoice-id>-refined-reconciliation.<locked format>`, and
`04_Financial-Audit/financial-audit.<locked format>`.
`recon_sp_prepare_result_uploads` returns a compact upload-session receipt for
the exact final result set while the full per-file upload session stays
server-side. `nexon-recon upload-result-artifacts` fetches that full session
from the MCP receipt route, streams the files to the MCP artifact URLs, and
writes the sanitized receipt accepted by the runtime.
The SharePoint MCP upload receipt is the server-side verification, so the
supervisor resumes with `--publication-receipt` only. Manual-upload sources are
not moved at final publication because they were already moved after parsed
publication.

## Status And Matching Rules

The temporary pre-reconciliation report preserves the complete deterministic
comparison for E2E diagnosis; it is not the refined result. The refined report
is generated only after required agent verification and uses the business report
schema defined by runtime `RECON_REPORT_COLUMNS`, including the approved agent
and human-review fields. Internal line/candidate fields
are intentionally omitted; exact source-line lineage is preserved in
`report_aggregation_manifest.json`. The format is frozen at run creation:
`xlsx` is the default and `NEXON_RECON_REPORT_FORMAT=csv` selects CSV. Publication uses the
runtime-emitted extension and bytes unchanged; the agent never renames,
converts, or re-saves a report. A parser-only test cannot report billing
comparison, matching, reconciliation reports, or publication completion.

For AAPT, deterministic matching confirms provider AAPT through the master
account relationship, uses the `rec001` billing month/year, and matches the
invoice identifier against `line_number` OR `circuit_id` through the
metadata-to-billing relationship. Amount and metadata
`service_provider_account_number` are not match keys. A single verified
candidate may auto-match. Multiple invoice candidates follow Lizeth's
identifier-hit rule and are marked Matched while retaining their candidate
count and evidence; zero, provisional, and single-candidate rows with
incomplete evidence require review. Broad unassociated Billing System Only
rows remain visible in the pre-reconciliation diagnostic but do not enter
investigation or the refined report. Deterministic zero-net exclusions also do
not enter investigation.
They are reported as exclusions and never counted as matched.

The financial-audit report runs after refinement. It remains a standalone
`04_Financial-Audit/financial-audit.<locked format>` artifact. For XLSX runs,
the same rows are also embedded as a separate `Financial Audit` tab beside
`Recon Result` in the invoice-scoped refined workbook; CSV runs keep only the
standalone audit because CSV cannot contain tabs. For
AAPT it preserves the `rec001` supplier breakdown, actual GST payable, current
charges including GST, previous account movements, detailed supplier-line
total, refined supplier total, and explicit exclusions. These are financial
controls only. GST and supplier/customer amount differences never alter service
matching. A financial-control mismatch is a non-blocking validation outcome.
The run completes with `validation=completed_with_audit_mismatch` and publishes
both reports. The audit summary records invoice, control, expected amount,
actual amount, difference, currency, and reason. Missing, corrupt, changed, or
unpublished artifacts remain blocking technical failures.
The report contains `CurrentCategoryControlReason`, `CurrentGSTControlReason`,
`SupplierLineControlReason`, and `RefinedTotalControlReason`; every reason states
pass/fail, expected amount, actual amount, difference, currency, and the
control-specific explanation.

## Failure Contract

Resolve config intent against capability first: disabled optional features are
skipped, while enabled unavailable or required unavailable features block.
Required capability, probe, index, preparation, fetch, parsing, candidate,
matching, report, publication, and server-side upload verification failures stop the run. Missing
optional reference fixtures map to `sharepoint_folder_not_found` and do not
trigger folder creation or notification.

Failure detail must never contain preparation content, endpoint, ticket,
private key, Graph identity, database credential, raw SQL parameters, or other
secret material.
