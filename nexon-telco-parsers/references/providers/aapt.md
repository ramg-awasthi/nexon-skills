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

Publish `voice-usage-summary.<format>` under `01_Parsed-Output` when voice source
rows are present. Each summary contains supplier account, invoice, source
file/member, service type, usage type, source-row count, amount excluding GST,
usage quantity and unit. Sum voice raw duration in its stated unit, grouping
incompatible units separately. Missing source quantities remain blank, not zero.
Retain zero charges and credits. Reject malformed financial amounts, quantities,
quantity/unit pairs, and invoice account mismatches. Preserve `rec010` billed and
raw quantities/units separately; never combine Mbps rates with MBytes volume.
Map rec004/rec005 `Qty` to `UsageQuantity`, retaining `billed_quantity` and
exact `Rate` as `source_unit_rate` in parsed provenance. These are billed counts,
not voice duration or data volume. If the source does not state quantity/unit,
show `Not supplied`; do not infer a unit from plan capacity or dates.
Aggregate billed counts only for the same charge description, rate, period and
unit. Keep incompatible charge bases separate and preserve source-line lineage.
Voice summary groups create report-only IDs, never matching or persistence IDs.
Append these compact rows to the pre-recon and refined result sheets, marked
`Excluded` from Inomial matching, with no billing links or review requirement.
Keep the established voice summary grouping. Populate other supplier charge
categories with numeric zero and include the exclusion explanation in the agent
reasoning column. Do not invent a service/customer/billing identity for a summary
that spans multiple services.
Both Financial Audit views use the existing usage charge column; no extra voice
amount column. Include voice exactly once in complete source detail and refined
report totals before calculating the previous-bill adjustment split.
XLSX parsed tabs are `Parsed Invoice (<source families>)`, with
`Parsed Invoice (002|006)` for voice. CSV mode remains supported.

Report columns place **ChargeType**, **UsageQuantity**, and **UsageUnit** immediately before supplier charge amounts. Labels distinguish voice source/category, data download/upload or bandwidth, and regular charge types; combined refined rows retain all contributing charge-type labels. Missing source quantities or units are explicitly marked `Not supplied`.
