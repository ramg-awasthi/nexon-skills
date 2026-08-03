# One-Time SharePoint Setup

## Folder Structure

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

Create a provider folder under `sample-invoices` only when a harmless parser
fixture is onboarded for that provider. An absent reference folder is reported
as `sharepoint_folder_not_found`; it is not a connectivity failure and must not
be created by a normal run.

Do not reuse a personal OneDrive folder, the historical `Recon` tree, or an
unrelated business site. Folder creation is a one-time administrator action,
not a routine agent capability.

## Permissions

SharePoint Intake MCP receives scoped access to the exact site. It may list
approved roots, read binary source/reference content, create the exact result
run folders needed for frozen artifacts, upload result artifacts, and verify
uploaded bytes. It must not move sources, delete, share, or manage permissions.

The native SharePoint connection may:

- list the approved site and folders;
- move the exact staged operational source only when the runtime emits a
  source-move request.

It must not make routine permission changes, create share links, or delete
unrelated content.

## Runtime Binding

Bind the environment-appropriate SharePoint Intake MCP connection exposing:

```text
recon_sp_get_capabilities
recon_sp_probe
recon_sp_index_sources
recon_sp_resolve_source_identity
recon_sp_prepare_download
recon_sp_prepare_reference_test
recon_sp_prepare_result_uploads
```

The MCP service owns the SharePoint application credential, site/drive
resolution, approved gateway, download endpoint, and attestation key. Do not
configure a Graph access profile for the Fleet agent and do not place these
values in skill text or runtime arguments.

## Validation

1. Save unchanged outputs from `recon_sp_get_capabilities` and
   `recon_sp_probe`.
2. Run the packaged preflight command:

```text
nexon-recon preflight \
  --run-mode parser_validation \
  --intake-mode manual_upload \
  --provider <provider> \
  --sharepoint-mcp-capabilities <capabilities.json> \
  --sharepoint-mcp-probe <probe.json> \
  --output <runtime_capabilities.json>
```

3. Index one onboarded reference fixture for the selected provider.
4. Use `recon_sp_prepare_reference_test` and `nexon-recon fetch` to prove
   ZIP/PDF/XLSX binary integrity without moving or modifying the fixture.
5. Confirm the private key and preparation are disposed before ticket
   redemption.
6. Start harmless result artifact upload sessions with
   `recon_sp_prepare_result_uploads`, then stream the files with
   `nexon-recon upload-result-artifacts`.
7. Re-index and re-download the result artifacts through MCP to verify the
   result upload path. Test native SharePoint source move only as a separate
   setup/admin validation, not as the result artifact upload path.

Normal runs validate that source and reference folders already exist and fail
closed when an operational source folder is missing. The upload tool may create
the exact result run folder path for frozen artifacts; it does not repair the
broader SharePoint structure.
