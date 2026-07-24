# Access And Secrets

Authoritative setup details live in `../../../docs/OPERATIONS.md`.

## SharePoint Access

Use the native SharePoint tool for listing, selection, movement, and artifact
upload. Capture tool-returned item URLs for receipts. Use the binary connector
through the active SharePoint access profile
to download ZIP, PDF, XLSX, and other binary sources. Credentials stay in the
profile and are not passed through prompts or files.

The only approved logical storage target is:

```text
Site: Nexon Reconciliation Automation
Site path: /sites/NexonReconciliationAutomation
Library: the site's default document library
```

Tenant hostname, site ID, drive ID, and library URL come only from
`resolve_sharepoint_target.py`. The resolver cross-checks the native SharePoint
site listing against the active access profile, then creates a validated binding.
Do not route normal runs to the personal OneDrive `Recon` folder, the
`Account Recon` site, or any alternate site found by search. Do not hand-author
the binding. Stop if the exact site name and path are absent or ambiguous.

The native SharePoint tool must handle:

- listing provider upload/result folders;
- moving the original uploaded source package into the run `source/` folder;
- uploading raw reports, refined reports, evidence, logs, and manifests.

The binary connector must download the selected source without text decoding and verify its checksum. Download and checksum must complete before the native SharePoint tool moves the cloud source into a run folder.

If the native SharePoint tool cannot confirm permissions, stop with `setup_incomplete`.

## Non-SharePoint Secrets

Provider API and billing/Inomial integrations use approved secret-store or
environment-backed credentials.

- Do not store credentials in prompts.
- Do not store credentials in `config/recon_settings.yaml`.
- Do not store tokens, signed URLs, SAS links, Function codes, DB passwords, provider keys, or native SharePoint transient links in reports/manifests/logs.
- Redact secrets from exceptions.
- Scripts receive secret references or environment variables, not literal secrets.

## Outlook Failure Notifications

Use the native Outlook Send Email tool for failure email notifications when enabled. The Outlook path is text-only.

- Do not attach files.
- Include SharePoint artifact links, report paths, or failure manifest paths in the email body.
- `notify_failure.py` prepares the sanitized `to`, `subject`, and `body_text`; the supervisor sends those values through the native Outlook tool.

## Preferred Access Order

SharePoint:

1. Native SharePoint tool for listing, moves, uploads, and returned item URLs.
2. Profile-backed deterministic Graph connector for binary download.
3. Stop if either required path lacks permissions or required file operations.

Billing/Inomial:

1. Approved read-only reconciliation DB credentials.
2. Direct Inomial PostgreSQL only after a separate approval.
3. UI/browser access only for investigation, not bulk extraction.

Core reconciliation persistence:

1. A separate Azure SQL identity scoped to the existing `Finance` reconciliation tables.
2. `NEXON_RECON_CORE_MODE=azure_sql` or `sqlserver`.
3. `NEXON_RECON_CORE_DSN` supplied only through the Fleet secret store.
4. `sqlite_shadow` only for local tests.
5. Stop if the identity can alter schema or if the target is not the approved side-by-side/cutover database.

Provider invoices:

1. Provider API adapter where configured and approved.
2. Manual SharePoint upload through the native SharePoint tool.
3. Provider portal/browser access is not a normal reconciliation intake mode. Use it only as a separately approved acquisition fallback recorded in the run manifest to place a provider package into the manual SharePoint upload folder; the supervisor still runs the package as `manual_upload`.

Failure notifications:

1. Native Outlook Send Email tool for text-only messages.
2. No attachments in the notification path.
