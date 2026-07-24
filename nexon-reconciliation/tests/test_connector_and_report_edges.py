from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from recon_core import apply_exception_investigation, billing_query, validate_run  # noqa: E402
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


class ConnectorAndReportEdgeTests(unittest.TestCase):
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
        self.assertFalse(refined["agent_review_required"])
        self.assertEqual("not_reviewed", refined["human_verified_status"])

    def test_billing_sql_helpers_validate_comments_and_modes(self) -> None:
        billing_query._assert_read_only_sql(
            "-- harmless\nselect ServiceNumber as service_id, "
            "SupplierName as provider, AccountNumber as provider_account, "
            "BillingDate as transaction_date from Finance.GenericNexonBilling"
        )
        with self.assertRaisesRegex(RuntimeError, "only one read-only statement"):
            billing_query._assert_read_only_sql(
                "select * from Finance.GenericNexonBilling; select * from Finance.GenericNexonBilling"
            )
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
        self.assertEqual("line-1", apply_exception_investigation._line_key({"line_id": "line-1"}))
        self.assertEqual({}, apply_exception_investigation._validated_update({"line_id": "1"}))
        with self.assertRaisesRegex(RuntimeError, "row/update list"):
            apply_exception_investigation._rows({"payload": []})
        with self.assertRaisesRegex(RuntimeError, "required line_id"):
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

if __name__ == "__main__":
    unittest.main()
