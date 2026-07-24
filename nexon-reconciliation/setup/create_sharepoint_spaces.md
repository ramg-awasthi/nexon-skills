# One-Time Setup Checklist

This setup is performed once per environment. It is not part of a normal reconciliation run.

## SharePoint Spaces

Create these folders only in:

```text
Site: Nexon Reconciliation Automation
Site path: /sites/NexonReconciliationAutomation
Library: the site's default document library
```

Do not create or use these runtime folders in the personal OneDrive `Recon`
folder, the `Account Recon` site, or any other searched/discovered SharePoint
location.

Create upload folders:

```text
/recon-upload-space/
  AAPT/
  Telstra/
  Optus/
  Vocus/
  Megaport/
  Equinix/
```

Create result roots:

```text
/recon-result-space/
  AAPT/
  Telstra/
  Optus/
  Vocus/
  Megaport/
  Equinix/
```

## Permissions

- The native SharePoint connection can list the site and folders, copy/move
  uploaded packages, create run folders, and upload artifacts.
- The Fleet SharePoint access-profile application can read the exact site and
  download binary source content through Graph.
- The Fleet access-profile application requires `Sites.Selected` with
  site-level `read`; the profile-backed connector is code-restricted to binary
  download and verification.
- The native SharePoint connection separately requires write access for runtime
  moves and uploads.

## Secrets And Profiles

- Connect and authorize the native LangSmith SharePoint tool account.
- Configure the Fleet SharePoint access profile for the same tenant and grant the
  application `Sites.Selected` plus site-level `read`.
- Confirm the native SharePoint tool can list, upload, and move files in the fixed
  upload/result spaces, and confirm the profile-backed binary connector can
  download a test ZIP/PDF/XLSX without changing its checksum.
- Store provider API and DB credentials in the profile secret store or environment secret manager.
- Do not store secrets in `config/recon_settings.yaml`, prompts, reports, manifests, or logs.

## Setup Validation

Run:

```text
python skills/nexon-reconciliation/scripts/resolve_sharepoint_target.py --sites-file <native_list_sites.json> --auth-mode auth_proxy --output <sharepoint_target_binding.json>
python skills/nexon-reconciliation/scripts/preflight_check.py --config skills/nexon-reconciliation/config/recon_settings.yaml --sharepoint-auth-mode auth_proxy --sharepoint-binding <sharepoint_target_binding.json>
```

Expected result:

- No root folder creation occurs during normal runs.
- `db_update_enabled=false`.
- All six providers are configured.

For local or mounted-folder testing only, run:

```text
python skills/nexon-reconciliation/scripts/preflight_check.py --config skills/nexon-reconciliation/config/recon_settings.yaml --local-check
```

Expected result:

- All provider upload folders exist under the fixed `/recon-upload-space` test root.
- All provider result roots exist under the fixed `/recon-result-space` test root.

For real SharePoint, folder existence and writability must be checked through the native LangSmith SharePoint tool during environment setup. The local validator does not silently create or mutate SharePoint folders.

If validation fails, fix setup before running reconciliation.
