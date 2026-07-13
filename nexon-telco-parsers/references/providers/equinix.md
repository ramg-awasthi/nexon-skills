# Equinix Provider Runtime Notes

Adapter boundary: `scripts/provider_adapters/equinix/`

## Accepted Input

- Equinix XLSX detail invoice files.

## Parser Rules

- Extract only deterministic invoice lines and approved infrastructure split rows.
- Matching/allocation must not auto-allocate one supplier line across multiple customer services.
- Equinix one-to-many candidates remain review-only.
- Fail closed for PDF, XLS, or unrecognized XLSX layouts unless a supported adapter is added.
- Preserve provider account, service, source file, and source row/page/sheet traceability when present.
- Do not add guessed column mappings or infer missing invoice rows.
