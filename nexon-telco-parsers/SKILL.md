---
name: nexon-telco-parsers
description: Apply deterministic, provider-specific invoice extraction for AAPT, Telstra, Optus, Vocus, Megaport, and Equinix through the installed Nexon reconciliation runtime.
---

# Nexon Telco Parsers

Use this skill for provider extraction behavior. The skill contains guidance
only. All parser code, libraries, mappings, and tests are bundled in the
immutable snapshot and invoked through `nexon-recon`; never search for or run a
parser from the skill directory.

## Rules

- Provider selection is explicit. File extension alone never selects a
  provider.
- Archive validation precedes parsing. ZIP handling is shared and safe; the
  provider adapter receives only validated members.
- AAPT, Telstra, Vocus, Megaport, and Equinix have isolated adapters behind the
  common command. Optus intentionally has separate PDF and Excel/voice routes
  behind its adapter.
- Parser output must be reproducible from the same bytes and runtime identity.
- Never create invoice rows with a model, repair malformed input creatively,
  infer missing financial values, or silently drop unsupported rows.
- Preserve typed account meanings: supplier invoice account, service-provider
  lookup account, metadata account, and customer billing account.
- Emit stable line IDs, invoice/service identifiers, billing windows, amounts,
  source provenance, warnings, and accounting.
- For AAPT, require `rec001` invoice/account/period identity and `rec005`
  primary service charges; include optional `rec004` and `rec010` when present.
  Stream every `rec002` and `rec006` charge into `voice-usage-summary.<format>`,
  grouped by invoice/source member/service type/usage type with exact amounts
  and source-row counts plus usage quantities and units. Preserve billed and raw
  data quantities/units, including zero-charge rows. Include compact voice rows
  in report totals without creating matching inputs; voice matching
  against Inomial is out of scope. Map rec004/rec005 `Qty` to report
  `UsageQuantity`, preserve exact `Rate` in parsed provenance, and use
  `UsageUnit` from the invoice when available. Explicitly display
  `Not supplied` for missing quantity/unit; never fabricate a measure.
  `rec012` is reference-only.
- Preserve the full AAPT invoice service identifier. Do not shorten it at a
  dash or aggregate it before deterministic billing matching. Mark `rec004`
  account-level rows as reportable but not eligible for service matching.

## Invocation

Normal operation uses `nexon-recon run`, which routes the provider adapter and
freezes its output. For an isolated deterministic parser check use:

```text
nexon-recon parse \
  --provider <provider> \
  --input-dir <validated_source_directory> \
  --output <provider_lines.json> \
  --warnings <parser_warnings.json> \
  --run-id <run_id>
```

Do not supply a config path or module path.

## Accounting Gate

Every result must distinguish:

- all raw source rows;
- charge-bearing input rows;
- reference/header rows;
- aggregation input and output rows;
- deliberately suppressed rows with reason;
- passthrough rows;
- final normalized rows;
- input/output financial totals and any non-enforced header total.

The accounting equation and financial checks must pass. Parser warnings are
data-quality evidence and cannot be hidden. A missing required member, unknown
layout, ambiguous route, or invalid financial value fails closed with a stable
code.

Default parser grain is one charge-bearing source row to one normalized output
line. Keep that grain through billing lookup and deterministic matching.
Aggregation belongs only to refined reporting after multiple rows share one
verified billing identity; otherwise it is a parser flaw, not a shortcut.

Provider-specific evidence and known format boundaries are documented in
`references/providers/`. Those references explain formats; they do not replace
the executable adapter or authorize speculative behavior.
