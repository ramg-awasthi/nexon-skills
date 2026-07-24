---
name: nexon-reconciliation
description: Run or validate Nexon telco invoice reconciliation for AAPT, Telstra, Optus, Vocus, Megaport, and Equinix using the dedicated SharePoint site, deterministic provider parsers, audited read-only billing queries, transactional persistence, current-contract XLSX reports, and bounded exception investigation. Use for manual-upload or approved provider-API intake, parser validation, reconciliation execution, run resumption, publication, and controlled failure handling.
---

# Nexon Reconciliation

Use this skill for the shared reconciliation lifecycle. Use `nexon-telco-parsers` for deterministic invoice extraction.

`nexon-recon-agent` owns orchestration and tool routing. `run_recon.py` owns
deterministic execution and durable lifecycle state. The agent hands unresolved
rows only to `nexon-recon-exception-investigator`.

## Safety Rules

- Use only the `Nexon Reconciliation Automation` SharePoint site at
  `/sites/NexonReconciliationAutomation` and its resolver-validated default
  document library.
- Use native SharePoint tools for listing, cloud move/copy, and uploads. Capture
  tool-returned item URLs for receipts; do not create share links.
- Use the approved binary-capable connector for ZIP, PDF, XLSX, and other binary downloads.
- Never parse invoices with model reasoning or invent rows.
- Never use parser-only output as a customer reconciliation result.
- Never let an agent write directly to a database.
- Keep accepted-resolution updates separate from required core reconciliation persistence.
- Keep `db_update_enabled=false` without a controlled approval artifact.
- Keep credentials and customer query values out of prompts, reports, manifests, and logs.

## Fixed Storage Contract

```text
Site: Nexon Reconciliation Automation
Path: /sites/NexonReconciliationAutomation
Library: the site's default document library
Upload: /recon-upload-space/<provider>/
Result: /recon-result-space/<provider>/<yyyy>/<MM>/<run_id>/
```

Resolve the physical target before every run. Save the unchanged native SharePoint
`List Sites` result, then run:

```text
python skills/nexon-reconciliation/scripts/resolve_sharepoint_target.py \
  --sites-file <sharepoint_sites.json> \
  --auth-mode auth_proxy \
  --output <sharepoint_target_binding.json>
```

The resolver accepts exactly one match for the fixed site name and path, validates
that the active access profile can read that site and its default document
library, and emits the only permitted site/drive binding. Never hand-author or
edit this binding.

Run ID:

```text
<provider_slug>_<yyyyMMdd_HHmmss>_<hash5>
```

## Source Intake

Resolve exactly one cloud source item. Download and checksum its binary bytes before moving it.
Pin the download to the selected item ID and retain the connector's provenance
receipt. If the active access profile changes, discard the target binding and
resolve it again.

- `parser_validation` always copies locally and leaves the cloud source unchanged.
- `manual_upload` reconciliation moves the cloud source into the run `source/`
  folder only after binary staging succeeds and the run ID exists.
- `provider_api` reconciliation does not perform a SharePoint source move.

Do not create missing SharePoint root/provider folders during a run.

## Runtime Entry Point

Use `python skills/nexon-reconciliation/scripts/run_recon.py ...`. Do not manually reproduce its stage sequence when the state machine is available.

Parser validation:

```text
python skills/nexon-reconciliation/scripts/run_recon.py \
  --config skills/nexon-reconciliation/config/recon_settings.yaml \
  --provider <provider> \
  --source-file <staged_file> \
  --result-root <local_result_root> \
  --run-mode parser_validation \
  --intake-mode manual_upload \
  --sharepoint-binding <sharepoint_target_binding.json> \
  --source-download-receipt <download_receipt.json> \
  --sharepoint-auth-mode auth_proxy \
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
  --sharepoint-binding <sharepoint_target_binding.json> \
  --sharepoint-auth-mode auth_proxy \
  --output <result.json>
```

Add `--source-download-receipt <download_receipt.json>` for `manual_upload`.
Provider API intake uses its provider provenance manifest instead.

The command fails closed with `core_reconciliation_not_available` unless its capability manifest enables every required stage.

## Pause And Resume

For `awaiting_exception_investigation`, resume the same run:

```text
python skills/nexon-reconciliation/scripts/run_recon.py \
  --config skills/nexon-reconciliation/config/recon_settings.yaml \
  --resume-run-root <run_root> \
  --investigation <exception_investigation.json> \
  --billing-period <period> \
  --output <result.json>
```

For `awaiting_publication`, upload every listed artifact to the exact SharePoint run folder, then resume:

```text
python skills/nexon-reconciliation/scripts/run_recon.py \
  --config skills/nexon-reconciliation/config/recon_settings.yaml \
  --resume-run-root <run_root> \
  --publication-receipt <publication_receipt.json> \
  --output <result.json>
```

Never create a second run for either resume operation.

## Status Boundaries

The raw workbook uses current `ReconMatchStatus`: `Matched`, `Not Matched`, `Supplier Only`, `Billing System Only`, `Dispute`, `Manual Matched`, `Billing Initiated`, or `Service Cancelled`.

Agent statuses exist only in the refined workbook. The investigator may return `suggested_match`, `needs_review`, `multi_match`, `no_match`, `parser_warning`, or `excluded`. Only deterministic matching may assign `auto_matched`.

## Workbook Contract

The raw output is XLSX with `Result`, `Adjustment`, and `Do not change`. `Result` follows the exact current 35-column contract in order.

The refined XLSX preserves all raw columns and appends only the approved agent and human fields. It excludes `agent_confidence_score`, `agent_reason_code`, and `agent_notes`.

If there are no unresolved rows, skip exception investigation and refined-workbook generation.

## Query Boundary

Agent-selected SQL is allowed for initial request-scoped billing preparation and bounded exception investigation only through `billing_query.py`.

Require one schema-qualified `SELECT`/`WITH`, approved tables and columns, canonical candidate projections, read-only credentials, timeout, row cap, query chunks, and a sanitized query log. The tool applies provider/account/period/service filters outside the supplied query. Reject `SELECT INTO`. Additional-evidence queries must provide the original exception input, an unresolved line-ID subset, and the configured sequential query-round budget. Query groups, never one row at a time. `billing.audit_required` must remain true.

## Audit And Failures

Every run requires `run_manifest.json`, `run_state.json`, `audit_manifest.json`, parser artifacts, applicable persistence/query/report manifests, and a publication receipt for Fleet publication.

Failure manifests identify run/correlation ID, failed stage, failure code, retryability, and sanitized detail. Retry only explicitly retryable and idempotent stages.

## Runtime References

- `references/operating_contract.md` for states, artifacts, and failure codes.
- `references/access_and_secrets.md` for connector and secret boundaries.
- `references/billing_query_contract.md` before preparing or executing billing SQL.
- `../nexon-telco-parsers/SKILL.md` for parser contracts.
