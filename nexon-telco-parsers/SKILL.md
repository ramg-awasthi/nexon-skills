---
name: nexon-telco-parsers
description: Deterministic Nexon telco invoice parser skill for AAPT, Telstra, Optus, Vocus, Megaport, and Equinix. Use when maintaining provider adapters, routing provider invoice files, validating parser contracts, or working with provider-specific invoice parsing notes.
---

# Nexon Telco Parsers

Use this skill for provider invoice extraction.

## Scope

This skill owns deterministic invoice line extraction only.

It does not own:

- reconciliation orchestration;
- SharePoint run folder movement;
- billing/Inomial lookup;
- matching;
- raw/refined report writing;
- exception investigation;
- database update.

## Non-Negotiable Rules

- Use deterministic provider adapter code for invoice extraction.
- Do not parse invoices with free-form model reasoning.
- Do not invent invoice rows.
- Do not infer or invent missing invoice rows.
- Do not query billing/Inomial.
- Do not perform customer matching.
- Do not write reports directly.
- Do not write to the database.
- Keep one adapter area per provider; do not create one skill per telco.

## Parser Entry Point

Use:

```text
python scripts/parse_provider_invoice.py --config ../nexon-reconciliation/config/recon_settings.yaml --provider <provider> --input-dir <dir> --output <json> --warnings <json> --manifest <json>
```

The command routes through:

```text
scripts/parser_core/parse_provider_invoice.py
```

Then to:

```text
scripts/provider_adapters/<provider>/
```

## Provider Adapter Layout

```text
scripts/provider_adapters/
  aapt/parser.py
  telstra/parser.py
  optus/parser_pdf.py
  optus/parser_excel_voice.py
  vocus/parser.py
  megaport/parser.py
  equinix/parser.py
```

Provider implementation evidence belongs in `references/providers/` and `../../migration/provider-migration-matrix.md`, not in per-provider runtime config files.

## Parser Contract

Input:

- provider;
- run id when available;
- source or extracted file path;
- shared runtime config from `../nexon-reconciliation/config/recon_settings.yaml` for feature flags and common policies only. Provider parser routing is code-owned.

Output:

- normalized invoice line JSON at `<run_root>/normalized/provider_lines.json`;
- parser warning JSON list at `<run_root>/logs/parser_warnings.json`;
- parser manifest JSON at `<run_root>/manifest/parser_manifest.json`.

The normalized JSON must use this top-level shape:

```json
{
  "headers": ["..."],
  "lines": [
    {
      "line_id": "...",
      "provider": "AAPT",
      "run_id": "...",
      "source_file": "...",
      "source_row": "...",
      "service_id_raw": "...",
      "service_id_normalized": "...",
      "invoice_number": "...",
      "billing_period_start": "...",
      "billing_period_end": "...",
      "amount": "..."
    }
  ]
}
```

Every row must preserve traceability to source file and row/page/sheet when available.

## Provider Notes

Read the provider note before parser edits:

- `references/providers/aapt.md`
- `references/providers/telstra.md`
- `references/providers/optus.md`
- `references/providers/vocus.md`
- `references/providers/megaport.md`
- `references/providers/equinix.md`

## Parser Availability

If the adapter cannot parse the supplied package, emit a `parser_warning` artifact and exit non-zero. Do not guess, backfill, or infer invoice lines.
