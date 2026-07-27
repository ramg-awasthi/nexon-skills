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
    read_json,
    write_json,
)
from .db_mcp_handoff import validate_database_mcp


SHAREPOINT_INTAKE_TOOLS = {
    "recon_sp_get_capabilities",
    "recon_sp_probe",
    "recon_sp_index_sources",
    "recon_sp_prepare_download",
    "recon_sp_prepare_reference_test",
}
EXPECTED_PROVIDER_EXTENSIONS = {
    "AAPT": {".zip"},
    "Telstra": {".csv", ".zip"},
    "Optus": {".dat", ".pdf", ".zip"},
    "Vocus": {".csv", ".zip"},
    "Megaport": {".csv", ".zip"},
    "Equinix": {".xlsx", ".zip"},
}


def validate_sharepoint_mcp_receipts(
    capabilities_path: Path, probe_path: Path, config: dict
) -> None:
    capabilities_envelope = read_json(capabilities_path)
    probe_envelope = read_json(probe_path)
    if (
        not isinstance(capabilities_envelope, dict)
        or set(capabilities_envelope) != {"schema_version", "kind", "result"}
        or capabilities_envelope.get("schema_version") != "1.0"
        or capabilities_envelope.get("kind") != "capabilities"
        or not isinstance(capabilities_envelope.get("result"), dict)
    ):
        raise RuntimeError(
            "sharepoint_mcp_invalid: expected unchanged capabilities envelope."
        )
    capabilities = capabilities_envelope["result"]
    intake = config.get("sharepoint_intake", {})
    expected_environment = (
        str(intake.get("environment") or "").strip().lower()
        if isinstance(intake, dict)
        else ""
    )
    expected_host = (
        str(intake.get("gateway_host") or "").strip().lower().rstrip(".")
        if isinstance(intake, dict)
        else ""
    )
    delivery = capabilities.get("binary_delivery")
    attestation = capabilities.get("attestation")
    limits = capabilities.get("limits")
    providers = capabilities.get("providers")
    if (
        capabilities.get("status") != "ok"
        or capabilities.get("environment") != expected_environment
        or capabilities.get("read_only") is not True
        or set(capabilities.get("tools", [])) != SHAREPOINT_INTAKE_TOOLS
        or capabilities.get("download_contract_version") != 1
        or not isinstance(delivery, dict)
        or delivery.get("method") != "POST"
        or delivery.get("ticket_header") != "X-Recon-Download-Ticket"
        or delivery.get("single_use") is not True
        or not str(delivery.get("endpoint") or "").startswith(
            f"https://{expected_host}/download"
        )
        or not isinstance(attestation, dict)
        or attestation.get("algorithm") != "Ed25519"
        or not str(attestation.get("public_key") or "")
        or len(str(attestation.get("public_key_sha256") or "")) != 64
        or not isinstance(limits, dict)
        or not 0 < int(limits.get("max_candidates", 0)) <= 50
        or not isinstance(providers, dict)
        or {
            name: set(extensions)
            for name, extensions in providers.items()
        }
        != EXPECTED_PROVIDER_EXTENSIONS
    ):
        raise RuntimeError(
            "sharepoint_mcp_invalid: capability result does not match the five-tool contract."
        )
    if (
        not isinstance(probe_envelope, dict)
        or set(probe_envelope) != {"schema_version", "kind", "result"}
        or probe_envelope.get("schema_version") != "1.0"
        or probe_envelope.get("kind") != "probe"
        or not isinstance(probe_envelope.get("result"), dict)
    ):
        raise RuntimeError(
            "sharepoint_mcp_invalid: expected unchanged probe envelope."
        )
    probe = probe_envelope["result"]
    if (
        probe.get("status") != "ok"
        or probe.get("environment") != expected_environment
        or probe.get("reachable") is not True
        or probe.get("site_name") != "Nexon Reconciliation Automation"
        or not str(probe.get("hostname") or "").strip()
        or not str(probe.get("path") or "").startswith("/")
        or not {"upload", "reference", "result"}.issubset(
            set(probe.get("spaces", []))
        )
    ):
        raise RuntimeError(
            "sharepoint_mcp_invalid: probe receipt does not prove the required spaces."
        )


