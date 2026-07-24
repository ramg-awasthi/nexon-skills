# Optus Provider Runtime Notes

Adapter boundary: `scripts/provider_adapters/optus/`

## Accepted Input

- Optus PDF invoice packages.
- Optus voice ZIP/DAT packages.

## Parser Rules

- Preserve separate PDF and voice ZIP/DAT parser branches under one provider.
- The runtime provider name is `Optus`; `OptusVoice` is not a provider name.
- PDF-only packages use the `optus_pdf` parser key.
- Voice ZIP/DAT packages use the `optus_excel_voice` parser key.
- Mixed PDF and voice packages fail as ambiguous; unsupported file types fail closed.
- Summary-only PDFs with no service line rows fail closed rather than inventing rows.
- Do not synthesize a VXC or other line that is absent from the source PDF.
- Voice packages parse `SRVS` service rows and `WUSG` withdrawn-usage rows.
- Preserve provider account, service, source file, and source row/page/sheet traceability when present.
- Do not add guessed column mappings or infer missing invoice rows.
