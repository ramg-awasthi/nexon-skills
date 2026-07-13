# Optus Provider Runtime Notes

Adapter boundary: `scripts/provider_adapters/optus/`

## Accepted Input

- Optus PDF invoice packages.
- Optus voice ZIP/DAT packages.

## Parser Rules

- Preserve separate PDF and Excel/voice parser branches under one provider.
- The runtime provider name is `Optus`; `OptusVoice` is not a provider name.
- PDF-only packages use the `optus_pdf` parser key.
- Non-PDF voice/Excel packages use the `optus_excel_voice` parser key.
- Mixed PDF and non-PDF packages fail as ambiguous; do not split them automatically during a normal run.
- Summary-only PDFs with no service line rows fail closed rather than inventing rows.
- Voice packages parse `SRVS` service rows and `WUSG` withdrawn-usage rows.
- Preserve provider account, service, source file, and source row/page/sheet traceability when present.
- Do not add guessed column mappings or infer missing invoice rows.
