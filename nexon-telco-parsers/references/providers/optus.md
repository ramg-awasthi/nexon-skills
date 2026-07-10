# Optus Provider Notes

Adapter path: `scripts/provider_adapters/optus/`

Current source: `optus_invoice`

Current function mappings:

- `accountReconV2/readOptusPDFInvoice`
- `accountReconV2/readOptusExcelInvoice`

Observed source file families: `.xlsx`, `.zip`, `.pdf`.

Observed SharePoint archive patterns:

- `/Recon/Optus/<year>/<month>/<run-timestamp>/Invoice`
- `/Recon/Optus/<year>/<month>/<run-timestamp>/ProcessOutput`
- `/Recon/Optus/OptusVoice/<run-timestamp>/Invoice`
- `/Recon/Optus/OptusVoice/<run-timestamp>/ProcessOutput`

Known behavior/gates:

- Preserve separate PDF and Excel/voice parser branches.
- Mixed PDF and non-PDF packages must be split into separate runs.
- Voice path evidence includes `OptusVoice` folders.
- Current output commonly lands as `ProcessOutput/result.xlsx` or voice branch output.
- Do not implement guessed column mappings. Record evidence in the migration matrix before marking complete.
Optus preserves two parser routes:

- `parser_pdf.py`
- `parser_excel_voice.py`

Mixed PDF and non-PDF packages must be split into separate runs. Do not merge the two extraction paths unless migration evidence proves the current system has converged them.
