---
name: nexon-reconciliation
description: Run or validate Nexon telco invoice reconciliation for AAPT, Telstra, Optus, Vocus, Megaport, and Equinix using SharePoint Intake MCP source discovery and binary staging, native SharePoint writes, deterministic provider parsers, audited billing queries, configurable lifecycle persistence, current-contract XLSX reports, and bounded exception investigation.
---

# Nexon Reconciliation

Use this skill for the shared reconciliation lifecycle. Use
`nexon-telco-parsers` for deterministic invoice extraction.

`nexon-recon-agent` owns orchestration and tool routing. `run_recon.py` owns
deterministic execution and durable lifecycle state. Send only unresolved rows
to `nexon-recon-exception-investigator`.

## Safety Rules

- Use only the configured `Nexon Reconciliation Automation` SharePoint site.
- Use SharePoint Intake MCP for read-only source discovery, exact selection,
  binary preparation, and post-publication re-download verification.
- The only SharePoint Intake MCP tools are
  `recon_sp_get_capabilities`, `recon_sp_probe`,
  `recon_sp_index_sources`, `recon_sp_prepare_download`, and
  `recon_sp_prepare_reference_test`.
- Use native SharePoint tools only for controlled source moves and result
  uploads. Do not use native text-file reads for binary content.
- Never expose a private download key or decrypted one-time ticket in a URL,
  query string, CLI argument, output, exception, report, log, or durable
  receipt.
- Never parse invoices with model reasoning or invent rows.
- Never use parser-only output as a customer reconciliation result.
- Never let an agent write directly to a database.
- Use Nexon Recon Database MCP for every Fleet database read or lifecycle write.
- Keep DSNs and direct database adapters confined to `--local-only` tests.
- Keep `db_update_enabled=false` without a controlled approval artifact.
- Keep credentials out of prompts and artifacts. Customer query values may
  exist only in the transient frozen MCP request; dispose it after successful
  resume and retain only hashes and sanitized logs.

## Storage Contract

```text
Site: Nexon Reconciliation Automation
Upload: /recon-upload-space/<provider>/
Reference: /recon-reference-space/sample-invoices/<provider>/
Result: /recon-result-space/<provider>/<yyyy>/<MM>/<run_id>/
```

Do not create missing SharePoint roots or provider folders during a run.

Run ID:

```text
<provider_slug>_<yyyyMMdd_HHmmss>_<hash5>
```

## Intake Contract

For `provider_api`, use the approved provider adapter and provenance manifest.
Do not call SharePoint Intake MCP.

For `manual_upload`:

1. Call `recon_sp_get_capabilities` and `recon_sp_probe`.
2. Call `recon_sp_index_sources` for `upload`, or `reference` only for an
   explicit `parser_validation` test.
   If a reference index returns `status=sharepoint_folder_not_found`, stop
   cleanly as `source_not_found`. Do not create the folder, retry it as a
   connectivity failure, or send a failure notification.
3. Apply only explicit provider, filename, selection, or all-file constraints.
4. Stop on no match. Ask the user to select when multiple sanitized candidates
   remain. Do not rank candidates.
5. Run `create_intake_download_key.py` to create a new private key and safe
   public-key request. Pass only `recipient_public_key` to
   `recon_sp_prepare_download`. For a reference fixture, call
   `recon_sp_prepare_reference_test` directly with the provider, optional exact
   source name, and public key; it replaces separate index and prepare calls.
6. Require the unchanged
   `{schema_version: "1.0", kind: "prepared_download", result: ...}` envelope.
   Save it to a permission-restricted temporary file without displaying or
   transforming it. Never read or expose the private key.
7. Immediately run:

```text
python skills/nexon-reconciliation/scripts/create_intake_download_key.py \
  --private-key <temporary_private_key.pem> \
  --output <public_key_request.json>

python skills/nexon-reconciliation/scripts/fetch_intake_artifact.py \
  --config skills/nexon-reconciliation/config/recon_settings.yaml \
  --preparation <temporary_preparation.json> \
  --private-key <temporary_private_key.pem> \
  --destination <staged_path>/<source_name> \
  --output <download_receipt.json>
```

