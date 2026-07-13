---
name: nexon-telco-parsers
description: Deterministic Nexon telco invoice parser contract for AAPT, Telstra, Optus, Vocus, Megaport, and Equinix. Use when routing provider invoice files to supported parser adapters, validating parser outputs, checking provider parser input contracts, or handling parser warnings; adapters fail closed for unsupported formats, missing libraries, or malformed inputs.
---

# Nexon Telco Parsers

Use this skill for provider invoice extraction.

## Supported Parser Inputs

Supported deterministic adapters:

- AAPT ZIP.
- Telstra CSV.
- Optus PDF.
- Optus voice ZIP/DAT.
- Vocus CSV.
- Megaport CSV.
- Equinix XLSX.

Unsupported input families must fail closed with a parser warning. Do not ask the model to parse unsupported invoice formats.

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
python scripts/parse_provider_invoice.py --config ../nexon-reconciliation/config/recon_settings.yaml --provider <provider> --input-dir <dir> --output <json> --warnings <json> --run-id <run_id> --manifest <json>
```

The command routes through:

```text
scripts/parser_core/parse_provider_invoice.py
```

Then to:

```text
scripts/provider_adapters/<provider>/
```

Only `scripts/parser_core/parse_provider_invoice.py` may decide parser routing. `SKILL.md` describes the command and contract only; it must not contain provider branching logic beyond naming the supported adapters.

Optus intentionally has two isolated adapter modules under one provider folder: `scripts/provider_adapters/optus/parser_pdf.py` and `scripts/provider_adapters/optus/parser_excel_voice.py`. The common router selects between them by source package shape: PDF-only packages use `optus_pdf`, non-PDF voice/Excel packages use `optus_excel_voice`, and mixed PDF plus non-PDF packages fail as ambiguous. Do not move this decision into skill prompt logic, runtime config, the supervisor prompt, or a merged Optus parser.

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

Implementation history and readiness evidence belong outside runtime instructions. Normal parser routing is code-owned and must not be moved into runtime config.

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

If the adapter cannot parse the supplied package, emit a `parser_warning` artifact with `parser_unavailable` or `parser_failed` and exit non-zero. Do not guess, backfill, or infer invoice lines.
