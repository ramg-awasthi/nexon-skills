# Billing Query Contract

Use only the approved reconciliation database through a read-only identity. Direct Inomial PostgreSQL requires separate approval.

## Mandatory Configuration

```yaml
billing:
  mode: read_only_sql
  agent_sql_allowed: true
  audit_required: true
```

`features.billing_query_enabled` must also be true.

## Execution Boundary

`scripts/billing_query.py` is the only billing lookup command.

It enforces:

- one `SELECT` or `WITH`;
- approved schema-qualified tables;
- no write, DDL, admin, execution, copy, or `SELECT INTO`;
- named parameters;
- configured timeout and row cap;
- provider/account/period grouping;
- configured rows per chunk;
- one sanitized query log record per chunk.

Supported runtime modes:

- `sqlite` for local fixtures;
- `sqlserver` or `azure_sql` for the reconciliation database;
- approved PostgreSQL mode only as a separate integration.

## Chunk Parameters

The command owns these scope parameters:

```text
:provider
:provider_account
:billing_period_start
:billing_period_end
:service_ids_json
```

The supplied query must project canonical `provider`, `provider_account`, `transaction_date`, and `service_id` fields. The command wraps it with provider/account/period/service filtering, using `OPENJSON(:service_ids_json)` for Azure SQL or the runtime-equivalent JSON expansion. Do not concatenate identifiers into SQL.

The query must return a service identifier such as `service_id`, `carrier_service_id`, `circuit_id`, or `line_number` so results can be assigned back to known invoice rows. Project a stable source identity as `candidate_id` whenever the source exposes one, such as a transaction UUID or existing billing-row ID. If absent, the tool derives a deterministic content hash; it never uses a line-local sequence as identity.

## Approved Tables

- `dbo.inomialServiceMetaData`
- `dbo.inomialTransactionData`
- `Finance.inomialServiceMetaData`
- `Finance.inomialTransactionData`
- `Finance.GenericNexonBilling`
- `Finance.BillingSystem`
- `Finance.ServiceProvider`
- `Finance.ServiceProviderAccount`

Queries referencing other tables fail closed until code ownership explicitly approves them.

## Candidate Output

Return only fields required to evaluate service, provider, account, invoice, date, subscription, customer, amount, and conflict evidence. Never return credentials or secret-bearing columns.

The runtime converts results into `candidates_by_line` and derives conservative boolean evidence. Query logs contain SQL hash, parameter-key metadata, parameter-value hashes, duration, row count, row limit, and timeout. They never contain raw SQL parameters.

## Investigator Loop

The investigator may propose an additional read-only query but cannot execute it. The supervisor validates and executes it through this command with `--exception-input`, `--line-ids-file`, `--query-round`, and `--query-budget`, then returns candidates and query-log identity. The line IDs must be a known unresolved subset. Query rounds are sequential, appended to the same audit log, and bounded by the configured limit.