The fetcher accepts no URL or ticket CLI argument. It requires the unchanged
version 1.0 `prepared_download` envelope whose result has `status=prepared`,
an exact approved HTTPS `/download` endpoint, provider,
environment, space, filename, expected size and SHA-256, expiry, sanitized
index identity, encrypted one-time ticket, and attestation key. It decrypts
locally, deletes the private key and preparation before redemption, refuses
redirects, sends the ticket only in `X-Recon-Download-Ticket`, verifies the
Ed25519 download attestation, and emits a version 1 `status=downloaded`
sanitized signed receipt.

The MCP preparation tool must fully spool and hash-attest the source bytes
before issuing the encrypted one-time ticket. Any failed fetch requires a fresh
key and preparation. MCP code owns provider file eligibility. Only sanitized
candidates from the approved provider roots may be selected, and user-facing
candidate and batch results are limited to 50 entries.

## Capability Gate

Resolve configured intent and integration capability through one
environment-agnostic policy. Disabled optional features are skipped; enabled
available features execute; enabled unavailable features block; required
unavailable stages always block; conditional features run only when their
condition occurs. The same rule applies to dev and prod Database MCP,
SharePoint Intake MCP, native SharePoint writes, native Outlook notifications,
and provider adapters. Require an MCP receipt only when that MCP owns a
selected reconciliation stage. The separate M365 Graph lifecycle MCP is not
the Outlook notification channel and is not used by this skill. Never rewrite
an MCP capability response to satisfy config, and never require a disabled
optional binding.

Save the unchanged `{schema_version: "1.0", kind, result}` outputs from
`recon_sp_get_capabilities` and `recon_sp_probe`. For reconciliation, also save
unchanged `recon_db_get_capabilities` and `recon_db_probe` results before
preflight. Then run:

```text
python skills/nexon-reconciliation/scripts/preflight_check.py \
  --config skills/nexon-reconciliation/config/recon_settings.yaml \
  --run-mode <parser_validation|reconciliation> \
  --intake-mode <manual_upload|provider_api> \
  --provider <provider> \
  --sharepoint-mcp-capabilities <capabilities_receipt.json> \
  --sharepoint-mcp-probe <probe_receipt.json> \
  --database-mcp-capabilities <database_capabilities.json> \
  --database-mcp-probe <database_probe.json> \
  --output <runtime_capabilities.json>
```

Omit both database arguments for parser validation. Reconciliation requires
both.

For `parser_validation`, require binary source staging, archive validation,
and provider parsing. For `reconciliation`, always require request-scoped
billing preparation, deterministic comparison, and current workbook generation.
Require core supplier/result persistence only when
`core_persistence_enabled=true`. When false, persistence stages must be
`skipped` and reconciliation continues through reports and publication.

Stop with `core_reconciliation_not_available` when the generated execution
policy is blocked. Check native/MCP bindings only for decisions marked
`binding_check_required`. Freeze the policy with the run and do not reinterpret
configuration during resume. Do not downgrade reconciliation into parser
validation.

For reconciliation require the configured database environment, read-only query
policy, schema-qualified allowlist, no comments, no wildcard projection, audit,
sufficient row limit, reachability, and core persistence when enabled.

## Runtime Entry Point

Parser validation is copy-first:

```text
python skills/nexon-reconciliation/scripts/run_recon.py \
  --config skills/nexon-reconciliation/config/recon_settings.yaml \
  --provider <provider> \
  --source-file <staged_file> \
  --result-root <local_result_root> \
  --run-mode parser_validation \
  --intake-mode manual_upload \
  --source-download-receipt <download_receipt.json> \
  --sharepoint-mcp-capabilities <capabilities_receipt.json> \
  --copy \
  --output <result.json>
```

Reconciliation:

```text
python skills/nexon-reconciliation/scripts/run_recon.py \
  --config skills/nexon-reconciliation/config/recon_settings.yaml \
  --provider <provider> \
  --source-file <staged_file> \
  --result-root <local_result_root> \
  --run-mode reconciliation \
  --intake-mode <manual_upload|provider_api> \
  --billing-period <period> \
  --billing-sql-file <billing_query.sql> \
  --provider-account-id <id> \
  --source-download-receipt <download_receipt.json> \
  --sharepoint-mcp-capabilities <capabilities_receipt.json> \
  --database-mcp-capabilities <database_capabilities.json> \
  --database-mcp-probe <database_probe.json> \
  --output <result.json>
```

