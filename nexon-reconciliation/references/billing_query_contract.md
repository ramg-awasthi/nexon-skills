# Billing Candidate Contract

## Core Rule

Core reconciliation uses one deterministic Database MCP operation:

```text
recon_db_get_billing_candidates
```

The agent does not author, edit, repair, or retry core billing SQL. The Database
MCP owns the versioned physical-column mapping, provider identifier precedence,
read-only query, schema validation, transaction isolation, row limits, and
sanitized audit receipt. Dev and prod may bind to different schemas, but they
must implement the same contract and declare their mapping version and schema
fingerprint.

## Frozen Plain Request

After deterministic parsing, the runtime first pauses for parsed SharePoint
publication. Only after `awaiting_parsed_publication` is uploaded, verified,
and resumed does the runtime resolve the billing period, emit one
`billing_candidate_plan.json`, and pause as `awaiting_billing_candidates`. The
plan exposes:

- `request_identity`: non-secret environment, run ID, and mapping version for
  sanity checks;
- `request`: the exact plain argument object to send to the Database MCP.

The request is built and frozen inside the deterministic runtime. It contains:

- environment and run ID;
- provider;
- typed account values: supplier invoice account, service-provider account,
  and metadata account;
- requested, invoice-derived, and effective billing periods;
- normalized invoice-line identities and provider service identifiers;
- requested mapping version;
- idempotency key.

Call `recon_db_get_billing_candidates` exactly once with the unchanged request
fields from the plan:

```text
{
  "environment": <request.environment>,
  "run_id": <request.run_id>,
  "provider": <request.provider>,
  "accounts": <request.accounts>,
  "periods": <request.periods>,
  "invoice_lines": <request.invoice_lines>,
  "mapping_version": <request.mapping_version>,
  "idempotency_key": <request.idempotency_key>
}
```

Do not summarize, rewrite, split, or inspect invoice details beyond what is
needed to pass the unchanged request. Do not translate field names, add guessed
columns, generate SQL chunks, or replace it with `recon_db_read_query`.

## Response And Resume

Save the complete unchanged MCP response returned by
`recon_db_get_billing_candidates` as a restricted temporary response, then
resume. Do not save only `request`, `request_identity`, a summary, or
model-written reconstruction:

```text
nexon-recon resume \
  --resume-run-root <run_root> \
  --billing-candidate-response <candidate_response.json> \
  --output <result.json>
```

The runtime verifies the MCP response schema, environment, run ID, mapping
version, schema contract/fingerprint, input hash, account identity, candidate
identities, and line associations. Matching does not begin if any binding
differs.

The durable audit record contains hashes, versions, table identities, row
counts, limits, and timing. It contains no credentials, raw parameter values,
full invoice lines, account details, or model-authored SQL.

## Candidate Semantics

The MCP returns both:

- supplier-linked candidates associated with known invoice lines; and
- billing-only candidates that have no supplier line in the current invoice.

Each line association declares its retrieval rule, rule status, candidate IDs,
candidate count, and whether automatic matching is authorized. The runtime may
auto-match only verified rules with complete deterministic evidence.
Provisional, zero-match, multi-match, and conflicting evidence remains
unresolved. Billing-only rows are retained in the reports and exception set.

## Billing Period

Invoice-derived effective periods scope the candidate lookup. A production
mismatch between requested and invoice periods fails closed. A historical
non-production fixture may proceed only with an explicit test-override reason
and actor plus a timezone-aware future expiry recorded in the audit; the
invoice-derived periods remain the query scope.

## Exception Diagnostic SQL

`recon_db_read_query` is not part of normal candidate retrieval. It may be used
only after deterministic comparison for an exception investigation or
controlled diagnostic. Each call must be bound to the run, investigation case,
known unresolved line IDs, approved schemas, named parameters, row/time limits,
and a finite query budget.

The diagnostic query is read-only and rejects writes, DDL, execution, copy,
wildcard projection, comments, and `SELECT INTO`. Its result is evidence for
the unresolved subset only; it cannot create invoice rows, weaken matching
authority, or update the database.

## Report-Only Persistence

Core persistence and accepted-resolution updates follow the active runtime
policy. When disabled, the runtime records both stages as skipped and continues
with report-only matching, workbook generation, exception review, publication,
and validation.
