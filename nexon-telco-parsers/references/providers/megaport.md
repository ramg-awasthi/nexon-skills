# Megaport Provider Notes

Adapter path: `scripts/provider_adapters/megaport/`

Current source: `megaport_invoice`

Current function mapping: `accountReconV2/readMegaportInvoice`

Observed source file families: `.csv`, `.xlsx`, `.pdf`.

Observed SharePoint archive patterns:

- `/Recon/Megaport/<year>/<month>/<run-timestamp>/Invoice`
- `/Recon/Megaport/<year>/<month>/<run-timestamp>/ProcessOutput`
- historical variants include `Process_Output` and `Final Report`.

Known behavior/gates:

- Preserve service id/account trace fields from CSV/PDF/XLSX inputs when present.
- Current output commonly lands as `ProcessOutput/result.xlsx`.
- Do not implement guessed column mappings. Record evidence in the migration matrix before marking complete.
