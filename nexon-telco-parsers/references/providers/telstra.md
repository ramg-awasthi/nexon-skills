# Telstra Provider Notes

Adapter path: `scripts/provider_adapters/telstra/`

Current source: `telstra_invoice`

Current function mapping: `accountReconV2/readTelstraInvoice`

Observed source file families: `.csv`, `.xlsx`, `.pdf`.

Observed SharePoint archive patterns:

- `/Recon/Telstra/<year>/<month>/<account-or-service>/Invoice`
- `/Recon/Telstra/<year>/<month>/<account-or-service>/ProcessOutput`
- historical variants include `Process_Output`, `Output`, `Output_Process`, `Not sure`, and account folders such as `1436132800`.

Known behavior/gates:

- Run evidence includes Telstra `detail_report (*.csv)`.
- Current failure path can require a file ending with `rec005.csv`.
- DataPrep timeout failures were observed in April 2026 runs.
- Current output commonly lands as `ProcessOutput/result.xlsx`.
- Do not implement guessed column mappings. Record evidence in the migration matrix before marking complete.
