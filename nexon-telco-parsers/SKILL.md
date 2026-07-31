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
line. Aggregation is allowed only when the runtime declares a provider/version
rule backed by production evidence; otherwise it is a parser flaw, not a
shortcut.

Provider-specific evidence and known format boundaries are documented in
`references/providers/`. Those references explain formats; they do not replace
the executable adapter or authorize speculative behavior.
