---
name: nexon-reconciliation
description: Operate the Nexon Phase 1 reconciliation workflow for AAPT, Telstra, Optus, Vocus, Megaport, and Equinix using configured deterministic adapters. Use when intaking invoices from SharePoint upload, creating run folders, calling deterministic telco parsers, querying approved read-only billing evidence, matching, producing raw/refined reports, validating runs, or coordinating exception investigation.
---

# Nexon Reconciliation

Use this skill for Nexon reconciliation runtime orchestration.

## Non-Negotiable Rules

- Use the native SharePoint tool for SharePoint upload/result access and deterministic scripts for local staging, ZIP extraction, billing lookup, matching, report writing, validation, and any optional DB update.
- Use `nexon-telco-parsers` for invoice extraction.
- Do not parse invoices with free-form model reasoning.
- Do not invent invoice rows.
- Do not generate write/admin SQL or run SQL outside `billing_query.py`; read-only billing SQL must pass agent guardrails and script validation.
- Do not write directly to the database.
- Keep `db_update_enabled=false` unless explicit approval, write credentials/update endpoint, dry-run, audit logging, and human-verification rules are supplied.
- Treat SharePoint upload/result root creation as one-time setup, not a normal reconciliation run.
- If required upload/result roots are missing, stop with `setup_incomplete`.

## Final Run ID

Use:

```text
<provider_slug>_<yyyyMMdd_HHmmss>_<hash5>
```

Rules:

- `provider_slug` is one of `AAPT`, `Telstra`, `Optus`, `Vocus`, `Megaport`, `Equinix`.
- Timestamp is Australia/Sydney run creation time.
- `hash5` is first five uppercase SHA-256 hex characters from provider, source checksum or provider API invoice id, and run timestamp.
- Write full checksum, timezone, source file names, API invoice ids, and collision handling to `manifest/run_manifest.json`.

## Phase 1 Flow

1. Run setup preflight. Do not create root folders during normal runs.
2. Use the native SharePoint tool or an enabled provider API adapter to resolve exactly one provider package.
3. Create one result run folder under `/recon-result-space/<provider>/<year>/<month>/<run_id>/`.
4. Move or store source package under `source/`.
5. Safely extract archives under `extracted/`.
6. Parse provider invoice lines by calling sibling skill `../nexon-telco-parsers/scripts/parse_provider_invoice.py`.
7. Query billing candidates through `billing_query.py` only when read-only SQL mode is enabled and the SQL passes agent guardrails plus script validation.
8. Run deterministic matching.
9. Write raw reconciliation report.
10. Send only unresolved rows to exception investigation.
11. Merge exception-investigator output with `scripts/apply_exception_investigation.py`.
12. Write refined reconciliation report preserving all raw/report fields and adding approved agent/human columns.
13. Validate reports, manifests, row counts, and no-write/default mode.
14. On controlled failure, call `scripts/record_failure.py`; if configured, call `scripts/notify_failure.py` to prepare a text-only Outlook notification and use the native Outlook Send Email tool to send it; then stop.

## Required Runtime Agents

- Supervisor agent: use `../../agents/supervisor/PROMPT.md`.
- Exception investigator agent: use `../../agents/exception-investigator/PROMPT.md`.

The supervisor performs validation by calling deterministic validation scripts, especially `scripts/validate_run.py`. Do not add additional default runtime sub-agents.

## Deterministic Script Entry Points

These top-level files are stable wrappers. Shared implementation lives in `scripts/recon_core/`. Telco-specific extraction lives in sibling skill `../nexon-telco-parsers/`.

- `scripts/preflight_check.py`
- `scripts/intake_run.py`
- `scripts/safe_unpack.py`
- `scripts/provider_api_download.py`
- `scripts/billing_query.py`
- `scripts/match_recon.py`
- `scripts/apply_exception_investigation.py`
- `scripts/write_reports.py`
- `scripts/validate_run.py`
- `scripts/record_failure.py`
- `scripts/notify_failure.py`
- `scripts/optional_db_update.py`

## Module Separation

- Keep `SKILL.md` as the thin orchestration contract.
- Keep shared deterministic behavior in `scripts/recon_core/`.
- Keep provider-specific invoice extraction in `../nexon-telco-parsers/scripts/provider_adapters/<provider>/`.
- Keep provider-specific notes in `../nexon-telco-parsers/references/providers/<provider>.md`.
- Use exactly two skills for Phase 1: `nexon-reconciliation` and `nexon-telco-parsers`.
- Do not move provider parser rules into this file.

## Runtime References

- Read `references/operating_contract.md` for safety and scope boundaries.
- Read `references/access_and_secrets.md` for native SharePoint tool access and non-SharePoint credential handling.
- Read `references/billing_query_contract.md` before enabling billing/Inomial lookup.
- Read `references/external_references.md` when checking current external docs.
- Read `../../docs/PROVIDER_API_RESEARCH.md` before changing provider API statuses.
- Read `../nexon-telco-parsers/SKILL.md` when parser routing or provider adapter behavior is needed.

## Integration Gates

Provider API download and billing lookup must use approved adapters, credentials, and deterministic scripts. If the required adapter, credentials, parser, or read-only SQL path is unavailable, stop with `integration_unavailable`. Parser internals belong to `nexon-telco-parsers`. Do not replace missing implementation with guesses.

Billing lookup follows the current Logic App evidence path as closely as possible: use the reconciliation DB/Inomial daily extract tables when available. Agent-authored SQL is allowed only in `billing.mode=read_only_sql`, must be read-only before script execution, is validated again by `billing_query.py`, and must produce an audit query log.