def capability_manifest(
    config: dict,
    *,
    local_check: bool,
    sharepoint_mcp_validated: bool = False,
    database_mcp_validated: bool = False,
    database_mcp_persistence: bool = False,
) -> dict:
    features = config.get("features", {})
    billing_enabled = features.get("billing_query_enabled") is True
    core_enabled = features.get("core_persistence_enabled") is True
    billing_dsn = bool(os.environ.get("NEXON_RECON_BILLING_DSN"))
    core_dsn = bool(os.environ.get("NEXON_RECON_CORE_DSN"))
    core_mode = os.environ.get("NEXON_RECON_CORE_MODE", "").strip().lower()
    core_ready = bool(
        core_enabled
        and (
            database_mcp_persistence
            or (
                core_dsn
                and (
                    core_mode in {"sqlserver", "azure_sql"}
                    or (local_check and core_mode == "sqlite_shadow")
                )
            )
        )
    )
    return {
        "contract_version": 1,
        "capabilities": {
            "binary_source_staging": bool(
                local_check or sharepoint_mcp_validated
            ),
            "provider_parsing": True,
            "archive_validation": True,
            "core_supplier_persistence": core_ready,
            "request_scoped_billing_preparation": bool(
                billing_enabled and (database_mcp_validated or billing_dsn)
            ),
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


def execution_policy(
    config: dict,
    capabilities: dict,
    *,
    run_mode: str,
    intake_mode: str,
    provider: str,
    local_only: bool,
) -> dict:
    """Resolve configured intent against observed runtime capabilities."""
    if run_mode not in {"parser_validation", "reconciliation"}:
        raise RuntimeError("execution_policy_invalid: unsupported run mode.")
    if intake_mode not in {"manual_upload", "provider_api"}:
        raise RuntimeError("execution_policy_invalid: unsupported intake mode.")
    if provider not in PROVIDERS:
        raise RuntimeError("execution_policy_invalid: unsupported provider.")

    features = config.get("features", {})
    if not isinstance(features, dict):
        features = {}
    adapters = config.get("provider_api_adapters", {})
    if not isinstance(adapters, dict):
        adapters = {}
    observed = capabilities.get("capabilities", {})
    if not isinstance(observed, dict):
        observed = {}

    decisions: dict[str, dict] = {}
    blockers: list[str] = []

    def decide(
        name: str,
        *,
        enabled: bool,
        required: bool,
        available: bool | None,
        owner: str,
        enabled_action: str = "execute",
    ) -> None:
        if not enabled:
            action = "block" if required else "skip"
        elif available is False:
            action = "block"
        else:
            action = enabled_action
        decisions[name] = {
            "enabled": enabled,
            "required": required,
            "available": available,
            "owner": owner,
            "action": action,
        }
        if action == "block":
            blockers.append(name)

    manual_intake = intake_mode == "manual_upload"
    provider_api = intake_mode == "provider_api"
    reconciliation = run_mode == "reconciliation"
    fleet_run = not local_only
    provider_key = provider.lower()

    decide(
        "sharepoint_binary_intake",
        enabled=manual_intake and fleet_run,
        required=manual_intake and fleet_run,
        available=None,
        owner="sharepoint_mcp",
        enabled_action="binding_check_required",
    )
    decide(
        "provider_api_intake",
        enabled=provider_api,
        required=provider_api,
        available=(
            features.get("provider_api_enabled") is True
            and adapters.get(provider_key) is True
        ),
        owner="provider_adapter",
    )
    decide(
        "billing_query",
        enabled=reconciliation and features.get("billing_query_enabled") is True,
        required=reconciliation,
        available=(
            observed.get("request_scoped_billing_preparation") is True
            if reconciliation
            else None
        ),
        owner="database_mcp" if fleet_run else "local_billing_adapter",
    )
    decide(
        "deterministic_matching",
        enabled=(
            reconciliation
            and features.get("deterministic_matching_enabled") is True
        ),
        required=reconciliation,
        available=(
            observed.get("deterministic_comparison") is True
            if reconciliation
            else None
        ),
        owner="reconciliation_runtime",
    )
    core_enabled = features.get("core_persistence_enabled") is True
    decide(
        "core_persistence",
        enabled=core_enabled and reconciliation,
        required=core_enabled and reconciliation,
        available=(
            observed.get("core_supplier_persistence") is True
            and observed.get("core_result_persistence") is True
            if core_enabled and reconciliation
            else None
        ),
        owner="database_mcp" if fleet_run else "local_persistence_adapter",
    )
    decide(
        "accepted_resolution_update",
        enabled=features.get("db_update_enabled") is True,
        required=False,
        available=observed.get("accepted_resolution_update") is True,
        owner="database_mcp",
    )
    decide(
        "sharepoint_publication",
        enabled=reconciliation and fleet_run,
        required=reconciliation and fleet_run,
        available=None,
        owner="native_sharepoint_and_sharepoint_mcp",
        enabled_action="binding_check_required",
    )
    failure_handling = config.get("failure_handling", {})
    if not isinstance(failure_handling, dict):
        failure_handling = {}
    notifications_enabled = (
        features.get("failure_notifications_enabled") is True
        and failure_handling.get("notify_operator") is True
    )
    decide(
        "failure_notification",
        enabled=notifications_enabled,
        required=False,
        available=None,
        owner="native_outlook",
        enabled_action="binding_check_required",
    )
    decisions["exception_investigation"] = {
        "enabled": reconciliation,
        "required": False,
        "available": None,
        "owner": "nexon-recon-exception-investigator_and_database_mcp",
        "action": "conditional_on_unresolved_rows" if reconciliation else "skip",
    }

    return {
        "contract_version": 1,
        "environment_agnostic": True,
        "run_mode": run_mode,
        "intake_mode": intake_mode,
        "provider": provider,
        "execution_mode": "local" if local_only else "fleet",
        "decisions": decisions,
        "blockers": blockers,
        "status": "blocked" if blockers else "ready",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate one-time Nexon recon folder setup.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--local-check", action="store_true", help="Treat fixed roots as local filesystem paths.")
    parser.add_argument("--sharepoint-mcp-capabilities", type=Path)
    parser.add_argument("--sharepoint-mcp-probe", type=Path)
    parser.add_argument("--database-mcp-capabilities", type=Path)
    parser.add_argument("--database-mcp-probe", type=Path)
    parser.add_argument("--output", type=Path, help="Write a machine-readable capability manifest.")
    parser.add_argument("--run-mode", choices=("parser_validation", "reconciliation"))
    parser.add_argument("--intake-mode", choices=("manual_upload", "provider_api"))
    parser.add_argument("--provider", choices=sorted(PROVIDERS))
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

    mcp_validated = False
    if not args.local_check:
        if (
            args.sharepoint_mcp_capabilities is None
            or args.sharepoint_mcp_probe is None
        ):
            raise RuntimeError(
                "sharepoint_mcp_required: capability and probe receipts are required."
            )
        validate_sharepoint_mcp_receipts(
            args.sharepoint_mcp_capabilities.resolve(),
            args.sharepoint_mcp_probe.resolve(),
            config,
        )
        mcp_validated = True

    database_validated = False
    database_persistence = False
    if args.run_mode == "reconciliation" and not args.local_check:
        if (
            args.database_mcp_capabilities is None
            or args.database_mcp_probe is None
        ):
            raise RuntimeError(
                "database_mcp_required: reconciliation preflight requires capability and probe receipts."
            )
        database_config = config.get("database_mcp", {})
        environment = (
            str(database_config.get("environment") or "").strip().lower()
            if isinstance(database_config, dict)
            else ""
        )
        if not environment:
            raise RuntimeError(
                "database_mcp_config_invalid: database_mcp.environment is required."
            )
        database_identity = validate_database_mcp(
            read_json(args.database_mcp_capabilities.resolve()),
            read_json(args.database_mcp_probe.resolve()),
            environment=environment,
            require_persistence=(
                config.get("features", {}).get("core_persistence_enabled") is True
            ),
            row_limit=int(
                config.get("limits", {}).get("billing_query_row_limit", 5000)
            ),
        )
        database_validated = True
        database_persistence = database_identity["core_persistence"] is True

    capabilities = capability_manifest(
        config,
        local_check=args.local_check,
        sharepoint_mcp_validated=mcp_validated,
        database_mcp_validated=database_validated,
        database_mcp_persistence=database_persistence,
    )
    policy_arguments = (args.run_mode, args.intake_mode, args.provider)
    if any(policy_arguments) and not all(policy_arguments):
        raise RuntimeError(
            "execution_policy_invalid: --run-mode, --intake-mode, and --provider must be supplied together."
        )
    if all(policy_arguments):
        capabilities["execution_policy"] = execution_policy(
            config,
            capabilities,
            run_mode=args.run_mode,
            intake_mode=args.intake_mode,
            provider=args.provider,
            local_only=args.local_check,
        )
    if args.output:
        write_json(args.output, capabilities)

    if not args.local_check:
        print("Setup config and SharePoint Intake MCP receipts validated.")
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
