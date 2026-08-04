# Runtime Components

## Declarative Skills

- `nexon-reconciliation` defines orchestration, safety gates, pause/resume
  behavior, report rules, and human-review boundaries.
- `nexon-telco-parsers` defines provider extraction expectations and accounting
  rules.

Skills do not contain the executable runtime. The immutable Fleet snapshot
provides `nexon-recon`, packaged settings, dependencies, and provider modules.
Operational instructions invoke that command only; they do not execute loose
Python files, search mounted skill content, provide a config path, or install
packages at run time.

## SharePoint

SharePoint Intake MCP owns approved source indexing, one-time preparation,
attested binary transfer, result artifact upload, result verification, and
runtime-requested source movement. Native SharePoint is reserved for setup
validation only. Result upload starts with `recon_sp_prepare_result_uploads`,
streams bytes through `nexon-recon upload-result-artifacts`, and is verified by
reusing the MCP index/prepare/fetch path against the exact frozen result set.

Tenant, site, drive, item, application credential, ticket, and attestation
private-key identities remain inside the relevant service boundary. The
runtime retains only sanitized receipts and content hashes.

## Reconciliation Database

Nexon Recon Database MCP owns the versioned core billing candidate operation:

```text
recon_db_start_run
recon_db_update_run
recon_db_prepare_billing_candidates
recon_db_get_billing_candidates
```

The runtime prepares run-start and progress-update requests; the supervisor sends
them unchanged and preserves receipts. For billing candidates, the supervisor
gets a scoped session with `recon_db_prepare_billing_candidates`, runs
`nexon-recon billing-candidates --plan ... --session ... --output ...`, then
resumes with the generated response through `--billing-candidate-response`.
Provider mappings and core SQL remain deterministic, tested MCP code/config.
The bounded
`recon_db_read_query` operation is available only for exception investigation
and controlled diagnostics, never for normal candidate generation.

The current report-only policy skips database persistence and
accepted-resolution updates even when the MCP is reachable.

## Notifications And Observability

Native Outlook owns text-only failure notifications with no attachments.

Use snapshot identity, run state, audit, parser accounting, candidate-contract,
matching, exception, report, publication, and sanitized transfer receipts for
observability. Never log preparations, tickets, download endpoints, private
keys, Graph identities, database credentials, raw SQL parameter values, or
provider secrets.
