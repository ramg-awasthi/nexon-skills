# Access And Secrets

## Immutable Runtime Boundary

The Fleet snapshot contains the `nexon-recon` executable, packaged settings,
dependencies, and provider adapters. Skills and prompts contain no executable
fallback, config path, credential, token, DSN, or environment-specific secret.
Do not install dependencies or substitute loose scripts during a run.

## SharePoint Boundaries

SharePoint Intake MCP holds its scoped SharePoint application credential and
exposes only:

```text
recon_sp_get_capabilities
recon_sp_probe
recon_sp_index_sources
recon_sp_resolve_source_identity
recon_sp_prepare_download
recon_sp_prepare_reference_test
recon_sp_prepare_result_uploads
```

The service resolves tenant, site, drive, item, endpoint, and attestation
identity internally. These values and Graph credentials do not enter prompts,
skills, agent memory, reports, or durable manifests.

Native SharePoint is independently authorized only for exact source moves after
verified publication upload and setup-time folder validation. Do not use native
text reads or native uploads for binary/result artifacts, create share links,
change permissions, or delete unrelated items.

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

## SharePoint Publication Transfer

Result upload starts with `recon_sp_prepare_result_uploads`. The agent sends
only the runtime-frozen artifact metadata: `local_path`, `relative_path`,
`sha256`, and `size_bytes`. The MCP returns a short-lived upload session under
`/mcp/artifact/...`; the runtime command `nexon-recon upload-result-artifacts`
streams the local file bytes and writes the small publication receipt used by
`nexon-recon resume`. Do not display, summarize, truncate, edit, rebuild, or
transform artifact content into text payloads in the agent. Do not print upload tokens,
artifact URLs, or full upload-session receipts.

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

1. SharePoint Intake MCP for capability, probe, index, preparation, result
   result upload, and binary verification.
2. Native SharePoint for controlled source move and setup validation only.
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
