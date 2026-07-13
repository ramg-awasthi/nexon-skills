# Runtime Operating Contract

## Scope

The runtime supports:

- Manual SharePoint upload intake.
- Provider API intake where credentials/endpoints exist.
- AAPT, Telstra, Optus, Vocus, Megaport, Equinix.
- ZIP and multi-file handling.
- Deterministic provider parsing through approved provider adapters.
- Read-only billing lookup for normal reconciliation.
- Deterministic matching.
- Agent-assisted exception investigation.
- Raw and refined reports.
- Run manifests, logs, and evidence.

Supported parser inputs are AAPT ZIP, Telstra CSV, Optus PDF, Optus voice ZIP/DAT, Vocus CSV, Megaport CSV, and Equinix XLSX. A normal run must stop with `parser_unavailable` when required parser libraries are unavailable, the package is malformed, or the selected source format is unsupported.

Normal reconciliation requires billing evidence. If read-only billing lookup is disabled or unavailable, stop with `billing_query_not_available`; parser-only validation is not a full reconciliation run and must not produce customer-match reports.

## Not Allowed By Default

- Automatic DB update.
- Business Central posting.
- PowerBI/Fabric publishing.
- Replacement/retirement of current Logic Apps.
- Free-form invoice parsing.
- Arbitrary SQL.
- Legacy/disabled workflow support unless separately approved.

## Folder Contract

Production folders live only in the dedicated SharePoint site:

```text
Site: Nexon Reconciliation Automation
Site URL: https://nexonap.sharepoint.com/sites/NexonReconciliationAutomation
Library: Shared Documents
```

The old personal OneDrive `Recon` folder and the previously discovered `Account Recon` site are not runtime targets. Do not read from or write to them during normal runs unless the user explicitly approves an investigation.

Users upload to:

```text
/recon-upload-space/<provider>/
```

Runs write to:

```text
/recon-result-space/<provider>/<year>/<month>/<run_id>/
```

Run folders include:

```text
source/
extracted/
normalized/
raw-recon-report/
refined-recon-report/
evidence/
logs/
manifest/
```

Root upload/result spaces are one-time setup.

If a normal run cannot confirm folders, resolve a single source package, unpack safely, parse deterministically, or run approved read-only billing evidence queries, it records a controlled failure manifest and stops. It does not continue by guessing.

## Report Columns

The refined report must preserve every raw/report field produced by the parser and matcher, then append the approved runtime columns below. Raw fields must not be dropped just because the refined report adds agent or human review fields.

Do not remove base fields.

The raw report is generated from pre-investigation deterministic match rows. The refined report is generated from post-investigation final match rows.

`agent_evidence_summary` must stay short and single-line. `reports.evidence_summary.auto_matched` controls deterministic auto-match rows only: `short` writes compact evidence, while `blank` allows blank evidence for `auto_matched` rows. Review, exception, parser-warning, no-match, and multi-match rows still require a short evidence summary.

Approved refined added columns:

- `agent_match_status`
- `agent_match_rule`
- `agent_suggested_customer_account`
- `agent_suggested_subscription_id`
- `agent_suggested_invoice_number`
- `agent_suggested_service_id`
- `agent_evidence_summary`
- `agent_review_required`
- `human_verified_status`
- `human_verified_by`
- `human_verified_at`
- `human_verified_invoice_number`

Excluded from the refined report schema:

- `agent_confidence_score`
- `agent_reason_code`
- `agent_notes`

## Human Verification Semantics

`human_verified_status=verified` means the reviewer is asserting a complete match. It requires `human_verified_invoice_number`.

`human_verified_status=deferred` means the reviewer is preserving a partial/incomplete review state. It may leave `human_verified_invoice_number` blank in the report. Deferred rows are report-only in the current runtime and are not eligible for a future DB update unless Nexon separately approves explicit update semantics for blank-invoice deferred rows.

`human_verified_status=not_reviewed` is report-only and should not produce a DB update row.

## Provider-Specific Matching Rules

Equinix one-to-many candidates remain review-only. The parser may extract deterministic supplier invoice lines, but matching must not auto-allocate one supplier invoice line across multiple customer services.
