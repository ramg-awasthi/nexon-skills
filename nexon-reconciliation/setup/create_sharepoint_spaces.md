# One-Time Setup Checklist

This setup is performed once per environment. It is not part of a normal reconciliation run.

## SharePoint Spaces

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

- Applied/service account can read upload folders.
- Applied/service account can move/copy uploaded source packages.
- Applied/service account can create result year/month/run folders.
- Applied/service account can write reports, evidence, logs, and manifests.
- Human reviewers can read refined reports and write approved review copies where required.

## Secrets And Profiles

- Connect and authorize the native LangSmith SharePoint tool account.
- Confirm the native SharePoint tool can list, read/stage, upload, and move files in the fixed upload/result spaces.
- Store provider API and DB credentials in the profile secret store or environment secret manager.
- Do not store secrets in `config/recon_settings.yaml`, prompts, reports, manifests, or logs.

## Setup Validation

Run:

```text
python scripts/preflight_check.py --config config/recon_settings.yaml
```

Expected result:

- No root folder creation occurs during normal runs.
- `db_update_enabled=false`.
- All six providers are configured.

For local or mounted-folder testing only, run:

```text
python scripts/preflight_check.py --config config/recon_settings.yaml --local-check
```

Expected result:

- All provider upload folders exist under the fixed `/recon-upload-space` test root.
- All provider result roots exist under the fixed `/recon-result-space` test root.

For real SharePoint, folder existence and writability must be checked through the native LangSmith SharePoint tool during environment setup. The local validator does not silently create or mutate SharePoint folders.

If validation fails, fix setup before running reconciliation.
