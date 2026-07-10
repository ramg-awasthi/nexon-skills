# Vocus Provider Notes

Adapter path: `scripts/provider_adapters/vocus/`

Current source: `vocus_invoice`

Current function mapping: `accountReconV2/readVocusInvoice`

Observed source file families: `.csv`, `.xlsx`, `.pdf`.

Observed SharePoint archive patterns:

- `/Recon/Vocus/<year>/<month>/<account-or-service>/Invoice`
- `/Recon/Vocus/<year>/<month>/<account-or-service>/ProcessOutput`
- direct account folders include `CN10712`, `CN11439`, `CN200`, and `Other accounts`.

Known behavior/gates:

- Current failure path can require a file ending with `rec005.csv`.
- `CN*` folder/account meaning remains owner-confirmation territory.
- Current output commonly lands as `ProcessOutput/result.xlsx`.
- Do not implement guessed column mappings. Record evidence in the migration matrix before marking complete.
