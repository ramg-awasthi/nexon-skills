from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from recon_core import preflight_check, provider_api_download  # noqa: E402


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
