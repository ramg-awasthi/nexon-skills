# Billing Candidate Contract

## Core Rule

Core reconciliation uses one deterministic runtime command:

```text
nexon-recon billing-candidates
```

Fleet first calls `recon_db_prepare_billing_candidates` with the runtime-emitted
plan SHA/size to get a scoped upload session. The command then streams the plan
through that session to the configured Database MCP billing-candidate operation,
polls the MCP job/status route, downloads paginated result artifacts, and
rebuilds the final local response without placing full invoice-line payloads in
Fleet tool arguments. Server-side, that prepared job executes
`recon_db_get_billing_candidates`; the agent does not call that tool directly
during normal runs and does not author, edit, repair, or retry core billing SQL.
The Database MCP owns the versioned physical-column mapping, provider
identifier precedence, read-only query, schema validation, transaction
isolation, row limits, and sanitized audit receipt. Dev and prod may bind to
different schemas, but they must implement the same contract and declare their
mapping version and schema fingerprint.

## Frozen Plain Request

After deterministic parsing, the runtime first pauses for parsed SharePoint
publication. Only after `awaiting_parsed_publication` is uploaded, verified,
and resumed does the runtime resolve the billing period, emit one
`billing_candidate_plan.json`, and pause as `awaiting_billing_candidates`. The
plan exposes:

- `request_identity`: non-secret environment, run ID, and mapping version for
  sanity checks;
- `request`: the exact plain argument object used internally by the runtime
  command.

The request is built and frozen inside the deterministic runtime. It contains:

- environment and run ID;
- provider;
- typed account values: supplier invoice account, service-provider account,
  and metadata account;
- requested, invoice-derived, and effective billing periods;
- normalized invoice-line identities and provider service identifiers;
- requested mapping version;
- idempotency key.

Call `recon_db_prepare_billing_candidates` with the runtime-emitted plan
SHA/size, save the scoped session response, then run the command with the frozen
plan and session:

```text
nexon-recon billing-candidates \
  --plan <billing_candidate_plan.json> \
  --session <billing_candidate_session.json> \
  --output <candidate_response.json>
```

Do not summarize, rewrite, split, print, or inspect invoice details beyond the
runtime command. Do not translate field names, add guessed columns, generate SQL
chunks, paste `invoice_lines` into MCP arguments, expose the scoped upload
token, or replace the lookup with `recon_db_read_query`.

The scoped session is short lived, with a 60-minute server TTL. The command
waits between MCP status checks, prints sanitized heartbeat lines as progress,
downloads result pages when the job succeeds, and writes the complete
`candidate_response.json`. Page size is only a transport chunk size; it is not a
normal reconciliation failure limit. If the job fails or times out, the command
writes one failed MCP-style response with a single blocker code.

By default, the MCP reuses an identical completed result for up to 60 minutes,
including after an MCP restart. The cache identity includes environment, run ID,
input hash, and mapping version. The command reports `cached_result` when it
reuses that result and `fresh_query` when it queries the DB. Changed, expired,
incomplete, corrupt, or failed results are not reused.

Use `--refresh` only when the user explicitly requests a fresh DB read or DB
data is confirmed to have changed since the cached result:

```text
nexon-recon billing-candidates \
  --plan <billing_candidate_plan.json> \
  --session <billing_candidate_session.json> \
  --output <candidate_response.json> \
  --refresh
```

A fresh lookup creates new indexed SQL temporary tables inside one read-only
transaction, stores the completed paginated result for bounded reuse, rolls
back, and closes the connection. Fleet never creates or retains SQL temporary
tables itself.

## Response And Resume

The command writes the complete unchanged MCP response as a restricted
temporary response, then resume. Do not save only `request`, `request_identity`,
a summary, or model-written reconstruction:

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

The MCP returns the complete query evidence for the invoice-derived provider
and period, including:

- supplier-linked candidates associated with known invoice lines; and
- billing-system rows not associated with a supplier line in the current
  invoice.

Each line association declares its retrieval rule, rule status, candidate IDs,
candidate count, and whether automatic matching is authorized. The runtime may
auto-match only verified rules with complete deterministic evidence.
Provisional, zero-match, multi-match, and conflicting evidence on invoice rows
remains unresolved. The broad unassociated billing-system population is kept in
`02_Pre-Reconciliation/pre-reconciliation.<locked format>` for temporary E2E
diagnosis. It is not an invoice exception and does not enter agent investigation
or `03_Reconciled-Output/<supplier-invoice-id>-refined-reconciliation.<locked format>`. This reporting
rule does not narrow, truncate, or rerun the MCP query.

For AAPT, deterministic runtime matching confirms provider AAPT through the
master account relationship, uses the `rec001` billing month/year, and matches
the invoice identifier against `line_number` OR `circuit_id` through the
metadata-to-billing relationship. Amount and metadata
`service_provider_account_number` are not match keys.

Exception transport never truncates the true candidate count. A bounded batch
embeds at most 20 candidate records per invoice line and is at most 512 KiB. If
the true count exceeds 20, `candidate_counts` keeps that count, the line appears
in `candidate_overflow_lines`, and its `candidates_by_line` entry is empty. The
investigator must not suggest a candidate from an omitted overflow set.

## Billing Period

Invoice-derived effective periods scope the candidate lookup. If the user did
not request a target period, the runtime infers the run period from the parsed
invoice package and no mismatch override is needed. A production mismatch
between an explicit requested period and invoice periods fails closed. A
historical non-production fixture with an explicit requested-period mismatch
may proceed only with a test-override reason and actor plus a timezone-aware
future expiry recorded in the audit; the invoice-derived periods remain the
query scope.

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