Omit `--source-download-receipt` for provider API intake and pass its approved
source identity, account/document identifiers, and provenance manifest.

`run_recon.py` verifies only the local receipt contract, local path, byte
count, checksum, provider, space, filename, and sanitized index identity. It
does not call SharePoint or Graph.

## Pause And Resume

For `awaiting_billing_query`, call `recon_db_read_query` exactly once per
frozen request in `billing_mcp_plan`. Save each unchanged response in chunk
order and resume the same run:

```text
python skills/nexon-reconciliation/scripts/run_recon.py \
  --config skills/nexon-reconciliation/config/recon_settings.yaml \
  --resume-run-root <run_root> \
  --billing-mcp-receipt <chunk_1.json> \
  --billing-mcp-receipt <chunk_2.json> \
  --output <result.json>
```

On successful resume, delete the temporary receipts. The runtime deletes the
frozen request and preserves only hashes and the sanitized query log.

Only when `core_persistence_enabled=true` may the run return
`awaiting_core_persistence`. For that status, call `recon_db_persist_run` once with the
exact frozen `database_persistence_request`, excluding only
`contract_version`. Save the unchanged response and resume:

```text
python skills/nexon-reconciliation/scripts/run_recon.py \
  --config skills/nexon-reconciliation/config/recon_settings.yaml \
  --resume-run-root <run_root> \
  --database-persistence-receipt <persistence_receipt.json> \
  --output <result.json>
```

On successful resume, delete the temporary receipt. The runtime deletes the
frozen request and preserves only hashes and committed persistence artifacts.

For `awaiting_exception_investigation`, resume the same run with
`--investigation <exception_investigation.json>`.

For `awaiting_publication`:

1. Use native SharePoint upload operations for every frozen artifact.
2. For manual upload, use the exact indexed provider/source path to move the
   source into the run `source/` folder. Parser validation never moves it.
3. Create a sanitized native publication receipt containing only run identity,
   local and relative paths, checksums, upload statuses, and the source move
   status when applicable.
4. Index the exact result run folder with `recon_sp_index_sources`.
5. Create a fresh ephemeral key, then use `recon_sp_prepare_download` and
   `fetch_intake_artifact.py` to re-download every published artifact,
   including the moved manual source.
6. Resume with the native receipt and every sanitized verification receipt:

```text
python skills/nexon-reconciliation/scripts/run_recon.py \
  --config skills/nexon-reconciliation/config/recon_settings.yaml \
  --resume-run-root <run_root> \
  --publication-receipt <publication_receipt.json> \
  --publication-verification-receipt <downloaded_artifact_receipt.json> \
  --publication-verification-receipt <next_receipt.json> \
  --output <result.json>
```

The same MCP index/prepare contract verifies publication; there is no separate
publication tool. Never create a second run for a resume operation.

## Result And Query Boundaries

The raw XLSX preserves the current 35-column workbook contract. The refined
XLSX preserves every raw column and adds only approved agent and human fields.
Do not emit `agent_confidence_score`, `agent_reason_code`, or `agent_notes`.

Run agent-selected SQL in Fleet only through `recon_db_read_query`. Require one
schema-qualified `SELECT` or `WITH`, approved tables/columns, canonical
candidate projections, read-only policy, timeout, row cap, query chunks, and a
sanitized query log. Reject `SELECT INTO`. Query groups, never one row at a
time. `billing.audit_required` must remain true. `billing_query.py` and direct
DSN persistence are available only for `--local-only` tests.

## Audit And Failures

Every run requires durable run, state, audit, parser, query, and report
manifests. Persistence manifests are required only when lifecycle persistence
is enabled and are forbidden in report-only mode. Fleet publication additionally requires a
sanitized native publication receipt and complete MCP re-download receipts.

Failure manifests contain only run/correlation identity, failed stage, failure
code, retryability, and sanitized detail. Retry only explicitly retryable and
idempotent stages. Outlook notifications are text only and contain no
attachments.

## Runtime References

- `references/operating_contract.md` for states, artifacts, and failure codes.
- `references/access_and_secrets.md` for tool and secret boundaries.
- `references/billing_query_contract.md` before executing billing SQL.
- `../nexon-telco-parsers/SKILL.md` for parser contracts.
