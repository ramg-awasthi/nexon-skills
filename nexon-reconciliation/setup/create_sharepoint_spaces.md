# One-Time Setup Checklist

## SharePoint Spaces

On the dedicated `Nexon Reconciliation Automation` site, create:

```text
/recon-upload-space/
/recon-reference-space/sample-invoices/
/recon-result-space/
```

Create provider folders under the upload and result roots:

```text
AAPT
Telstra
Optus
Vocus
Megaport
Equinix
```

Reference provider folders are optional and should exist only when a fixture is
onboarded for that provider. Do not create empty reference folders merely to
satisfy parser validation; the SharePoint Intake MCP reports the missing
fixture folder cleanly.

Do not reuse a personal OneDrive folder, the historical `Recon` tree, or
another business site.

## Permissions

SharePoint Intake MCP:

- read-only access to the exact site;
- permission to list approved roots and read binary content;
- no create, update, move, delete, share, or permission-management capability.

Native SharePoint connection:

- list the configured site and folders;
- move exact operational sources into result run folders;
- upload result artifacts;
- no routine permission changes, sharing, or unrelated deletion.

## MCP Runtime

Bind one environment-appropriate SharePoint Intake MCP connection exposing
exactly:

```text
recon_sp_get_capabilities
recon_sp_probe
recon_sp_index_sources
recon_sp_prepare_download
recon_sp_prepare_reference_test
```

Configure the exact gateway hostname in
`sharepoint_intake.environment` and `sharepoint_intake.gateway_host`. The
service download endpoint is HTTPS `/download`. A decrypted one-time ticket is
accepted only in
`X-Recon-Download-Ticket`.

Keep the SharePoint application credential and attestation-signing material in the
MCP service environment. Do not configure a Graph access profile in Fleet.

## Validation

1. Call `recon_sp_get_capabilities`; save the unchanged
   `{schema_version: "1.0", kind: "capabilities", result: ...}` envelope.
2. Call `recon_sp_probe`; save the unchanged
   `{schema_version: "1.0", kind: "probe", result: ...}` envelope.
3. Run:

```text
python skills/nexon-reconciliation/scripts/preflight_check.py \
  --config skills/nexon-reconciliation/config/recon_settings.yaml \
  --sharepoint-mcp-capabilities <capabilities.json> \
  --sharepoint-mcp-probe <probe.json> \
  --output <runtime_capabilities.json>
```

4. For each provider being validated, onboard one harmless fixture in its
   reference folder. Providers without fixtures are skipped cleanly.
5. Use `recon_sp_prepare_reference_test` and
   `fetch_intake_artifact.py` to prove ZIP/PDF/XLSX binary integrity without
   moving or modifying the fixture.
6. Confirm the transient preparation file is deleted before redemption.
7. Upload and move harmless setup artifacts with native SharePoint.
8. Re-index and re-download the result artifacts through MCP to prove the
   publication verification path.

For local-only folder validation:

```text
python skills/nexon-reconciliation/scripts/preflight_check.py \
  --config skills/nexon-reconciliation/config/recon_settings.yaml \
  --local-check
```

Local validation does not create or mutate SharePoint folders.
