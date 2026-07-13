# AAPT Provider Runtime Notes

Adapter boundary: `scripts/provider_adapters/aapt/`

## Accepted Input

- AAPT invoice ZIP packages.
- Record files: `rec001`, `rec005`, `rec002`, `rec006`, `rec010`, and `rec004`.

## Parser Rules

- Fail closed when required ZIP members cannot be parsed.
- Preserve provider account, service, source file, and source row/page/sheet traceability when present.
- Required compatibility rule for account `2000060308`: `rec002` usage rows are grouped by `Origin[:-2]` before output.
- Required compatibility rule for `rec004`: account-level charges use service id `10000`.
- Do not add guessed column mappings or infer missing invoice rows.
