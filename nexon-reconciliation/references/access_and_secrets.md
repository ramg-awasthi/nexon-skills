# Access And Secrets

## SharePoint Boundaries

SharePoint Intake MCP holds the read-only SharePoint application credential.
Fleet receives only five tools:

```text
recon_sp_get_capabilities
recon_sp_probe
recon_sp_index_sources
recon_sp_prepare_download
recon_sp_prepare_reference_test
```

The MCP service resolves tenant/site/drive identity internally. Prompts,
skills, config, agent memory, reports, and run manifests contain no Graph
credential, site ID, drive ID, or item ID.

Native SharePoint is independently authorized for:

- exact source moves after successful staging;
- result artifact uploads;
- setup-time folder validation.

Do not use native text reads for binary sources. Do not create share links,
change permissions, or delete unrelated items.

## One-Time Download Ticket

The MCP service completely spools and SHA-256 attests the selected bytes before
returning an unchanged
`{schema_version: "1.0", kind: "prepared_download", result: ...}` envelope.
Its result contains the exact environment-specific HTTPS `/download` endpoint
and a short-lived single-use ticket encrypted to an ephemeral client key.

The preparation file is transient secret material:

- write it with restrictive permissions when possible;
- do not display, summarize, transform, or log it;
- pass only its file path and the private-key path to
  `fetch_intake_artifact.py`;
- delete both before the fetcher redeems the ticket;
- never retry it after any failure.

The fetcher refuses redirects, sends the decrypted ticket only through
`X-Recon-Download-Ticket`, and verifies the signed response attestation. Never
put the private key or ticket in a URL, query, CLI argument, output, exception,
audit record, or durable receipt.

Durable download receipts contain only provider, space, filename, local path,
byte count, SHA-256, sanitized index identity, preparation receipt hash, and
timestamp.

## Non-SharePoint Secrets

- Database DSNs and credentials stay in the database MCP service environment or
  approved runtime secret boundary.
- Provider API credentials stay in provider-specific secret bindings.
- Outlook credentials remain in the native Outlook connection.
- Do not store tokens, tickets, signed URLs, SAS links, passwords, provider
  keys, or transient SharePoint links in durable artifacts. Customer query
  values may exist only in a frozen MCP request while its run is paused; delete
  the request and temporary receipts after successful resume, retaining hashes
  and sanitized logs.

## Access Order

SharePoint:

1. SharePoint Intake MCP for capability, probe, index, prepare, and read-only
   binary verification.
2. Native SharePoint for controlled move and upload.
3. No direct Graph or browser fallback in the runtime path.

Invoices:

1. Approved provider API where implemented and enabled.
2. Manual SharePoint upload otherwise.
3. A separately approved portal acquisition may place a package into upload
   space, but the run still uses `manual_upload`.

Database:

1. Approved database MCP tools when bound.
2. Guarded script connector only in an approved runtime.
3. No free-form database client, browser query, or agent-generated update.
