# Billing Query Contract

Use this reference when enabling read-only Inomial/reconciliation billing lookup.

## Billing Source Boundary

Use only approved read-only billing sources for runtime lookup.

Approved runtime source order:

1. Reconciliation database tables populated from Inomial daily extracts.
2. Direct Inomial PostgreSQL only when Nexon supplies approved read-only credentials.

Known extract table names include `inomialServiceMetaData` and `inomialTransactionData`. Matching evidence should use service ID, carrier/provider, and billing period/date fields when available.

## Runtime Contract

`scripts/billing_query.py` is the only runtime billing lookup script.

It must:

- use `features.billing_query_enabled=true`;
- use `billing.mode=read_only_sql`;
- use `billing.agent_sql_allowed=true`;
- accept run-scoped agent-prepared SQL through `--sql-file` or `--sql`;
- reject non-read-only SQL before execution;
- execute with read-only credentials;
- write candidate evidence only;
- always write a query log;
- never write to the database.

## Query SQL

The supervisor or exception investigator may prepare read-only SQL for the current run. Prefer a run-scoped SQL file under `evidence/` and pass it with `--sql-file`.

SQL should use named parameters derived from normalized invoice rows:

```sql
:provider
:service_id
:service_id_raw
:service_id_normalized
:provider_account
:invoice_number
:billing_period_start
:billing_period_end
```

The script converts named parameters for PostgreSQL when `NEXON_RECON_BILLING_MODE=postgres`.

Allowed SQL shape:

- one statement only;
- starts with `SELECT` or `WITH`;
- no write/admin tokens such as `INSERT`, `UPDATE`, `DELETE`, `MERGE`, `CREATE`, `DROP`, `ALTER`, `TRUNCATE`, `GRANT`, `REVOKE`, `CALL`, `EXECUTE`, or `INTO`.

The adapter still relies on read-only credentials as a second guardrail. The SQL shape check protects against accidental unsafe SQL but is not a substitute for read-only database roles.

## Billing Evidence Output

The query output should return as many of these columns as possible:

- `candidate_id`
- `customer_account`
- `subscription_id`
- `service_id`
- `circuit_id`
- `carrier_service_id`
- `line_number`
- `carrier_name`
- `service_provider`
- `provider_account`
- `customer_invoice_number`
- `transaction_date`
- `charge_start`
- `ledger_date`
- `creation_time`
- `customer_invoice_amount`
- `service_id_match`
- `provider_match`
- `billing_period_match`
- `conflicting_candidate`
- `one_to_many`

If the boolean evidence fields are not returned, `billing_query.py` derives conservative match evidence from returned service/provider/date fields.

## Connection Modes

Supported modes:

- `NEXON_RECON_BILLING_MODE=sqlite` for local fixture tests.
- `NEXON_RECON_BILLING_MODE=postgres` for direct PostgreSQL/Inomial-style access.

Set `NEXON_RECON_BILLING_DSN` in the runtime profile. Do not store DSNs or passwords in prompts, docs, logs, manifests, or reports.

Query logs include billing mode, SQL source, SQL hash, read-only validation status, populated parameter keys, short SHA-256 hashes of populated parameter values, row count, and duration. They must not log raw billing/customer parameters or raw SQL text.
