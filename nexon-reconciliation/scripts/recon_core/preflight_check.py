from __future__ import annotations

import argparse
import os
from pathlib import Path

from .common import (
    DEFAULT_CONFIG_PATH,
    PROVIDER_CONFIG_KEYS,
    PROVIDERS,
    ensure_db_update_disabled,
    load_config,
    require_audit,
    sharepoint_roots,
    write_json,
)
from .sharepoint_binding import load_binding
from . import sharepoint_connector


def capability_manifest(
    config: dict,
    *,
    local_check: bool,
    sharepoint_auth_mode: str = "auth_proxy",
    sharepoint_binding: Path | None = None,
    profile_validated: bool = False,
) -> dict:
    features = config.get("features", {})
    billing_enabled = features.get("billing_query_enabled") is True
    core_enabled = features.get("core_persistence_enabled") is True
    billing_dsn = bool(os.environ.get("NEXON_RECON_BILLING_DSN"))
    core_dsn = bool(os.environ.get("NEXON_RECON_CORE_DSN"))
    core_mode = os.environ.get("NEXON_RECON_CORE_MODE", "").strip().lower()
    core_ready = bool(
        core_enabled
        and core_dsn
        and (
            core_mode in {"sqlserver", "azure_sql"}
            or (local_check and core_mode == "sqlite_shadow")
        )
    )
    binding_ready = False
    if sharepoint_binding is not None:
        load_binding(sharepoint_binding)
        binding_ready = True
    profile_ready = (
        sharepoint_auth_mode == "auth_proxy"
        and binding_ready
        and profile_validated
    )
    return {
        "contract_version": 1,
        "capabilities": {
            "binary_source_staging": bool(
                local_check or profile_ready
            ),
            "provider_parsing": True,
            "archive_validation": True,
            "core_supplier_persistence": core_ready,
            "request_scoped_billing_preparation": bool(billing_enabled and billing_dsn),
            "deterministic_comparison": features.get("deterministic_matching_enabled") is True,
            "core_result_persistence": core_ready,
            "current_workbook_generation": True,
            "exception_investigation": bool(billing_enabled and billing_dsn),
            "accepted_resolution_update": False,
        },
        "feature_flags": {
            "provider_api_enabled": features.get("provider_api_enabled") is True,
            "billing_query_enabled": billing_enabled,
            "core_persistence_enabled": core_enabled,
            "deterministic_matching_enabled": features.get("deterministic_matching_enabled") is True,
            "db_update_enabled": features.get("db_update_enabled") is True,
            "failure_notifications_enabled": features.get("failure_notifications_enabled") is True,
        },
        "audit_required": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate one-time Nexon recon folder setup.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--local-check", action="store_true", help="Treat fixed roots as local filesystem paths.")
    parser.add_argument(
        "--sharepoint-auth-mode",
        choices=["auth_proxy"],
        default="auth_proxy",
    )
    parser.add_argument("--sharepoint-binding", type=Path)
    parser.add_argument("--output", type=Path, help="Write a machine-readable capability manifest.")
    args = parser.parse_args()

    config = load_config(args.config)
    ensure_db_update_disabled(config)
    require_audit(config)
    upload_root, result_root = sharepoint_roots(config)

    provider_api_adapters = config.get("provider_api_adapters", {})
    if not isinstance(provider_api_adapters, dict):
        raise RuntimeError("provider_api_adapters must be a mapping when present.")
    unsupported_adapters = set(provider_api_adapters) - set(PROVIDER_CONFIG_KEYS)
    if unsupported_adapters:
        raise RuntimeError(f"Unsupported provider API adapter config: {sorted(unsupported_adapters)}")
    disabled_adapters = [provider for provider, enabled in provider_api_adapters.items() if enabled is not True]
    if disabled_adapters:
        raise RuntimeError(f"Remove disabled provider API adapter entries instead of setting false: {sorted(disabled_adapters)}")

    profile_validated = False
    if not args.local_check and args.sharepoint_binding is not None:
        binding = load_binding(args.sharepoint_binding)
        sharepoint_connector.configure_runtime(
            auth_mode=args.sharepoint_auth_mode,
            binding_path=args.sharepoint_binding,
        )
        site = sharepoint_connector._graph_json(
            "GET", f"/sites/{binding['site_id']}"
        )
        drive = sharepoint_connector._graph_json(
            "GET", f"/drives/{binding['drive_id']}"
        )
        profile_validated = (
            site.get("id") == binding["site_id"]
            and str(site.get("webUrl") or "").rstrip("/") == binding["site_url"]
            and drive.get("id") == binding["drive_id"]
            and str(drive.get("webUrl") or "").rstrip("/")
            == binding["drive_web_url"]
        )
        if not profile_validated:
            raise RuntimeError(
                "profile_site_mismatch: active profile does not match the resolved SharePoint target."
            )

    capabilities = capability_manifest(
        config,
        local_check=args.local_check,
        sharepoint_auth_mode=args.sharepoint_auth_mode,
        sharepoint_binding=args.sharepoint_binding,
        profile_validated=profile_validated,
    )
    if args.output:
        write_json(args.output, capabilities)

    if not args.local_check:
        print("Setup config validated. SharePoint permissions must be checked with the native SharePoint tool.")
        return 0

    problems: list[str] = []
    for provider in sorted(PROVIDERS):
        if not (upload_root / provider).is_dir():
            problems.append(f"Missing upload folder: {upload_root / provider}")
        if not (result_root / provider).is_dir():
            problems.append(f"Missing result folder: {result_root / provider}")

    if problems:
        for problem in problems:
            print(problem)
        return 2

    print("Local setup validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
