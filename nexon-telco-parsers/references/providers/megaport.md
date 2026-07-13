# Megaport Provider Runtime Notes

Adapter boundary: `scripts/provider_adapters/megaport/`

## Accepted Input

- Megaport invoice CSV files.

## Parser Rules

- Derive billing period dates from the invoice date's previous month, not row-level `From`/`To` columns.
- Fail closed for PDF, XLSX, or unrecognized CSV layouts unless a supported adapter is added.
- Preserve provider account, service, source file, and source row/page/sheet traceability when present.
- Do not add guessed column mappings or infer missing invoice rows.
