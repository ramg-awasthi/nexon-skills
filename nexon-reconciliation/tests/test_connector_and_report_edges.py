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

from recon_core import apply_exception_investigation, billing_query, validate_run  # noqa: E402
from recon_core import sharepoint_connector  # noqa: E402
from recon_core.common import RunPaths, evidence_summary_policy, ensure_provider, provider_api_adapter_enabled  # noqa: E402
from recon_core.match_recon import has_exact_match_evidence  # noqa: E402
from recon_core.write_reports import ordered_columns, with_refined_defaults  # noqa: E402


def write_config(path: Path) -> None:
    path.write_text(
        """
timezone: Australia/Sydney
features:
  provider_api_enabled: false
  billing_query_enabled: false
  db_update_enabled: false
  failure_notifications_enabled: false
provider_api_adapters:
  equinix: true
billing:
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


class ConnectorAndReportEdgeTests(unittest.TestCase):
    def test_sharepoint_connector_local_download_and_upload_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "config.yaml"
            write_config(config)
            upload_path = Path("/recon-upload-space") / "AAPT"
            result_path = Path("/recon-result-space") / "AAPT"
            upload_path.mkdir(parents=True, exist_ok=True)
            result_path.mkdir(parents=True, exist_ok=True)
            source = upload_path / "download-me.csv"
            source.write_text("invoice", encoding="utf-8")
            download_output = root / "download.json"
            staged = root / "staged.csv"

            try:
                download = subprocess.run(
                    [
                        sys.executable,
                        str(SCRIPTS / "sharepoint_connector.py"),
                        "--config",
                        str(config),
                        "--mode",
                        "local",
                        "download-upload",
                        "--provider",
                        "AAPT",
                        "--source-name",
                        source.name,
                        "--destination",
                        str(staged),
                        "--output",
                        str(download_output),
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                upload_output = root / "upload.json"
                target = result_path / "uploaded" / "manifest.json"
                upload = subprocess.run(
                    [
                        sys.executable,
                        str(SCRIPTS / "sharepoint_connector.py"),
                        "--config",
                        str(config),
                        "--mode",
                        "local",
                        "upload-artifact",
                        "--local-file",
                        str(staged),
                        "--sharepoint-path",
                        str(target),
                        "--output",
                        str(upload_output),
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )
            finally:
                source.unlink(missing_ok=True)
                target.unlink(missing_ok=True)

            self.assertEqual(0, download.returncode, download.stderr + download.stdout)
            self.assertEqual(0, upload.returncode, upload.stderr + upload.stdout)
            self.assertEqual("downloaded", read_json(download_output)["status"])
            self.assertEqual("uploaded", read_json(upload_output)["status"])
            self.assertEqual("invoice", staged.read_text(encoding="utf-8").strip())

    def test_sharepoint_connector_local_find_reports_ambiguous_uploads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "config.yaml"
            write_config(config)
            upload_path = Path("/recon-upload-space") / "AAPT"
            upload_path.mkdir(parents=True, exist_ok=True)
            first = upload_path / "ambiguous-a.csv"
            second = upload_path / "ambiguous-b.csv"
            first.write_text("a", encoding="utf-8")
            second.write_text("b", encoding="utf-8")
            output = root / "find.json"

            try:
                result = subprocess.run(
                    [
                        sys.executable,
                        str(SCRIPTS / "sharepoint_connector.py"),
                        "--config",
                        str(config),
                        "--mode",
                        "local",
                        "find-upload",
                        "--provider",
                        "AAPT",
                        "--output",
                        str(output),
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )
            finally:
                first.unlink(missing_ok=True)
                second.unlink(missing_ok=True)

            self.assertEqual(2, result.returncode)
            self.assertGreaterEqual(read_json(output)["count"], 2)

    def test_write_reports_rejects_row_count_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw_matches = root / "raw.json"
            final_matches = root / "final.json"
            raw_matches.write_text(json.dumps({"rows": [{"line_id": "1"}, {"line_id": "2"}]}), encoding="utf-8")
            final_matches.write_text(json.dumps({"rows": [{"line_id": "1"}]}), encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "write_reports.py"),
                    "--raw-matches",
                    str(raw_matches),
                    "--matches",
                    str(final_matches),
                    "--raw-output",
                    str(root / "raw.csv"),
                    "--refined-output",
                    str(root / "refined.csv"),
                    "--manifest",
                    str(root / "manifest.json"),
                ],
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertNotEqual(0, result.returncode)
        self.assertIn("same row count", result.stderr + result.stdout)

    def test_write_reports_rejects_excluded_phase1_columns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            matches = root / "matches.json"
            matches.write_text(json.dumps({"rows": [{"line_id": "1", "agent_confidence_score": "0.9"}]}), encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "write_reports.py"),
                    "--matches",
                    str(matches),
                    "--raw-output",
                    str(root / "raw.csv"),
                    "--refined-output",
                    str(root / "refined.csv"),
                    "--manifest",
                    str(root / "manifest.json"),
                ],
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertNotEqual(0, result.returncode)
        self.assertIn("Excluded runtime columns", result.stderr + result.stdout)

    def test_report_column_helpers_drop_excluded_columns_and_default_review_fields(self) -> None:
        columns = ordered_columns([{"line_id": "1", "agent_notes": "drop", "custom": "keep"}], ["line_id"])
        refined = with_refined_defaults([{"line_id": "1", "agent_match_status": "auto_matched"}])[0]

        self.assertEqual(["line_id", "custom"], columns)
        self.assertEqual("false", refined["agent_review_required"])
        self.assertEqual("not_reviewed", refined["human_verified_status"])

    def test_billing_sql_helpers_validate_comments_and_modes(self) -> None:
        billing_query._assert_read_only_sql("-- harmless\nselect * from candidates")
        with self.assertRaisesRegex(RuntimeError, "only one read-only statement"):
            billing_query._assert_read_only_sql("select * from candidates; select * from other")
        with self.assertRaisesRegex(RuntimeError, "Enable approved"):
            billing_query._assert_billing_config({"features": {"billing_query_enabled": False}})
        with self.assertRaisesRegex(RuntimeError, "billing.mode"):
            billing_query._assert_billing_config({"features": {"billing_query_enabled": True}, "billing": {"mode": "other"}})
        with self.assertRaisesRegex(RuntimeError, "agent_sql_allowed"):
            billing_query._assert_billing_config(
                {"features": {"billing_query_enabled": True}, "billing": {"mode": "read_only_sql"}}
            )
        with self.assertRaisesRegex(RuntimeError, "billing_profile_missing"):
            billing_query._execute_query("select 1", {})

    def test_match_evidence_rejects_conflicts_and_one_to_many(self) -> None:
        base = {"service_id_match": True, "provider_match": True, "billing_period_match": True}

        self.assertTrue(has_exact_match_evidence(base))
        self.assertFalse(has_exact_match_evidence({**base, "conflicting_candidate": True}))
        self.assertFalse(has_exact_match_evidence({**base, "one_to_many": True}))

    def test_exception_investigation_helpers_validate_payload_shape(self) -> None:
        self.assertEqual([{"line_id": "1"}], apply_exception_investigation._rows([{"line_id": "1"}]))
        self.assertEqual("run|AAPT|file.csv|7|SVC-1", apply_exception_investigation._line_key(
            {
                "run_id": "run",
                "provider": "AAPT",
                "source_file": "file.csv",
                "source_row": "7",
                "service_id_raw": "SVC-1",
            }
        ))
        self.assertEqual({}, apply_exception_investigation._validated_update({"line_id": "1"}))
        with self.assertRaisesRegex(RuntimeError, "row/update list"):
            apply_exception_investigation._rows({"payload": []})
        with self.assertRaisesRegex(RuntimeError, "fallback source identity"):
            apply_exception_investigation._line_key({})
        with self.assertRaisesRegex(RuntimeError, "disallowed fields"):
            apply_exception_investigation._validated_update({"line_id": "1", "human_verified_status": "verified"})
        with self.assertRaisesRegex(RuntimeError, "Invalid agent_match_status"):
            apply_exception_investigation._validated_update(
                {"line_id": "1", "agent_match_status": "wrong", "agent_evidence_summary": "evidence"}
            )

    def test_exception_investigation_cli_rejects_duplicate_and_unknown_updates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            matches = root / "matches.json"
            investigation = root / "investigation.json"
            matches.write_text(json.dumps({"rows": [{"line_id": "line-1", "provider": "AAPT"}]}), encoding="utf-8")
            investigation.write_text(
                json.dumps(
                    {
                        "rows": [
                            {"line_id": "line-1", "agent_match_status": "no_match", "agent_evidence_summary": "evidence"},
                            {"line_id": "line-1", "agent_match_status": "no_match", "agent_evidence_summary": "evidence"},
                        ]
                    }
                ),
                encoding="utf-8",
            )

            duplicate = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "apply_exception_investigation.py"),
                    "--matches",
                    str(matches),
                    "--investigation",
                    str(investigation),
                    "--output",
                    str(root / "out.json"),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            investigation.write_text(
                json.dumps({"rows": [{"line_id": "missing", "agent_match_status": "no_match", "agent_evidence_summary": "evidence"}]}),
                encoding="utf-8",
            )
            unknown = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "apply_exception_investigation.py"),
                    "--matches",
                    str(matches),
                    "--investigation",
                    str(investigation),
                    "--output",
                    str(root / "out.json"),
                ],
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertNotEqual(0, duplicate.returncode)
        self.assertIn("Duplicate exception investigation update", duplicate.stderr + duplicate.stdout)
        self.assertNotEqual(0, unknown.returncode)
        self.assertIn("referenced unknown rows", unknown.stderr + unknown.stdout)

    def test_validate_run_helpers_reject_secret_and_bad_evidence_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_root = Path(tmp)
            validate_run.assert_no_secret_markers(run_root)
            (run_root / "logs").mkdir()
            (run_root / "logs" / "nested").mkdir()
            (run_root / "logs" / "nested" / "note.txt").write_text("password=secret", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "Secret-like marker"):
                validate_run.assert_no_secret_markers(run_root)

            warnings_path = run_root / "logs" / "parser_warnings.json"
            warnings_path.write_text(json.dumps({"not": "a list"}), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "must be a list"):
                validate_run.assert_parser_warnings_resolved(run_root, [])

        with self.assertRaisesRegex(RuntimeError, "agent_evidence_summary is required"):
            validate_run.assert_evidence_summary({"agent_match_status": "no_match"}, auto_matched_mode="short", max_chars=160)
        with self.assertRaisesRegex(RuntimeError, "single line"):
            validate_run.assert_evidence_summary(
                {"agent_match_status": "no_match", "agent_evidence_summary": "line one\nline two"},
                auto_matched_mode="short",
                max_chars=160,
            )

    def test_common_runtime_helpers_validate_policy_and_paths(self) -> None:
        self.assertEqual({"auto_matched": "blank", "max_chars": 80}, evidence_summary_policy({"reports": {"evidence_summary": {"auto_matched": "blank", "max_chars": 80}}}))
        with self.assertRaisesRegex(ValueError, "auto_matched"):
            evidence_summary_policy({"reports": {"evidence_summary": {"auto_matched": "verbose"}}})
        with self.assertRaisesRegex(ValueError, "max_chars"):
            evidence_summary_policy({"reports": {"evidence_summary": {"max_chars": 999}}})
        self.assertFalse(provider_api_adapter_enabled({"provider_api_adapters": []}, "AAPT"))
        self.assertEqual("AAPT", ensure_provider({"provider_api_adapters": {"aapt": True}}, "AAPT")["provider"])
        paths = RunPaths.from_root(Path("run"))
        self.assertEqual(Path("run") / "source", paths.source)

    def test_sharepoint_legacy_graph_helpers_fail_closed_without_env(self) -> None:
        old_token = os.environ.pop("NEXON_RECON_GRAPH_ACCESS_TOKEN", None)
        old_drive = os.environ.pop("NEXON_RECON_SHAREPOINT_DRIVE_ID", None)
        try:
            with self.assertRaisesRegex(RuntimeError, "sharepoint_auth_missing"):
                sharepoint_connector._token()
            with self.assertRaisesRegex(RuntimeError, "sharepoint_drive_missing"):
                sharepoint_connector._drive_id()
            os.environ["NEXON_RECON_SHAREPOINT_DRIVE_ID"] = "drive-1"
            self.assertIn("/drives/drive-1/root:", sharepoint_connector._drive_path("/recon-upload-space/AAPT"))
        finally:
            if old_token is not None:
                os.environ["NEXON_RECON_GRAPH_ACCESS_TOKEN"] = old_token
            if old_drive is not None:
                os.environ["NEXON_RECON_SHAREPOINT_DRIVE_ID"] = old_drive

    def test_sharepoint_graph_request_and_json_helpers_without_live_network(self) -> None:
        class FakeResponse:
            def __enter__(self) -> "FakeResponse":
                return self

            def __exit__(self, *_args: object) -> None:
                return None

            def read(self) -> bytes:
                return b'{"ok": true}'

        old_token = os.environ.get("NEXON_RECON_GRAPH_ACCESS_TOKEN")
        old_urlopen = sharepoint_connector.urlopen
        captured: list[object] = []
        try:
            os.environ["NEXON_RECON_GRAPH_ACCESS_TOKEN"] = "token"

            def fake_urlopen(request: object, timeout: int) -> FakeResponse:
                captured.append((request, timeout))
                return FakeResponse()

            sharepoint_connector.urlopen = fake_urlopen
            self.assertEqual(b'{"ok": true}', sharepoint_connector._graph_request("POST", "/test", {"a": 1}))
            self.assertEqual({"ok": True}, sharepoint_connector._graph_json("GET", "/test"))
            self.assertEqual(2, len(captured))
        finally:
            sharepoint_connector.urlopen = old_urlopen
            if old_token is None:
                os.environ.pop("NEXON_RECON_GRAPH_ACCESS_TOKEN", None)
            else:
                os.environ["NEXON_RECON_GRAPH_ACCESS_TOKEN"] = old_token

    def test_sharepoint_graph_mode_commands_without_live_network(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "output.json"
            staged = root / "staged.bin"
            local_file = root / "artifact.json"
            local_file.write_text("artifact", encoding="utf-8")
            calls: list[tuple[str, object]] = []
            old_get_item = sharepoint_connector._get_item
            old_children = sharepoint_connector._children
            old_graph_request = sharepoint_connector._graph_request
            old_graph_json = sharepoint_connector._graph_json
            old_ensure_folder = sharepoint_connector._ensure_folder
            old_drive_id = sharepoint_connector._drive_id
            try:
                sharepoint_connector._drive_id = lambda: "drive-1"
                sharepoint_connector._get_item = lambda path: {"id": f"id:{path}", "name": Path(path).name}
                sharepoint_connector._children = lambda path: [
                    {"name": "invoice.csv", "id": "item-1", "size": 123, "file": {}},
                    {"name": "folder", "id": "folder-1", "folder": {}},
                ]

                def fake_graph_request(method: str, path: str, body: dict | bytes | None = None, content_type: str = "application/json") -> bytes:
                    calls.append(("request", method, path, body, content_type))
                    return b"downloaded"

                def fake_graph_json(method: str, path: str, body: dict | None = None) -> dict:
                    calls.append(("json", method, path, body))
                    return {"id": "result-1", "method": method, "path": path, "body": body}

                sharepoint_connector._graph_request = fake_graph_request
                sharepoint_connector._graph_json = fake_graph_json
                sharepoint_connector._ensure_folder = lambda path: {"id": f"folder:{path}"}
                config: dict = {}

                self.assertEqual(
                    0,
                    sharepoint_connector.check_spaces(
                        SimpleNamespace(provider="AAPT", mode="graph", output=output),
                        config,
                    ),
                )
                self.assertEqual("ok", read_json(output)["status"])

                self.assertEqual(
                    0,
                    sharepoint_connector.find_upload(
                        SimpleNamespace(provider="AAPT", source_name="invoice.csv", mode="graph", output=output),
                        config,
                    ),
                )
                self.assertEqual(1, read_json(output)["count"])

                self.assertEqual(
                    0,
                    sharepoint_connector.download_upload(
                        SimpleNamespace(provider="AAPT", source_name="invoice.csv", destination=staged, mode="graph", output=output),
                        config,
                    ),
                )
                self.assertEqual(b"downloaded", staged.read_bytes())

                self.assertEqual(
                    0,
                    sharepoint_connector.move_upload_to_run_source(
                        SimpleNamespace(provider="AAPT", source_name="invoice.csv", run_root="/recon-result-space/AAPT/2026/07/run", copy=False, mode="graph", output=output),
                        config,
                    ),
                )
                self.assertEqual("moved", read_json(output)["status"])

                self.assertEqual(
                    0,
                    sharepoint_connector.upload_artifact(
                        SimpleNamespace(local_file=local_file, sharepoint_path="/recon-result-space/AAPT/manifest.json", mode="graph", output=output),
                        config,
                    ),
                )
                self.assertEqual("uploaded", read_json(output)["status"])
                self.assertTrue(calls)
            finally:
                sharepoint_connector._get_item = old_get_item
                sharepoint_connector._children = old_children
                sharepoint_connector._graph_request = old_graph_request
                sharepoint_connector._graph_json = old_graph_json
                sharepoint_connector._ensure_folder = old_ensure_folder
                sharepoint_connector._drive_id = old_drive_id


if __name__ == "__main__":
    unittest.main()
