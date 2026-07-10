# Equinix Provider Notes

Adapter path: `scripts/provider_adapters/equinix/`

Current source: `equinix_invoice`

Current function mapping: `accountReconV2/readEquinixInvoice`

Observed source file families: `.xlsx`, `.pdf`, `.xls`.

Observed SharePoint archive patterns:

- `/Recon/Equinix/<year>/<month>/<account-or-service>/Invoice`
- `/Recon/Equinix/<year>/<month>/<account-or-service>/ProcessOutput`
- `/Recon/Equinix/<year>/<month>/<run-timestamp>/Invoice`
- `/Recon/Equinix/<year>/<month>/<run-timestamp>/ProcessOutput`

Known behavior/gates:

- Equinix one-to-many candidates remain review-only.
- Parser may extract deterministic invoice lines, but matching/allocation must not auto-allocate one supplier line across multiple customer services.
- Current output commonly lands as `ProcessOutput/result.xlsx`.
- Do not implement guessed column mappings. Record evidence in the migration matrix before marking complete.
