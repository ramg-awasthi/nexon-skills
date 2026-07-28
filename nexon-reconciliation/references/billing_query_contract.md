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

## Frozen Encrypted Request

After deterministic parsing and billing-period resolution, `nexon-recon run`
emits one `billing_candidate_plan.json` and pauses as
`awaiting_billing_candidates`. The plan exposes only:

- `request_identity`: non-secret environment, run ID, and mapping version for
  sanity checks;
- `encrypted_request`: the exact opaque envelope to send to the Database MCP.

The plaintext request is built inside the deterministic runtime and encrypted
before the agent sees it. It contains:

- environment and run ID;
- provider;
- typed account values: supplier invoice account, service-provider account,
  and metadata account;
- requested, invoice-derived, and effective billing periods;
- normalized invoice-line identities and provider service identifiers;
- requested mapping version;
- idempotency key;
- ephemeral recipient public key.

Call `recon_db_get_billing_candidates` exactly once with one argument only:

```text
{"encrypted_request": <exact encrypted_request object from billing_candidate_plan.json>}
```

Do not decrypt, summarize, rewrite, split, or inspect the encrypted request. Do
not translate field names, add guessed columns, generate SQL chunks, or replace
it with `recon_db_read_query`.

## Opaque Response And Resume

Save the complete unchanged MCP response returned by
`recon_db_get_billing_candidates` as a restricted temporary preparation, then
resume. Do not save only `encrypted_request`, `request_identity`, a summary, or
model-written reconstruction:

```text
nexon-recon resume \
  --resume-run-root <run_root> \
  --billing-candidate-preparation <candidate_preparation.json> \
  --output <result.json>
```

The runtime retrieves the one-time opaque artifact and verifies its envelope,
ticket binding, endpoint, size, SHA-256, Ed25519 attestation, environment, run
ID, mapping version, schema contract/fingerprint, input hash, account identity,
candidate identities, and line associations. Matching does not begin if any
binding differs.

The durable audit record contains hashes, versions, table identities, row
counts, limits, and timing. It contains no credentials, raw parameter values,
decrypted ticket, or model-authored SQL.

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
