from __future__ import annotations

import json
import hashlib
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from recon_core import preflight_check, provider_api_download, validate_run  # noqa: E402


def valid_mcp_receipts() -> tuple[dict, dict]:
    capabilities = {
        "schema_version": "1.0",
        "kind": "capabilities",
        "result": {
            "status": "ok",
            "environment": "dev",
            "read_only": True,
            "download_contract_version": 1,
            "tools": sorted(preflight_check.SHAREPOINT_INTAKE_TOOLS),
            "binary_delivery": {
                "method": "POST",
                "endpoint": (
                    "https://nexon-recon-sharepoint-dev.netbird.aaic.cc/download"
                ),
                "ticket_header": "X-Recon-Download-Ticket",
                "single_use": True,
            },
            "attestation": {
                "algorithm": "Ed25519",
                "public_key": "A" * 43,
                "public_key_sha256": "a" * 64,
            },
            "limits": {"max_candidates": 50},
            "providers": {
                name: sorted(extensions)
                for name, extensions in preflight_check.EXPECTED_PROVIDER_EXTENSIONS.items()
            },
        },
    }
    probe = {
        "schema_version": "1.0",
        "kind": "probe",
        "result": {
            "status": "ok",
            "environment": "dev",
            "reachable": True,
            "site_name": "Nexon Reconciliation Automation",
            "hostname": "tenant.sharepoint.com",
            "path": "/sites/NexonReconciliationAutomation",
            "spaces": ["upload", "reference", "result"],
        },
    }
    return capabilities, probe


def valid_database_receipts() -> tuple[dict, dict]:
    return (
        {
            "service": "nexon-recon-db-mcp",
            "environment": "dev",
            "capabilities": {"read_queries": True, "core_persistence": False},
            "query_policy": {
                "read_only": True,
                "schema_qualified_allowlist": True,
                "comments_allowed": False,
                "wildcard_projection_allowed": False,
                "row_limit": 5000,
                "audit_required": True,
            },
        },
        {
            "environment": "dev",
            "reachable": True,
            "database_name": "test_database",
        },
    )


