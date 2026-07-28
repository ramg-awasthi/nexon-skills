# Telstra Provider Runtime Notes

Runtime boundary: the installed `nexon-recon parse --provider Telstra` command.

## Accepted Input

- Telstra detail report CSV files.

## Parser Rules

- Parse supported legacy/current Telstra CSV shapes through deterministic column handling.
- Select detail description in this order when populated: `CALL_TYPE`, `Number Description 2`, `Service Description 2`, `Number Description 1`, then legacy description fields.
- Fail closed for PDF, XLSX, or unrecognized CSV layouts unless a supported adapter is added.
- Preserve provider account, service, source file, and source row/page/sheet traceability when present.
- Do not add guessed column mappings or infer missing invoice rows.
