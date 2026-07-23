# Access And Secrets

Fleet environment provisioning is maintained outside this portable skill. This
reference defines the runtime identities and variable names the skill expects.

## SharePoint Access

Use the native SharePoint tool for listing, selection, movement, artifact upload, and links. Use an approved Graph service principal or equivalent binary-capable connector to download ZIP, PDF, XLSX, and other binary sources.

The only approved production storage target is:

```text
Site: Nexon Reconciliation Automation
Site URL: https://nexonap.sharepoint.com/sites/NexonReconciliationAutomation
Library: Shared Documents
Browser URL: https://nexonap.sharepoint.com/sites/NexonReconciliationAutomation/Shared%20Documents/Forms/AllItems.aspx
```

Do not route normal runs to the old personal OneDrive `Recon` folder, the `Account Recon` site, or any alternate site found by search. If the native SharePoint tool cannot access this exact site and library, stop with `setup_incomplete`.

The native SharePoint tool must handle:

- listing provider upload/result folders;
- moving the original uploaded source package into the run `source/` folder;
- uploading raw reports, refined reports, evidence, logs, and manifests.

The binary connector must download the selected source without text decoding and verify its checksum. Download and checksum must complete before the native SharePoint tool moves the cloud source into a run folder.

If the native SharePoint tool cannot confirm permissions, stop with `setup_incomplete`.

## Non-SharePoint Secrets

Provider API and billing/Inomial integrations still use approved secret-store or environment-backed credentials.

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

1. Native SharePoint tool for listing, moves, uploads, and links.
2. Approved Graph application connector for binary download.
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
