# Vocus Provider Runtime Notes

Adapter boundary: `scripts/provider_adapters/vocus/`

## Accepted Input

- Vocus invoice CSV files.

## Parser Rules

- Derive billing period dates from invoice issue month, not row-level charge period columns.
- Treat `CN*` folder/account labels as trace fields only unless explicit owner-confirmed account semantics are supplied.
- Fail closed for PDF, XLSX, or unrecognized CSV layouts unless a supported adapter is added.
- Preserve provider account, service, source file, and source row/page/sheet traceability when present.
- Do not add guessed column mappings or infer missing invoice rows.
