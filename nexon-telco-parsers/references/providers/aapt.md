# AAPT Provider Notes

Adapter path: `scripts/provider_adapters/aapt/`

Current source: `aapt_invoice`

Current function mapping: `accountReconV2/readAAPTInvoice`

Observed source file families: `.zip`, `.xlsx`, `.pdf`, `.xls`.

Observed SharePoint archive patterns:

- `/Recon/AAPT/<year>/<month>/<run-timestamp>/Invoice`
- `/Recon/AAPT/<year>/<month>/<run-timestamp>/ProcessOutput`
- historical variants include `Invoices`, `Process_Output`, `Process Output`, `Old`, `New`, `IDK`, and `Disputes`.

Known behavior/gates:

- AAPT is ZIP-heavy in run evidence.
- Current output commonly lands as `ProcessOutput/result.xlsx`.
- Parser must preserve provider account/run folder evidence when present.
- Do not implement guessed column mappings. Record evidence in the migration matrix before marking complete.