def write_config(
    path: Path,
    *,
    db_update_enabled: bool = False,
    provider_api_enabled: bool = False,
    adapters: str = "  equinix: true\n",
) -> None:
    path.write_text(
        f"""
timezone: Australia/Sydney
sharepoint_intake:
  environment: dev
  gateway_host: nexon-recon-sharepoint-dev.netbird.aaic.cc
features:
  provider_api_enabled: {str(provider_api_enabled).lower()}
  billing_query_enabled: false
  db_update_enabled: {str(db_update_enabled).lower()}
  failure_notifications_enabled: false
provider_api_adapters:
{adapters}billing:
  mode: read_only_sql
  agent_sql_allowed: true
  audit_required: true
reports:
  evidence_summary:
    auto_matched: short
    max_chars: 160
failure_handling:
  notify_operator: false
  notification_mode: outlook
  notification_content: text_only
""".lstrip(),
        encoding="utf-8",
    )


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class PreflightIntakeAndProviderApiTests(unittest.TestCase):
    def test_execution_policy_resolves_optional_and_required_integrations(self) -> None:
        config = {
            "features": {
                "provider_api_enabled": False,
                "billing_query_enabled": True,
                "core_persistence_enabled": False,
                "deterministic_matching_enabled": True,
                "db_update_enabled": False,
                "failure_notifications_enabled": False,
            },
            "provider_api_adapters": {"equinix": True},
            "failure_handling": {"notify_operator": False},
        }
        capabilities = {
            "capabilities": {
                "request_scoped_billing_preparation": True,
                "deterministic_comparison": True,
                "core_supplier_persistence": False,
                "core_result_persistence": False,
                "accepted_resolution_update": False,
            }
        }

        policy = preflight_check.execution_policy(
            config,
            capabilities,
            run_mode="reconciliation",
            intake_mode="manual_upload",
            provider="AAPT",
            local_only=False,
        )

        self.assertEqual("ready", policy["status"])
        self.assertTrue(policy["environment_agnostic"])
        decisions = policy["decisions"]
        self.assertEqual(
            "binding_check_required", decisions["sharepoint_binary_intake"]["action"]
        )
        self.assertEqual("execute", decisions["billing_query"]["action"])
        self.assertEqual("execute", decisions["deterministic_matching"]["action"])
        self.assertEqual("skip", decisions["core_persistence"]["action"])
        self.assertEqual("skip", decisions["accepted_resolution_update"]["action"])
        self.assertEqual(
            "binding_check_required", decisions["sharepoint_publication"]["action"]
        )
        self.assertEqual("skip", decisions["failure_notification"]["action"])
        self.assertEqual(
            "conditional_on_unresolved_rows",
            decisions["exception_investigation"]["action"],
        )

        config["features"]["core_persistence_enabled"] = True
        blocked = preflight_check.execution_policy(
            config,
            capabilities,
            run_mode="reconciliation",
            intake_mode="manual_upload",
            provider="AAPT",
            local_only=False,
        )
        self.assertEqual("blocked", blocked["status"])
        self.assertEqual(["core_persistence"], blocked["blockers"])

    def test_execution_policy_handles_provider_api_notifications_and_validation(self) -> None:
        config = {
            "features": {
                "provider_api_enabled": True,
                "billing_query_enabled": True,
                "core_persistence_enabled": False,
                "deterministic_matching_enabled": True,
                "db_update_enabled": False,
                "failure_notifications_enabled": True,
            },
            "provider_api_adapters": {"equinix": True},
            "failure_handling": {"notify_operator": True},
        }
        capabilities = {
            "capabilities": {
                "request_scoped_billing_preparation": True,
                "deterministic_comparison": True,
                "accepted_resolution_update": False,
            }
        }
        policy = preflight_check.execution_policy(
            config,
            capabilities,
            run_mode="reconciliation",
            intake_mode="provider_api",
            provider="Equinix",
            local_only=False,
        )
        self.assertEqual("execute", policy["decisions"]["provider_api_intake"]["action"])
        self.assertEqual("skip", policy["decisions"]["sharepoint_binary_intake"]["action"])
        self.assertEqual(
            "binding_check_required", policy["decisions"]["failure_notification"]["action"]
        )

        config["provider_api_adapters"] = []
        blocked = preflight_check.execution_policy(
            config,
            capabilities,
            run_mode="reconciliation",
            intake_mode="provider_api",
            provider="Equinix",
            local_only=False,
        )
        self.assertEqual(["provider_api_intake"], blocked["blockers"])

        for kwargs in (
            {"run_mode": "bad", "intake_mode": "manual_upload", "provider": "AAPT"},
            {"run_mode": "reconciliation", "intake_mode": "bad", "provider": "AAPT"},
            {"run_mode": "reconciliation", "intake_mode": "manual_upload", "provider": "Bad"},
        ):
            with self.assertRaisesRegex(RuntimeError, "execution_policy_invalid"):
                preflight_check.execution_policy(
                    config, capabilities, local_only=False, **kwargs
                )

        fallback = preflight_check.execution_policy(
            {"features": [], "provider_api_adapters": [], "failure_handling": []},
            {"capabilities": []},
            run_mode="parser_validation",
            intake_mode="manual_upload",
            provider="AAPT",
            local_only=True,
        )
        self.assertEqual("ready", fallback["status"])

    def test_execution_policy_artifact_validation_fails_closed(self) -> None:
        base_policy = {
            "contract_version": 1,
            "environment_agnostic": True,
            "status": "ready",
            "blockers": [],
            "run_mode": "reconciliation",
            "intake_mode": "manual_upload",
            "provider": "AAPT",
            "decisions": {
                "core_persistence": {"enabled": False, "action": "skip"},
                "accepted_resolution_update": {"enabled": False, "action": "skip"},
            },
        }
        base_manifest = {
            "run_mode": "reconciliation",
            "intake_mode": "manual_upload",
            "provider": "AAPT",
            "core_persistence_enabled": False,
        }
        with tempfile.TemporaryDirectory() as tmp:
            run_root = Path(tmp)
            manifest_dir = run_root / "manifest"
            manifest_dir.mkdir()
            policy_path = manifest_dir / "execution_policy.json"

            with self.assertRaisesRegex(RuntimeError, "must both exist"):
                validate_run.assert_execution_policy(
                    run_root, {**base_manifest, "execution_policy_sha256": "0" * 64}
                )

            policy_path.write_text(json.dumps(base_policy), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "hash does not match"):
                validate_run.assert_execution_policy(
                    run_root, {**base_manifest, "execution_policy_sha256": "0" * 64}
                )

            def manifest_for(policy: dict) -> dict:
                policy_path.write_text(json.dumps(policy), encoding="utf-8")
                return {
                    **base_manifest,
                    "execution_policy_sha256": hashlib.sha256(
                        policy_path.read_bytes()
                    ).hexdigest(),
                }

            invalid_identity = {**base_policy, "status": "blocked"}
            with self.assertRaisesRegex(RuntimeError, "identity or ready state"):
                validate_run.assert_execution_policy(
                    run_root, manifest_for(invalid_identity)
                )

            core_drift = json.loads(json.dumps(base_policy))
            core_drift["decisions"]["core_persistence"]["action"] = "execute"
            with self.assertRaisesRegex(RuntimeError, "core-persistence decision"):
                validate_run.assert_execution_policy(
                    run_root, manifest_for(core_drift)
                )

            update_drift = json.loads(json.dumps(base_policy))
            update_drift["decisions"]["accepted_resolution_update"]["enabled"] = True
            with self.assertRaisesRegex(RuntimeError, "accepted-resolution updates"):
                validate_run.assert_execution_policy(
                    run_root, manifest_for(update_drift)
                )

    def test_preflight_emits_execution_policy_when_run_context_is_complete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "config.yaml"
            write_config(config)
            output = root / "output.json"
            old_argv = sys.argv
            try:
                sys.argv = [
                    "preflight_check.py",
                    "--config",
                    str(config),
                    "--local-check",
                    "--run-mode",
                    "parser_validation",
                    "--intake-mode",
                    "manual_upload",
                    "--provider",
                    "AAPT",
                    "--output",
                    str(output),
                ]
                self.assertEqual(2, preflight_check.main())
                self.assertEqual(
                    "ready", read_json(output)["execution_policy"]["status"]
                )

                sys.argv = [
                    "preflight_check.py",
                    "--config",
                    str(config),
                    "--local-check",
                    "--run-mode",
                    "parser_validation",
                ]
                with self.assertRaisesRegex(RuntimeError, "must be supplied together"):
                    preflight_check.main()
            finally:
                sys.argv = old_argv

    def test_reconciliation_preflight_requires_and_consumes_database_mcp_receipts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "config.yaml"
            write_config(config)
            config.write_text(
                config.read_text(encoding="utf-8")
                .replace("billing_query_enabled: false", "billing_query_enabled: true")
                .replace(
                    "  db_update_enabled: false",
                    "  core_persistence_enabled: false\n"
                    "  deterministic_matching_enabled: true\n"
                    "  db_update_enabled: false",
                )
                .replace(
                    "features:\n",
                    "database_mcp:\n  environment: dev\nfeatures:\n",
                ),
                encoding="utf-8",
            )
            sp_capabilities = root / "sp-capabilities.json"
            sp_probe = root / "sp-probe.json"
            db_capabilities = root / "db-capabilities.json"
            db_probe = root / "db-probe.json"
            output = root / "output.json"
            sp_capability_payload, sp_probe_payload = valid_mcp_receipts()
            db_capability_payload, db_probe_payload = valid_database_receipts()
            for path, payload in (
                (sp_capabilities, sp_capability_payload),
                (sp_probe, sp_probe_payload),
                (db_capabilities, db_capability_payload),
                (db_probe, db_probe_payload),
            ):
                path.write_text(json.dumps(payload), encoding="utf-8")

            base = [
                "preflight_check.py",
                "--config",
                str(config),
                "--run-mode",
                "reconciliation",
                "--intake-mode",
                "manual_upload",
                "--provider",
                "AAPT",
                "--sharepoint-mcp-capabilities",
                str(sp_capabilities),
                "--sharepoint-mcp-probe",
                str(sp_probe),
            ]
            old_argv = sys.argv
            try:
                sys.argv = base
                with self.assertRaisesRegex(RuntimeError, "database_mcp_required"):
                    preflight_check.main()

                complete = [
                    *base,
                    "--database-mcp-capabilities",
                    str(db_capabilities),
                    "--database-mcp-probe",
                    str(db_probe),
                    "--output",
                    str(output),
                ]
                valid_config_text = config.read_text(encoding="utf-8")
                config.write_text(
                    valid_config_text.replace(
                        "database_mcp:\n  environment: dev", "database_mcp: []"
                    ),
                    encoding="utf-8",
                )
                sys.argv = complete
                with self.assertRaisesRegex(RuntimeError, "environment is required"):
                    preflight_check.main()

                config.write_text(valid_config_text, encoding="utf-8")
                sys.argv = complete
                self.assertEqual(0, preflight_check.main())
            finally:
                sys.argv = old_argv

            payload = read_json(output)
            self.assertEqual("ready", payload["execution_policy"]["status"])
            self.assertEqual(
                "execute",
                payload["execution_policy"]["decisions"]["billing_query"]["action"],
            )

    def test_sharepoint_mcp_receipt_validation_rejects_contract_mutations(self) -> None:
        capabilities, probe = valid_mcp_receipts()
        config = {
            "sharepoint_intake": {
                "environment": "dev",
                "gateway_host": (
                    "nexon-recon-sharepoint-dev.netbird.aaic.cc"
                ),
            }
        }
        capability_mutations = [
            [],
            {**capabilities, "extra": True},
            {**capabilities, "schema_version": "2.0"},
            {**capabilities, "kind": "probe"},
            {**capabilities, "result": []},
            {**capabilities, "result": {**capabilities["result"], "status": "error"}},
            {**capabilities, "result": {**capabilities["result"], "tools": []}},
            {
                **capabilities,
                "result": {
                    **capabilities["result"],
                    "download_contract_version": 2,
                },
            },
        ]
        probe_mutations = [
            [],
            {**probe, "extra": True},
            {**probe, "schema_version": "2.0"},
            {**probe, "kind": "capabilities"},
            {**probe, "result": []},
            {**probe, "result": {**probe["result"], "status": "error"}},
            {**probe, "result": {**probe["result"], "reachable": False}},
            {**probe, "result": {**probe["result"], "site_name": ""}},
            {**probe, "result": {**probe["result"], "hostname": ""}},
            {**probe, "result": {**probe["result"], "path": "sites/recon"}},
            {**probe, "result": {**probe["result"], "spaces": ["upload"]}},
        ]

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            capabilities_path = root / "capabilities.json"
            probe_path = root / "probe.json"

            for mutation in capability_mutations:
                capabilities_path.write_text(json.dumps(mutation), encoding="utf-8")
                probe_path.write_text(json.dumps(probe), encoding="utf-8")
                with self.assertRaisesRegex(RuntimeError, "capabilities envelope|five-tool contract"):
                    preflight_check.validate_sharepoint_mcp_receipts(
                        capabilities_path, probe_path, config
                    )

            for mutation in probe_mutations:
                capabilities_path.write_text(
                    json.dumps(capabilities), encoding="utf-8"
                )
                probe_path.write_text(json.dumps(mutation), encoding="utf-8")
                with self.assertRaisesRegex(RuntimeError, "probe envelope|required spaces"):
                    preflight_check.validate_sharepoint_mcp_receipts(
                        capabilities_path, probe_path, config
                    )

    def test_preflight_accepts_sharepoint_intake_mcp_receipts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "config.yaml"
            write_config(config)
            capabilities = root / "capabilities.json"
            probe = root / "probe.json"
            capability_payload, probe_payload = valid_mcp_receipts()
            capabilities.write_text(json.dumps(capability_payload), encoding="utf-8")
            probe.write_text(json.dumps(probe_payload), encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "preflight_check.py"),
                    "--config",
                    str(config),
                    "--sharepoint-mcp-capabilities",
                    str(capabilities),
                    "--sharepoint-mcp-probe",
                    str(probe),
                ],
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(0, result.returncode, result.stderr + result.stdout)
        self.assertIn("SharePoint Intake MCP receipts validated", result.stdout)

    def test_preflight_rejects_bad_provider_api_adapter_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.yaml"
            write_config(config, adapters="  equinix: false\n  unknown: true\n")

            result = subprocess.run(
                [sys.executable, str(SCRIPTS / "preflight_check.py"), "--config", str(config)],
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertNotEqual(0, result.returncode)
        self.assertIn("Unsupported provider API adapter", result.stderr + result.stdout)

    def test_preflight_rejects_non_mapping_and_disabled_adapter_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "config.yaml"
            config.write_text(
                """
features:
  db_update_enabled: false
provider_api_adapters: []
billing:
  audit_required: true
""".lstrip(),
                encoding="utf-8",
            )
            old_argv = sys.argv
            try:
                sys.argv = [
                    "preflight_check.py",
                    "--config",
                    str(config),
                    "--local-check",
                ]
                with self.assertRaisesRegex(RuntimeError, "must be a mapping"):
                    preflight_check.main()

                write_config(config, adapters="  equinix: false\n")
                with self.assertRaisesRegex(RuntimeError, "Remove disabled"):
                    preflight_check.main()
            finally:
                sys.argv = old_argv

    def test_preflight_requires_both_mcp_receipts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.yaml"
            write_config(config)
            old_argv = sys.argv
            try:
                sys.argv = ["preflight_check.py", "--config", str(config)]
                with self.assertRaisesRegex(RuntimeError, "sharepoint_mcp_required"):
                    preflight_check.main()
            finally:
                sys.argv = old_argv

    def test_preflight_local_check_reports_missing_fixed_folders(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "config.yaml"
            output = root / "capabilities.json"
            write_config(config)
            old_argv = sys.argv
            original_roots = preflight_check.sharepoint_roots
            try:
                preflight_check.sharepoint_roots = lambda _config: (
                    root / "missing-upload",
                    root / "missing-result",
                )
                sys.argv = [
                    "preflight_check.py",
                    "--config",
                    str(config),
                    "--local-check",
                    "--output",
                    str(output),
                ]
                self.assertEqual(2, preflight_check.main())
            finally:
                preflight_check.sharepoint_roots = original_roots
                sys.argv = old_argv

            self.assertTrue(output.is_file())

    def test_preflight_local_check_passes_when_all_fixed_folders_exist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "config.yaml"
            upload_root = root / "upload"
            result_root = root / "result"
            for provider in preflight_check.PROVIDERS:
                (upload_root / provider).mkdir(parents=True)
                (result_root / provider).mkdir(parents=True)
            write_config(config)
            old_argv = sys.argv
            original_roots = preflight_check.sharepoint_roots
            try:
                preflight_check.sharepoint_roots = lambda _config: (
                    upload_root,
                    result_root,
                )
                sys.argv = [
                    "preflight_check.py",
                    "--config",
                    str(config),
                    "--local-check",
                ]
                self.assertEqual(0, preflight_check.main())
            finally:
                preflight_check.sharepoint_roots = original_roots
                sys.argv = old_argv

    def test_intake_run_copy_creates_run_package_and_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "config.yaml"
            result_root = root / "result"
            (result_root / "AAPT").mkdir(parents=True)
            source = root / "invoice.csv"
            source.write_text("service,amount\nSVC-1,12\n", encoding="utf-8")
            write_config(config)

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "intake_run.py"),
                    "--config",
                    str(config),
                    "--provider",
                    "AAPT",
                    "--source-file",
                    str(source),
                    "--result-root",
                    str(result_root),
                    "--source-identity",
                    "fixture-1",
                    "--copy",
                ],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(0, result.returncode, result.stderr + result.stdout)
            run_root = Path(result.stdout.strip())
            manifest = read_json(run_root / "manifest" / "run_manifest.json")
            self.assertTrue(source.exists())
            self.assertTrue((run_root / "source" / source.name).is_file())
            self.assertEqual("copy", manifest["intake_action"])
            self.assertEqual("fixture-1", manifest["source_identity"])
            self.assertFalse(manifest["db_update_enabled"])

    def test_provider_api_download_rejects_disabled_feature_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.yaml"
            write_config(config, provider_api_enabled=False)

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "provider_api_download.py"),
                    "--config",
                    str(config),
                    "--provider",
                    "Equinix",
                    "--account-id",
                    "acct",
                    "--invoice-id",
                    "inv",
                ],
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertNotEqual(0, result.returncode)
        self.assertIn("provider_api_not_available", result.stderr + result.stdout)

    def test_provider_api_download_requires_equinix_invoice_identifiers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.yaml"
            write_config(config, provider_api_enabled=True)

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "provider_api_download.py"),
                    "--config",
                    str(config),
                    "--provider",
                    "Equinix",
                ],
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertNotEqual(0, result.returncode)
        self.assertIn("provider_api_listing_unavailable", result.stderr + result.stdout)

    def test_provider_api_download_requires_equinix_credentials_after_identifiers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.yaml"
            write_config(config, provider_api_enabled=True)
            env = os.environ.copy()
            for key in list(env):
                if key.startswith("NEXON_RECON_PROVIDER_API_"):
                    env.pop(key)

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "provider_api_download.py"),
                    "--config",
                    str(config),
                    "--provider",
                    "Equinix",
                    "--account-id",
                    "acct",
                    "--invoice-id",
                    "inv",
                ],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertNotEqual(0, result.returncode)
        self.assertIn("provider_api_credentials_missing", result.stderr + result.stdout)

    def test_provider_api_download_fails_closed_for_unimplemented_enabled_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.yaml"
            write_config(config, provider_api_enabled=True, adapters="  equinix: true\n  aapt: true\n")

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "provider_api_download.py"),
                    "--config",
                    str(config),
                    "--provider",
                    "AAPT",
                ],
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertNotEqual(0, result.returncode)
        self.assertIn("integration_unavailable", result.stderr + result.stdout)

    def test_match_recon_cli_classifies_normalized_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "config.yaml"
            normalized = root / "normalized.json"
            candidates = root / "candidates.json"
            output = root / "matches.json"
            write_config(config)
            normalized.write_text(json.dumps({"lines": [{"line_id": "line-1", "provider": "AAPT"}]}), encoding="utf-8")
            candidates.write_text(json.dumps({"candidates_by_line": {"line-1": []}}), encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "match_recon.py"),
                    "--config",
                    str(config),
                    "--normalized",
                    str(normalized),
                    "--candidates",
                    str(candidates),
                    "--output",
                    str(output),
                ],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(0, result.returncode, result.stderr + result.stdout)
            self.assertEqual("Supplier Only", read_json(output)["rows"][0]["ReconMatchStatus"])

    def test_safe_unpack_cli_writes_blocked_manifest_for_unsafe_zip(self) -> None:
        import zipfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "unsafe.zip"
            manifest = root / "manifest.json"
            config = root / "config.yaml"
            write_config(config)
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("../escape.txt", "bad")

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "safe_unpack.py"),
                    "--config",
                    str(config),
                    "--zip",
                    str(archive),
                    "--output-dir",
                    str(root / "out"),
                    "--manifest",
                    str(manifest),
                ],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(2, result.returncode)
            self.assertTrue(read_json(manifest)["blocked"])

    def test_equinix_provider_api_adapter_writes_download_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output_dir = root / "downloads"
            manifest = root / "manifest.json"
            calls: list[tuple[str, object]] = []
            original_http_json = provider_api_download._http_json
            original_http_download = provider_api_download._http_download
            env = os.environ.copy()
            os.environ["NEXON_RECON_PROVIDER_API_CLIENT_ID_EQUINIX"] = "client"
            os.environ["NEXON_RECON_PROVIDER_API_CLIENT_SECRET_EQUINIX"] = "secret"
            try:
                def fake_http_json(url: str, payload: dict, headers: dict[str, str] | None = None) -> dict:
                    calls.append(("token", url, payload))
                    return {"access_token": "token-1"}

                def fake_http_download(url: str, token: str, destination: Path) -> None:
                    calls.append(("download", url, token))
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(b"pdf")

                provider_api_download._http_json = fake_http_json
                provider_api_download._http_download = fake_http_download
                destination = provider_api_download._equinix_download(
                    SimpleNamespace(
                        account_id="acct-1",
                        invoice_id="inv-1",
                        document_id=None,
                        output_dir=output_dir,
                        output_name=None,
                        manifest=manifest,
                    ),
                    {"provider": "Equinix", "provider_api_adapter_enabled": True},
                )
            finally:
                provider_api_download._http_json = original_http_json
                provider_api_download._http_download = original_http_download
                os.environ.clear()
                os.environ.update(env)

            self.assertTrue(destination.is_file())
            payload = read_json(manifest)
            self.assertEqual("Equinix", payload["provider"])
            self.assertEqual("DETAILED_PDF_EN", payload["document_id"])
            self.assertEqual("equinix", payload["adapter"])
            self.assertEqual("token", calls[0][0])
            self.assertEqual("download", calls[1][0])

    def test_provider_api_http_helpers_without_live_network(self) -> None:
        class FakeJsonResponse:
            def __enter__(self) -> "FakeJsonResponse":
                return self

            def __exit__(self, *_args: object) -> None:
                return None

            def read(self) -> bytes:
                return b'{"access_token": "token"}'

        class FakeDownloadResponse:
            def __enter__(self) -> "FakeDownloadResponse":
                return self

            def __exit__(self, *_args: object) -> None:
                return None

            def read(self) -> bytes:
                return b"file"

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            destination = root / "file.pdf"
            old_urlopen = provider_api_download.urlopen
            calls: list[object] = []
            try:
                def fake_urlopen(request: object, timeout: int) -> object:
                    calls.append((request, timeout))
                    if len(calls) == 1:
                        return FakeJsonResponse()
                    return FakeDownloadResponse()

                provider_api_download.urlopen = fake_urlopen
                self.assertEqual({"access_token": "token"}, provider_api_download._http_json("https://example.test/token", {"grant": "client"}))
                provider_api_download._http_download("https://example.test/invoice", "token", destination)
            finally:
                provider_api_download.urlopen = old_urlopen

            self.assertEqual(b"file", destination.read_bytes())
            self.assertEqual(2, len(calls))

    def test_run_recon_requires_explicit_new_run_inputs(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "run_recon.py"),
                "--config",
                str(Path(__file__).resolve().parents[1] / "config" / "recon_settings.yaml"),
                "--provider",
                "AAPT",
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertNotEqual(0, result.returncode)
        self.assertIn("run_input_missing", result.stderr + result.stdout)


if __name__ == "__main__":
    unittest.main()
