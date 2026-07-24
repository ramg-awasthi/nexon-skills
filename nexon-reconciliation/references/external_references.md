# External References

## Skills And Runtime Tools

- `nexon-reconciliation` owns orchestration contracts and deterministic run
  scripts.
- `nexon-telco-parsers` owns provider extraction.
- SharePoint Intake MCP owns read-only source index/prepare and binary
  attestation.
- Native SharePoint owns controlled moves and uploads.
- Nexon Recon Database MCP owns approved database capability.
- Native Outlook owns text-only notifications.

## SharePoint Runtime

The five SharePoint Intake MCP tools are the only read path. The service keeps
tenant, site, drive, item, and credential identities behind its boundary.
`fetch_intake_artifact.py` consumes a transient encrypted preparation and
ephemeral private key, refuses redirects, redeems the decrypted ticket through
the exact environment-specific HTTPS `/download` endpoint, verifies the signed
attestation, and emits a sanitized receipt.

Native SharePoint remains the write path. Result publication is proven by
reusing MCP index/prepare plus the generic fetcher against the exact result run
folder. No separate publication-verification tool exists.

## Observability

Use run state, audit, query, parser, persistence, report, native publication,
and sanitized download receipts. Never log preparation contents, download
endpoint, ticket, Graph identity, credentials, or customer-sensitive SQL
parameters.
