# AAPT Provider Runtime Notes

Runtime boundary: the installed `nexon-recon parse --provider AAPT` command.

## Accepted Input

- AAPT invoice ZIP packages.
- Record files: `rec001`, `rec005`, `rec002`, `rec006`, `rec010`, and `rec004`.

## Parser Rules

- Require a readable `rec001` member. Treat `rec005`, `rec002`, `rec006`, `rec010`, and `rec004` as optional record families and account for every member that is present.
- Preserve provider account, service, source file, and source row/page/sheet traceability when present.
- Preserve leading zeros in `service_id_raw`; remove them only in `service_id_normalized`, which feeds the current report service-number field.
- Preserve AAPT source charge rows in raw parsed accounting. Refined/reconciled output may group rows only under an explicit provider/version rule with production evidence. Invoice `21919695` proves `rec010` internet usage was historically collapsed from 181 source rows to one persisted/result row.
- Required compatibility rule for `rec004`: account-level charges use service id `10000`.
- Do not add guessed column mappings or infer missing invoice rows.
