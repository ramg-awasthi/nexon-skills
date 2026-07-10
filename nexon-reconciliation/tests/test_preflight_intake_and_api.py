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

from recon_core import provider_api_download  # noqa: E402


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
features:
  provider_api_enabled: {str(provider_api_enabled).lower()}
  billing_query_enabled: false
  db_update_enabled: {str(db_update_enabled).lower()}
  failure_notifications_enabled: false
provider_api_adapters:
{adapters}billing:
  mode: read_only_sql
  agent_sql_allowed: true
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
    def test_preflight_accepts_default_sharepoint_tool_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.yaml"
            write_config(config)

            result = subprocess.run(
                [sys.executable, str(SCRIPTS / "preflight_check.py"), "--config", str(config)],
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(0, result.returncode, result.stderr + result.stdout)
        self.assertIn("native SharePoint tool", result.stdout)

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

    def test_preflight_local_check_reports_missing_fixed_folders(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.yaml"
            write_config(config)

            result = subprocess.run(
                [sys.executable, str(SCRIPTS / "preflight_check.py"), "--config", str(config), "--local-check"],
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertIn(result.returncode, {0, 2})
        self.assertRegex(result.stdout, r"(Missing upload folder|Local setup validation passed)")

    def test_intake_run_copy_creates_run_package_and_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "config.yaml"
            result_root = root / "result"
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
            self.assertEqual("no_match", read_json(output)["rows"][0]["agent_match_status"])

    def test_safe_unpack_cli_writes_blocked_manifest_for_unsafe_zip(self) -> None:
        import zipfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "unsafe.zip"
            manifest = root / "manifest.json"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("../escape.txt", "bad")

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "safe_unpack.py"),
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

    def test_run_recon_fails_closed_until_full_runner_is_approved(self) -> None:
        result = subprocess.run(
            [sys.executable, str(SCRIPTS / "run_recon.py"), "--provider", "AAPT"],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertNotEqual(0, result.returncode)
        self.assertIn("integration_unavailable", result.stderr + result.stdout)


if __name__ == "__main__":
    unittest.main()
