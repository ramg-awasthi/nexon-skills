# AAPT Provider Runtime Notes

Runtime boundary: the installed `nexon-recon parse --provider AAPT` command.

## Accepted Input

- AAPT invoice ZIP packages.
- Record files: `rec001`, `rec005`, `rec002`, `rec006`, `rec010`, and `rec004`.

## Parser Rules

- Require a readable `rec001` member. Treat `rec005`, `rec002`, `rec006`, `rec010`, and `rec004` as optional record families and account for every member that is present.
- Preserve provider account, service, source file, and source row/page/sheet traceability when present.
- Preserve leading zeros in `service_id_raw`; remove them only in `service_id_normalized`, which feeds the current report service-number field.
- Required compatibility rule for account `2000060308`: `rec002` usage rows are grouped by `Origin[:-2]` before output.
- Required compatibility rule for `rec004`: account-level charges use service id `10000`.
- Do not add guessed column mappings or infer missing invoice rows.
