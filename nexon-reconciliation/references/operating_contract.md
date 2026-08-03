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
5. parsed-output publication and re-download verification
6. billing-candidate request/response handoff
7. deterministic comparison
8. core persistence or audited report-only skip
9. raw workbook
10. exception investigation when unresolved rows exist
11. refined workbook
12. final publication and re-download verification
13. validation
14. notification when enabled

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
SharePoint-facing parsed phase under `Invoice/` and `ParsedOutput/`, then later
records the billing-candidate contract identity, sanitized query receipt,
matching evidence, `ReconciledOutput/` reports, investigation evidence when
applicable, and publication verification.

## Parsed Publication Pause

`awaiting_parsed_publication` means provider parsing is complete and the
runtime has frozen a small parsed artifact set before DB matching begins. The
set contains the original invoice package under `Invoice/`, plus
`ParsedOutput/raw_parsed_invoice.csv` and
`ParsedOutput/parser_manifest.json`.

The supervisor prepares upload sessions only for that frozen set through
`recon_sp_prepare_result_uploads`. Only frozen metadata is sent:
`local_path`, `relative_path`, `sha256`, and `size_bytes`. The supervisor saves
`structuredContent.result` as the parsed upload-session receipt and runs
`nexon-recon upload-result-artifacts` with that receipt and the frozen
`parsed_publication_set.json`. The runtime streams bytes through
`/mcp/artifact/...` and writes the small parsed publication receipt. The
supervisor then re-indexes the result run folder, re-downloads every item
through SharePoint MCP, and resumes with `--parsed-publication-receipt` plus one
`--parsed-publication-verification-receipt` per uploaded item. Billing
candidate preparation must not begin until this parsed publication is verified.

## Billing-Candidate Pause

`awaiting_billing_candidates` means:

- `manifest/billing_candidate_plan.json` contains one non-secret
  `request_identity` and one frozen plain `request` object for
  `recon_db_get_billing_candidates`;
- the request is built only by the deterministic runtime and includes typed
  provider accounts, invoice-derived effective periods, normalized line
  identifiers, mapping version, and idempotency key;
- the supervisor calls the MCP operation exactly once with
  the plain request fields copied unchanged from the plan and saves the
  complete unchanged response temporarily;
- the same run resumes with `--billing-candidate-response`;
- the runtime validates the response schema, environment, run ID, mapping
  version, schema contract/fingerprint, input hash, candidate identities, and
  per-line associations before matching;
- temporary request and response files are disposed according to run workspace
  hygiene, retaining only sanitized hashes and audit data.

The agent never writes core billing SQL. Provider identifier precedence and
physical schema mappings live in versioned, tested Database MCP code/config.

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
performs billing lookup, deterministic matching, exception investigation where
needed, raw/refined workbook generation, publication, and final validation. No
persistence request is produced and no database write tool is called.

If persistence is enabled in a separately approved future policy, it must use
its own explicit policy-controlled resume contract. It must never be inferred
from Database MCP availability alone.

## Exception Investigation

`awaiting_exception_investigation` contains known unresolved line IDs frozen
for the run. The investigator may return evidence for only that set. Additional
database lookup is allowed only through bounded `recon_db_read_query` with:

- a declared investigation case and run ID;
- a known unresolved line-ID subset;
- schema-qualified read-only SQL;
- named parameters, row/time limits, and a finite query budget;
- no writes, DDL, wildcard projection, comments, or `SELECT INTO`;
- a sanitized audited receipt.

This diagnostic operation may refine unresolved evidence but may not replace
the core candidate operation or invent invoice rows.

## Publication Pause

`awaiting_publication` freezes local paths, result-relative paths, and
checksums for final evidence and `ReconciledOutput/`.
`recon_sp_prepare_result_uploads` returns scoped upload sessions for the exact
final result set. `nexon-recon upload-result-artifacts` streams the files to the
MCP artifact URLs and writes the sanitized receipt accepted by the runtime.
Native SharePoint is reserved for the runtime-requested source move until a
dedicated MCP move tool exists. Every uploaded artifact and moved source is then
re-indexed, prepared, downloaded, and compared by relative path and SHA-256
before completion.

## Status And Matching Rules

The raw workbook preserves all current reconciliation fields and status values.
The refined workbook preserves every raw field and adds the approved agent and
human-review fields. A parser-only test cannot report billing comparison,
matching, reconciliation workbooks, or publication completion.

Auto-match requires a verified mapping rule and deterministic provider,
account, service, period, and cardinality evidence. Provisional rules and
ambiguous candidates require review. Billing-only candidates are retained as
explicit report/exception rows rather than silently dropped.

## Failure Contract

Resolve config intent against capability first: disabled optional features are
skipped, while enabled unavailable or required unavailable features block.
Required capability, probe, index, preparation, fetch, parsing, candidate,
matching, report, publication, and re-download failures stop the run. Missing
optional reference fixtures map to `sharepoint_folder_not_found` and do not
trigger folder creation or notification.

Failure detail must never contain preparation content, endpoint, ticket,
private key, Graph identity, database credential, raw SQL parameters, or other
secret material.
