from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def write_config(
    path: Path,
    upload_root: Path,
    result_root: Path,
    *,
    billing: bool = False,
    provider_api: bool = False,
    failure_notifications: bool = False,
) -> None:
    billing_enabled = "true" if billing else "false"
    provider_api_enabled = "true" if provider_api else "false"
    failure_notifications_enabled = "true" if failure_notifications else "false"
    path.write_text(
        f"""
timezone: Australia/Sydney
features:
  provider_api_enabled: {provider_api_enabled}
  billing_query_enabled: {billing_enabled}
  db_update_enabled: false
  failure_notifications_enabled: {failure_notifications_enabled}
provider_api_adapters:
  equinix: true
billing:
  mode: read_only_sql
  agent_sql_allowed: true
  audit_required: true
reports:
  evidence_summary:
    auto_matched: short
    max_chars: 160
failure_handling:
  notify_operator: true
  notification_mode: outlook
  notification_content: text_only
""".lstrip(),
        encoding="utf-8",
    )


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class ConnectorBillingAndFailureTests(unittest.TestCase):
    def test_billing_query_executes_agent_read_only_sqlite_query(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            upload_root = root / "upload"
            result_root = root / "result"
            config = root / "config.yaml"
            write_config(config, upload_root, result_root, billing=True)
            db_path = root / "billing.sqlite"
            connection = sqlite3.connect(db_path)
            try:
                connection.execute(
                    "create table billing_candidates (service_id text, service_provider text, provider_account text, transaction_date text, subscription_id text)"
                )
                connection.execute(
                    "insert into billing_candidates values ('SVC-1', 'AAPT', 'ACC-1', '2026-07-15', 'SUB-1')"
                )
                connection.commit()
            finally:
                connection.close()
            normalized = root / "normalized.json"
            normalized.write_text(
                json.dumps(
                    {
                        "lines": [
                            {
                                "line_id": "line-1",
                                "run_id": "AAPT_20260709_153012_A1B2C",
                                "provider": "AAPT",
                                "provider_account": "ACC-1",
                                "service_id_normalized": "SVC-1",
                                "billing_period_start": "2026-07-01",
                                "billing_period_end": "2026-07-31",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            output = root / "candidates.json"
            query_log = root / "query-log.json"
            sql_file = root / "billing-query.sql"
            sql_file.write_text(
                "select service_id, service_provider as provider, "
                "provider_account, transaction_date, subscription_id "
                "from billing_candidates",
                encoding="utf-8",
            )
            env = os.environ.copy()
            env["NEXON_RECON_BILLING_MODE"] = "sqlite"
            env["NEXON_RECON_BILLING_DSN"] = str(db_path)

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "billing_query.py"),
                    "--config",
                    str(config),
                    "--normalized",
                    str(normalized),
                    "--sql-file",
                    str(sql_file),
                    "--output",
                    str(output),
                    "--query-log",
                    str(query_log),
                ],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(0, result.returncode, result.stderr)
            payload = read_json(output)
            candidate = payload["candidates_by_line"]["line-1"][0]
            log_entry = json.loads(query_log.read_text(encoding="utf-8"))[0]
            self.assertTrue(candidate["service_id_match"])
            self.assertTrue(candidate["provider_match"])
            self.assertTrue(candidate["billing_period_match"])
            self.assertEqual("SUB-1", candidate["subscription_id"])
            self.assertEqual("sqlite", log_entry["billing_mode"])
            self.assertEqual(str(sql_file), log_entry["sql_source"])
            self.assertEqual(payload["sql_hash"], log_entry["sql_hash"])
            self.assertEqual("passed", log_entry["read_only_validation"])
            self.assertIn("duration_ms", log_entry)
            self.assertIn("parameter_hashes", log_entry)
            self.assertIn("service_ids_json", log_entry["parameter_hashes"])

    def test_billing_query_rejects_non_read_only_sql(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "config.yaml"
            write_config(config, root / "upload", root / "result", billing=True)
            db_path = root / "billing.sqlite"
            sqlite3.connect(db_path).close()
            normalized = root / "normalized.json"
            normalized.write_text(json.dumps({"lines": [{"line_id": "line-1", "run_id": "AAPT_20260709_153012_A1B2C", "provider": "AAPT"}]}), encoding="utf-8")
            sql_file = root / "unsafe.sql"
            sql_file.write_text("update candidates set service_id = :service_id", encoding="utf-8")
            env = os.environ.copy()
            env["NEXON_RECON_BILLING_MODE"] = "sqlite"
            env["NEXON_RECON_BILLING_DSN"] = str(db_path)

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "billing_query.py"),
                    "--config",
                    str(config),
                    "--normalized",
                    str(normalized),
                    "--sql-file",
                    str(sql_file),
                    "--output",
                    str(root / "output.json"),
                    "--query-log",
                    str(root / "query-log.json"),
                ],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertNotEqual(0, result.returncode)
            self.assertIn("billing_query_not_read_only", result.stderr + result.stdout)

    def test_billing_query_rejects_select_into_sql(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "config.yaml"
            write_config(config, root / "upload", root / "result", billing=True)
            db_path = root / "billing.sqlite"
            sqlite3.connect(db_path).close()
            normalized = root / "normalized.json"
            normalized.write_text(json.dumps({"lines": [{"line_id": "line-1", "run_id": "AAPT_20260709_153012_A1B2C", "provider": "AAPT"}]}), encoding="utf-8")
            sql_file = root / "unsafe.sql"
            sql_file.write_text("select service_id into temp unsafe_candidate_copy from candidates", encoding="utf-8")
            env = os.environ.copy()
            env["NEXON_RECON_BILLING_MODE"] = "sqlite"
            env["NEXON_RECON_BILLING_DSN"] = str(db_path)

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "billing_query.py"),
                    "--config",
                    str(config),
                    "--normalized",
                    str(normalized),
                    "--sql-file",
                    str(sql_file),
                    "--output",
                    str(root / "output.json"),
                    "--query-log",
                    str(root / "query-log.json"),
                ],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertNotEqual(0, result.returncode)
            self.assertIn("billing_query_not_read_only", result.stderr + result.stdout)

    def test_record_failure_writes_controlled_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "config.yaml"
            write_config(config, root / "upload", root / "result", failure_notifications=True)
            run_root = root / "result" / "AAPT" / "2026" / "07" / "AAPT_20260709_153012_A1B2C"
            output = run_root / "manifest" / "failure_manifest.json"

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "record_failure.py"),
                    "--config",
                    str(config),
                    "--provider",
                    "AAPT",
                    "--stage",
                    "billing_query",
                    "--reason",
                    "billing_query_not_available",
                    "--run-root",
                    str(run_root),
                ],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(0, result.returncode, result.stderr)
            payload = read_json(output)
            self.assertEqual("failed", payload["status"])
            self.assertEqual("billing_query", payload["failed_stage"])
            self.assertFalse(payload["accepted_resolution_update_attempted"])
            self.assertTrue(payload["notification_required"])

    def test_notify_failure_noops_when_notifications_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "config.yaml"
            write_config(config, root / "upload", root / "result")
            manifest = root / "failure.json"
            manifest.write_text(
                json.dumps({"status": "failed", "provider": "AAPT", "stage": "parser", "reason": "parser_failed"}),
                encoding="utf-8",
            )
            output = root / "notify.json"

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "notify_failure.py"),
                    "--config",
                    str(config),
                    "--failure-manifest",
                    str(manifest),
                    "--output",
                    str(output),
                ],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual({"notification_sent": False, "status": "disabled"}, read_json(output))

    def test_notify_failure_prepares_outlook_text_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "config.yaml"
            write_config(config, root / "upload", root / "result", failure_notifications=True)
            manifest = root / "failure.json"
            manifest.write_text(
                json.dumps(
                    {
                        "status": "failed",
                        "provider": "AAPT",
                        "stage": "billing_query",
                        "reason": "billing_query_not_available",
                        "run_id": "AAPT_20260709_153012_A1B2C",
                        "recorded_at": "2026-07-09T15:30:12+10:00",
                    }
                ),
                encoding="utf-8",
            )
            output = root / "notify.json"
            env = os.environ.copy()
            env["NEXON_RECON_FAILURE_NOTIFICATION_EMAIL_TO"] = "ops@example.com,recon@example.com"

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "notify_failure.py"),
                    "--config",
                    str(config),
                    "--failure-manifest",
                    str(manifest),
                    "--output",
                    str(output),
                ],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(0, result.returncode, result.stderr)
            payload = read_json(output)
            self.assertEqual("ready_for_outlook_tool", payload["status"])
            self.assertEqual("native_outlook_send_email", payload["delivery_tool"])
            self.assertFalse(payload["notification_sent"])
            self.assertFalse(payload["attachments_allowed"])
            self.assertEqual(["ops@example.com", "recon@example.com"], payload["to"])
            self.assertIn("Nexon reconciliation failure", payload["subject"])
            self.assertIn("No files are attached", payload["body_text"])

    def test_notify_failure_requires_recipients_for_outlook_text_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "config.yaml"
            write_config(config, root / "upload", root / "result", failure_notifications=True)
            manifest = root / "failure.json"
            manifest.write_text(
                json.dumps({"status": "failed", "provider": "AAPT", "stage": "parser", "reason": "parser_failed"}),
                encoding="utf-8",
            )
            env = os.environ.copy()
            env.pop("NEXON_RECON_FAILURE_NOTIFICATION_EMAIL_TO", None)

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "notify_failure.py"),
                    "--config",
                    str(config),
                    "--failure-manifest",
                    str(manifest),
                ],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertNotEqual(0, result.returncode)
            self.assertIn("failure_notification_recipients_missing", result.stderr + result.stdout)

    def test_intake_run_blocks_provider_api_when_feature_flag_is_off(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            upload_root = root / "upload"
            result_root = root / "result"
            config = root / "config.yaml"
            write_config(config, upload_root, result_root, provider_api=False)
            source = root / "equinix.pdf"
            source.write_text("invoice", encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "intake_run.py"),
                    "--config",
                    str(config),
                    "--provider",
                    "Equinix",
                    "--source-file",
                    str(source),
                    "--intake-mode",
                    "provider_api",
                    "--result-root",
                    str(result_root),
                    "--source-identity",
                    "EQ-INV-1",
                ],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertNotEqual(0, result.returncode)
            self.assertIn("provider_api_not_available", result.stderr + result.stdout)

    def test_apply_exception_investigation_merges_agent_fields_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            matches = root / "matches.json"
            investigation = root / "investigation.json"
            output = root / "merged.json"
            manifest = root / "merge-manifest.json"
            matches.write_text(
                json.dumps(
                    {
                        "rows": [
                            {
                                "line_id": "line-1",
                                "provider": "AAPT",
                                "service_id_raw": "SVC-1",
                                "agent_match_status": "no_match",
                                "customer_raw_field": "must stay",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            investigation.write_text(
                json.dumps(
                    {
                        "rows": [
                            {
                                "line_id": "line-1",
                                "agent_match_status": "suggested_match",
                                "agent_match_rule": "service_id_provider_period",
                                "agent_suggested_customer_account": "CUST-1",
                                "agent_evidence_summary": "SVC-1 matched one billing candidate.",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "apply_exception_investigation.py"),
                    "--matches",
                    str(matches),
                    "--investigation",
                    str(investigation),
                    "--output",
                    str(output),
                    "--manifest",
                    str(manifest),
                ],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(0, result.returncode, result.stderr)
            merged_row = read_json(output)["rows"][0]
            self.assertEqual("must stay", merged_row["customer_raw_field"])
            self.assertEqual("suggested_match", merged_row["agent_match_status"])
            self.assertEqual("true", merged_row["agent_review_required"])
            self.assertEqual(1, read_json(manifest)["applied_updates"])

    def test_apply_exception_investigation_requires_evidence_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            matches = root / "matches.json"
            investigation = root / "investigation.json"
            matches.write_text(
                json.dumps({"rows": [{"line_id": "line-1", "provider": "AAPT", "agent_match_status": "no_match"}]}),
                encoding="utf-8",
            )
            investigation.write_text(
                json.dumps({"rows": [{"line_id": "line-1", "agent_match_status": "suggested_match"}]}),
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "apply_exception_investigation.py"),
                    "--matches",
                    str(matches),
                    "--investigation",
                    str(investigation),
                    "--output",
                    str(root / "merged.json"),
                ],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertNotEqual(0, result.returncode)
            self.assertIn("agent_evidence_summary", result.stderr + result.stdout)


if __name__ == "__main__":
    unittest.main()
