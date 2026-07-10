# Access And Secrets

Authoritative setup details live in `../../../docs/ACCESS_SETUP.md`.

## SharePoint Access

Use the native LangSmith SharePoint tool for SharePoint upload/result access. Do not use a runtime profile, browser profile, Graph wrapper script, or model-driven UI operation for normal SharePoint file movement.

The native SharePoint tool must handle:

- listing provider upload/result folders;
- reading or staging the selected source package for deterministic scripts;
- moving the original uploaded source package into the run `source/` folder;
- uploading raw reports, refined reports, evidence, logs, and manifests.

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

1. Native LangSmith SharePoint tool.
2. Stop if the native tool lacks permissions or required file operations.

Billing/Inomial:

1. Approved read-only reconciliation DB credentials.
2. Approved direct Inomial PostgreSQL read-only credentials.
3. UI/browser access only as fallback and only for investigation, not bulk extraction.

Provider invoices:

1. Provider API adapter where available.
2. Manual SharePoint upload through the native SharePoint tool.
3. Provider portal/browser access is not a normal reconciliation intake mode. Use it only as a separately approved acquisition fallback to place a provider package into the manual SharePoint upload folder; the supervisor still runs the package as `manual_upload`.

Failure notifications:

1. Native Outlook Send Email tool for text-only messages.
2. No attachments in the Phase 1 notification path.
