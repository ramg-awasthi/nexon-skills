# AAPT Provider Runtime Notes

Runtime boundary: the installed `nexon-recon parse --provider AAPT` command.

## Accepted Input

- Complete AAPT invoice ZIP packages.
- Enabled families: `rec001`, `rec004`, `rec005`, and `rec010`.
- Financial-only voice families: `rec002` and `rec006`; Inomial matching is out of scope.
- Reference-only family: `rec012`.

## Parser Rules

- Require readable `rec001` identity/account/period data and at least one
  `rec005` primary service-charge row. Do not derive invoice identity or
  billing period from filenames or partial packages.
- Include every present `rec004`, `rec005`, and `rec010` charge row in parsed
  accounting. Stream every `rec002` and `rec006` charge into the compact voice
  summary for complete invoice financial accounting. Voice matching against
  Inomial is out of scope; `rec012` is reference-only.
- Preserve provider account, the full service identifier, source file, and
  source row/page/sheet traceability. Do not cut an identifier at a dash or
  aggregate it before deterministic billing matching.
- Use the `rec001` billing period as the invoice billing period for every
  parsed charge row. Preserve source charge dates as audit fields when present.
- Keep `rec004` account-level adjustments and discounts in financial and
  report accounting, but exclude them from service-identifier matching. The
  compatibility service value `10000` is a report placeholder, not match
  evidence.
- Preserve individual source charge rows through billing lookup and
  deterministic matching. Refined output may aggregate only rows that already
  share one verified billing identity, and it must retain every contributing
  source-line ID.
- Preserve every `rec010` source row in parsed/raw accounting. Calculate each
  exact source service identifier's net `Charge(ex GST)` from numeric amounts; when that net
  is zero, mark all contributing rows ineligible for billing candidates and
  exclude them from refined financial output. Never use description text for
  this decision.
- Do not add guessed column mappings or infer missing invoice rows.
- Preserve the complete `rec001` supplier financial breakdown for the final
  finance report: current categories excluding GST, actual `GST Payable`,
  current charges including GST, previous account, payments, and previous bill
  adjustments. The header GST is authoritative; line GST rates are supporting
  source data and must not replace it.

## Voice financial accounting

Publish `voice-usage-summary.csv` under `01_Parsed-Output` when voice source
rows are present. Each summary contains supplier account, invoice, source
file/member, service type, usage type, source-row count, and amount excluding GST.
Retain zero totals and credits. Reject malformed financial amounts and invoice
account mismatches. Voice summary groups do not create reconciliation line IDs.
The Financial Audit adds one column, `Voice Usage Amount Ex GST (002/006)`.
Existing control reasons explain that voice is included in financial accounting
and that only Inomial matching is out of scope. Compare all parsed usage,
including `rec010`, with header usage. Include voice exactly once in complete
source detail and refined-plus-exclusions controls before calculating the
previous-bill adjustment split.
