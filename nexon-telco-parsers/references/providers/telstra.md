# Telstra Provider Runtime Notes

Adapter boundary: `scripts/provider_adapters/telstra/`

## Accepted Input

- Telstra detail report CSV files.

## Parser Rules

- Parse supported legacy/current Telstra CSV shapes through deterministic column handling.
- Fail closed for PDF, XLSX, or unrecognized CSV layouts unless a supported adapter is added.
- Preserve provider account, service, source file, and source row/page/sheet traceability when present.
- Do not add guessed column mappings or infer missing invoice rows.
