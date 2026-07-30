# Access And Secrets

## Immutable Runtime Boundary

The Fleet snapshot contains the `nexon-recon` executable, packaged settings,
dependencies, and provider adapters. Skills and prompts contain no executable
fallback, config path, credential, token, DSN, or environment-specific secret.
Do not install dependencies or substitute loose scripts during a run.

## SharePoint Boundaries

SharePoint Intake MCP holds its read-only SharePoint application credential and
exposes only:

```text
recon_sp_get_capabilities
recon_sp_probe
recon_sp_index_sources
recon_sp_prepare_download
recon_sp_prepare_reference_test
```

The service resolves tenant, site, drive, item, endpoint, and attestation
identity internally. These values and Graph credentials do not enter prompts,
skills, agent memory, reports, or durable manifests.

Native SharePoint is independently authorized for exact source moves after
verified publication upload, result uploads, and setup-time folder validation.
Do not use native text reads for binary files, create share links, change
permissions, or delete unrelated items.

## One-Time SharePoint Transfer

The MCP spools and hashes the selected bytes before returning an encrypted,
short-lived, single-use preparation bound to an ephemeral recipient key. Treat
the preparation and private key as transient secrets:

- store them only in restricted temporary files;
- never display, summarize, transform, or log their contents;
- pass only their paths to `nexon-recon fetch`;
- dispose of both before ticket redemption;
- never retry after a fetch attempt.

The runtime sends the decrypted ticket only in the required request header,
refuses redirects, and verifies the signed response attestation. Never put the
ticket, private key, endpoint, or authorization material in a URL, query, CLI
argument, output, exception, audit record, or durable receipt.

## Database Boundaries

Database credentials and DSNs remain exclusively in the Database MCP service
environment. The Fleet agent receives only MCP operations and sanitized
receipts.

Core billing lookup uses one frozen plain `request` object sent unchanged to
`recon_db_get_billing_candidates`. The agent cannot author core SQL, add
identifiers, split batches, or select physical database columns; mapping and
query ownership remain in versioned MCP code/config. Fleet must not log full
invoice lines, account details, credentials, DSNs, SQL parameters, or raw MCP
response payloads; Database MCP server-side request logging follows its own
audited service contract.

`recon_db_read_query` is allowed only for a bounded exception investigation or
controlled diagnostic. The request must be read-only, scoped to known
unresolved line IDs, limited by row/time/query budgets, and audited without raw
parameter values.

The current report-only policy does not call database persistence or
accepted-resolution update tools. Their skipped status is recorded in the run
policy and audit.

## Other Secret Boundaries

- Provider API credentials remain in provider-specific service bindings.
- Invoice Intake MCP receipts are sanitized source provenance only:
  provider, account, billing period, invoice/document identity, parser contract,
  local path, byte count, checksum, and single-invoice selection scope. They
  must not contain API URLs, endpoints, authorization headers, bearer tokens,
  passwords, or provider secrets.
- Outlook credentials remain in the native Outlook connection.
- Never persist tokens, tickets, signed URLs, SAS links, passwords, provider
  keys, raw database parameters, or transient SharePoint links.
- Frozen MCP requests, temporary billing responses, and unchanged SharePoint
  preparations exist only while a run is paused and are disposed after
  successful resume according to their contract; durable records retain only
  sanitized hashes and audit metadata.

## Access Order

SharePoint:

1. SharePoint Intake MCP for capability, probe, index, preparation, and
   read-only binary verification.
2. Native SharePoint for controlled move and upload.
3. No direct Graph, browser, or loose-script fallback in the runtime path.

Invoices:

1. Approved provider API where implemented and enabled.
2. Manual SharePoint upload otherwise. AAPT remains manual until its endpoint,
   authentication, exact invoice selector, expected ZIP output, and test
   download are proven.
3. A separately approved portal process may place a package in upload space,
   after which the run still uses `manual_upload`.

Database:

1. `recon_db_get_billing_candidates` for the normal reconciliation lookup.
2. Bounded `recon_db_read_query` only for exception investigation or an
   approved diagnostic.
3. No free-form database client, model-authored core SQL, direct credential
   access, or agent-generated update.
