from __future__ import annotations

import csv
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from recon_core.common import APPROVED_REFINED_COLUMNS, RUN_SUBDIRS, generate_run_id, write_json  # noqa: E402
from recon_core.write_reports import BASE_COLUMNS  # noqa: E402


def read_header(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return next(csv.reader(handle))


def write_empty_parser_warnings(run_root: Path) -> None:
    write_json(run_root / "logs" / "parser_warnings.json", [])


class ReportWriterAndRunValidationTests(unittest.TestCase):
    def test_write_reports_outputs_expected_headers_and_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            matches = root / "matches.json"
            raw = root / "raw.csv"
            refined = root / "refined.csv"
            manifest = root / "manifest.json"
            write_json(
                matches,
                {
                    "rows": [
                        {
                            "run_id": "AAPT_20260709_153012_A1B2C",
                            "provider": "AAPT",
                            "agent_match_status": "no_match",
                            "agent_evidence_summary": "No billing candidate was found.",
                            "human_verified_status": "not_reviewed",
                            "logic_app_raw_column": "preserve me",
                        }
                    ]
                },
            )

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "write_reports.py"),
                    "--matches",
                    str(matches),
                    "--raw-output",
                    str(raw),
                    "--refined-output",
                    str(refined),
                    "--manifest",
                    str(manifest),
                ],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn("logic_app_raw_column", read_header(raw))
            self.assertIn("logic_app_raw_column", read_header(refined))
            for column in APPROVED_REFINED_COLUMNS:
                self.assertIn(column, read_header(refined))

    def test_write_reports_uses_pre_investigation_rows_for_raw_and_final_rows_for_refined(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw_matches = root / "raw-matches.json"
            final_matches = root / "final-matches.json"
            raw = root / "raw.csv"
            refined = root / "refined.csv"
            manifest = root / "manifest.json"
            write_json(
                raw_matches,
                {
                    "rows": [
                        {
                            "line_id": "line-1",
                            "provider": "AAPT",
                            "agent_match_status": "no_match",
                            "agent_evidence_summary": "No billing candidate was found.",
                        }
                    ]
                },
            )
            write_json(
                final_matches,
                {
                    "rows": [
                        {
                            "line_id": "line-1",
                            "provider": "AAPT",
                            "agent_match_status": "suggested_match",
                            "agent_evidence_summary": "Investigator found one candidate.",
                            "agent_suggested_customer_account": "CUST-1",
                        }
                    ]
                },
            )

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "write_reports.py"),
                    "--raw-matches",
                    str(raw_matches),
                    "--matches",
                    str(final_matches),
                    "--raw-output",
                    str(raw),
                    "--refined-output",
                    str(refined),
                    "--manifest",
                    str(manifest),
                ],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertNotIn("agent_suggested_customer_account", read_header(raw))
            self.assertIn("agent_suggested_customer_account", read_header(refined))

    def test_validate_run_accepts_minimal_report_only_package(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pack = Path(__file__).resolve().parents[1]
            config = pack / "config" / "recon_settings.yaml"
            run_id = generate_run_id("AAPT", "source", __import__("datetime").datetime.fromisoformat("2026-07-09T15:30:12+10:00"))
            run_root = Path(tmp) / "AAPT" / "2026" / "07" / run_id
            for subdir in RUN_SUBDIRS:
                (run_root / subdir).mkdir(parents=True, exist_ok=True)

            write_json(run_root / "manifest" / "run_manifest.json", {"run_id": run_id, "db_update_enabled": False})
            write_json(run_root / "manifest" / "report_manifest.json", {"row_count": 1})
            write_empty_parser_warnings(run_root)
            (run_root / "raw-recon-report" / "raw.csv").write_text(
                ",".join(BASE_COLUMNS) + "\n" + ",".join([""] * len(BASE_COLUMNS)) + "\n",
                encoding="utf-8",
            )
            refined_header = BASE_COLUMNS + APPROVED_REFINED_COLUMNS
            row = {column: "" for column in refined_header}
            row["agent_match_status"] = "no_match"
            row["agent_evidence_summary"] = "No billing candidate was found."
            row["human_verified_status"] = "not_reviewed"
            with (run_root / "refined-recon-report" / "refined.csv").open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=refined_header)
                writer.writeheader()
                writer.writerow(row)

            result = subprocess.run(
                [sys.executable, str(SCRIPTS / "validate_run.py"), "--config", str(config), "--run-root", str(run_root)],
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(0, result.returncode, result.stderr + result.stdout)

    def test_validate_run_allows_blank_auto_match_evidence_when_configured(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "config.yaml"
            config.write_text(
                """
features:
  db_update_enabled: false
reports:
  evidence_summary:
    auto_matched: blank
    max_chars: 80
""".lstrip(),
                encoding="utf-8",
            )
            run_id = "AAPT_20260709_153012_A1B2C"
            run_root = root / "AAPT" / "2026" / "07" / run_id
            for subdir in RUN_SUBDIRS:
                (run_root / subdir).mkdir(parents=True, exist_ok=True)

            write_json(run_root / "manifest" / "run_manifest.json", {"run_id": run_id, "db_update_enabled": False})
            write_json(run_root / "manifest" / "report_manifest.json", {"row_count": 1})
            write_empty_parser_warnings(run_root)
            (run_root / "raw-recon-report" / "raw.csv").write_text(
                ",".join(BASE_COLUMNS) + "\n" + ",".join([""] * len(BASE_COLUMNS)) + "\n",
                encoding="utf-8",
            )
            refined_header = BASE_COLUMNS + APPROVED_REFINED_COLUMNS
            row = {column: "" for column in refined_header}
            row["agent_match_status"] = "auto_matched"
            row["agent_match_rule"] = "deterministic_exact_candidate_v1"
            row["agent_evidence_summary"] = ""
            row["agent_review_required"] = "false"
            row["human_verified_status"] = "not_reviewed"
            with (run_root / "refined-recon-report" / "refined.csv").open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=refined_header)
                writer.writeheader()
                writer.writerow(row)

            result = subprocess.run(
                [sys.executable, str(SCRIPTS / "validate_run.py"), "--config", str(config), "--run-root", str(run_root)],
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(0, result.returncode, result.stderr + result.stdout)

    def test_validate_run_rejects_long_evidence_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "config.yaml"
            config.write_text(
                """
features:
  db_update_enabled: false
reports:
  evidence_summary:
    auto_matched: short
    max_chars: 40
""".lstrip(),
                encoding="utf-8",
            )
            run_id = "AAPT_20260709_153012_A1B2C"
            run_root = root / "AAPT" / "2026" / "07" / run_id
            for subdir in RUN_SUBDIRS:
                (run_root / subdir).mkdir(parents=True, exist_ok=True)

            write_json(run_root / "manifest" / "run_manifest.json", {"run_id": run_id, "db_update_enabled": False})
            write_json(run_root / "manifest" / "report_manifest.json", {"row_count": 1})
            write_empty_parser_warnings(run_root)
            (run_root / "raw-recon-report" / "raw.csv").write_text(
                ",".join(BASE_COLUMNS) + "\n" + ",".join([""] * len(BASE_COLUMNS)) + "\n",
                encoding="utf-8",
            )
            refined_header = BASE_COLUMNS + APPROVED_REFINED_COLUMNS
            row = {column: "" for column in refined_header}
            row["agent_match_status"] = "auto_matched"
            row["agent_evidence_summary"] = "This evidence summary is intentionally too long for the configured compact report limit."
            row["human_verified_status"] = "not_reviewed"
            with (run_root / "refined-recon-report" / "refined.csv").open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=refined_header)
                writer.writeheader()
                writer.writerow(row)

            result = subprocess.run(
                [sys.executable, str(SCRIPTS / "validate_run.py"), "--config", str(config), "--run-root", str(run_root)],
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertNotEqual(0, result.returncode)
        self.assertIn("agent_evidence_summary exceeds max_chars", result.stderr + result.stdout)

    def test_validate_run_fails_without_no_write_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pack = Path(__file__).resolve().parents[1]
            config = pack / "config" / "recon_settings.yaml"
            run_id = "AAPT_20260709_153012_A1B2C"
            run_root = Path(tmp) / "AAPT" / "2026" / "07" / run_id
            for subdir in RUN_SUBDIRS:
                (run_root / subdir).mkdir(parents=True, exist_ok=True)
            write_json(run_root / "manifest" / "run_manifest.json", {"run_id": run_id})
            write_json(run_root / "manifest" / "report_manifest.json", {"row_count": 0})
            write_empty_parser_warnings(run_root)
            (run_root / "raw-recon-report" / "raw.csv").write_text(",".join(BASE_COLUMNS) + "\n", encoding="utf-8")
            (run_root / "refined-recon-report" / "refined.csv").write_text(
                ",".join(BASE_COLUMNS + APPROVED_REFINED_COLUMNS) + "\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                [sys.executable, str(SCRIPTS / "validate_run.py"), "--config", str(config), "--run-root", str(run_root)],
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertNotEqual(0, result.returncode)
        self.assertIn("db_update_enabled=false", result.stderr + result.stdout)

    def test_validate_run_fails_on_manifest_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pack = Path(__file__).resolve().parents[1]
            config = pack / "config" / "recon_settings.yaml"
            run_id = "AAPT_20260709_153012_A1B2C"
            run_root = Path(tmp) / "AAPT" / "2026" / "07" / run_id
            for subdir in RUN_SUBDIRS:
                (run_root / subdir).mkdir(parents=True, exist_ok=True)
            write_json(run_root / "manifest" / "run_manifest.json", {"run_id": "AAPT_20260709_153012_FFFFF", "db_update_enabled": False})
            write_json(run_root / "manifest" / "report_manifest.json", {"row_count": 999})
            write_empty_parser_warnings(run_root)
            (run_root / "raw-recon-report" / "raw.csv").write_text(",".join(BASE_COLUMNS) + "\n", encoding="utf-8")
            (run_root / "refined-recon-report" / "refined.csv").write_text(
                ",".join(BASE_COLUMNS + APPROVED_REFINED_COLUMNS) + "\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                [sys.executable, str(SCRIPTS / "validate_run.py"), "--config", str(config), "--run-root", str(run_root)],
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertNotEqual(0, result.returncode)
        self.assertIn("run_id does not match", result.stderr + result.stdout)

    def test_validate_run_fails_on_report_manifest_row_count_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pack = Path(__file__).resolve().parents[1]
            config = pack / "config" / "recon_settings.yaml"
            run_id = "AAPT_20260709_153012_A1B2C"
            run_root = Path(tmp) / "AAPT" / "2026" / "07" / run_id
            for subdir in RUN_SUBDIRS:
                (run_root / subdir).mkdir(parents=True, exist_ok=True)
            write_json(run_root / "manifest" / "run_manifest.json", {"run_id": run_id, "db_update_enabled": False})
            write_json(run_root / "manifest" / "report_manifest.json", {"row_count": 999})
            write_empty_parser_warnings(run_root)
            (run_root / "raw-recon-report" / "raw.csv").write_text(",".join(BASE_COLUMNS) + "\n", encoding="utf-8")
            (run_root / "refined-recon-report" / "refined.csv").write_text(
                ",".join(BASE_COLUMNS + APPROVED_REFINED_COLUMNS) + "\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                [sys.executable, str(SCRIPTS / "validate_run.py"), "--config", str(config), "--run-root", str(run_root)],
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertNotEqual(0, result.returncode)
        self.assertIn("row_count does not match", result.stderr + result.stdout)

    def test_validate_run_fails_on_wrong_path_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pack = Path(__file__).resolve().parents[1]
            config = pack / "config" / "recon_settings.yaml"
            run_id = "AAPT_20260709_153012_A1B2C"
            run_root = Path(tmp) / "wrong" / "2026" / "07" / run_id
            for subdir in RUN_SUBDIRS:
                (run_root / subdir).mkdir(parents=True, exist_ok=True)
            write_json(run_root / "manifest" / "run_manifest.json", {"run_id": run_id, "db_update_enabled": False})
            write_json(run_root / "manifest" / "report_manifest.json", {"row_count": 0})
            write_empty_parser_warnings(run_root)
            (run_root / "raw-recon-report" / "raw.csv").write_text(",".join(BASE_COLUMNS) + "\n", encoding="utf-8")
            (run_root / "refined-recon-report" / "refined.csv").write_text(
                ",".join(BASE_COLUMNS + APPROVED_REFINED_COLUMNS) + "\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                [sys.executable, str(SCRIPTS / "validate_run.py"), "--config", str(config), "--run-root", str(run_root)],
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertNotEqual(0, result.returncode)
        self.assertIn("<provider>/<year>/<month>/<run_id>", result.stderr + result.stdout)

    def test_validate_run_fails_when_parser_warnings_artifact_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pack = Path(__file__).resolve().parents[1]
            config = pack / "config" / "recon_settings.yaml"
            run_id = "AAPT_20260709_153012_A1B2C"
            run_root = Path(tmp) / "AAPT" / "2026" / "07" / run_id
            for subdir in RUN_SUBDIRS:
                (run_root / subdir).mkdir(parents=True, exist_ok=True)
            write_json(run_root / "manifest" / "run_manifest.json", {"run_id": run_id, "db_update_enabled": False})
            write_json(run_root / "manifest" / "report_manifest.json", {"row_count": 0})
            (run_root / "raw-recon-report" / "raw.csv").write_text(",".join(BASE_COLUMNS) + "\n", encoding="utf-8")
            (run_root / "refined-recon-report" / "refined.csv").write_text(
                ",".join(BASE_COLUMNS + APPROVED_REFINED_COLUMNS) + "\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                [sys.executable, str(SCRIPTS / "validate_run.py"), "--config", str(config), "--run-root", str(run_root)],
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertNotEqual(0, result.returncode)
        self.assertIn("Missing parser warnings artifact", result.stderr + result.stdout)

    def test_validate_run_fails_when_parser_error_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pack = Path(__file__).resolve().parents[1]
            config = pack / "config" / "recon_settings.yaml"
            run_id = "AAPT_20260709_153012_A1B2C"
            run_root = Path(tmp) / "AAPT" / "2026" / "07" / run_id
            for subdir in RUN_SUBDIRS:
                (run_root / subdir).mkdir(parents=True, exist_ok=True)
            write_json(run_root / "manifest" / "run_manifest.json", {"run_id": run_id, "db_update_enabled": False})
            write_json(run_root / "manifest" / "report_manifest.json", {"row_count": 1})
            write_json(run_root / "logs" / "parser_warnings.json", [{"severity": "error", "message": "parser failed"}])
            (run_root / "raw-recon-report" / "raw.csv").write_text(
                ",".join(BASE_COLUMNS) + "\n" + ",".join([""] * len(BASE_COLUMNS)) + "\n",
                encoding="utf-8",
            )
            refined_header = BASE_COLUMNS + APPROVED_REFINED_COLUMNS
            row = {column: "" for column in refined_header}
            row["agent_match_status"] = "no_match"
            row["agent_evidence_summary"] = "Parser returned an error warning."
            row["human_verified_status"] = "not_reviewed"
            with (run_root / "refined-recon-report" / "refined.csv").open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=refined_header)
                writer.writeheader()
                writer.writerow(row)

            result = subprocess.run(
                [sys.executable, str(SCRIPTS / "validate_run.py"), "--config", str(config), "--run-root", str(run_root)],
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertNotEqual(0, result.returncode)
        self.assertIn("Parser error warnings block run validation", result.stderr + result.stdout)

    def test_validate_run_requires_invoice_number_for_human_verified_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pack = Path(__file__).resolve().parents[1]
            config = pack / "config" / "recon_settings.yaml"
            run_id = "AAPT_20260709_153012_A1B2C"
            run_root = Path(tmp) / "AAPT" / "2026" / "07" / run_id
            for subdir in RUN_SUBDIRS:
                (run_root / subdir).mkdir(parents=True, exist_ok=True)
            write_json(run_root / "manifest" / "run_manifest.json", {"run_id": run_id, "db_update_enabled": False})
            write_json(run_root / "manifest" / "report_manifest.json", {"row_count": 1})
            write_empty_parser_warnings(run_root)
            (run_root / "raw-recon-report" / "raw.csv").write_text(
                ",".join(BASE_COLUMNS) + "\n" + ",".join([""] * len(BASE_COLUMNS)) + "\n",
                encoding="utf-8",
            )
            refined_header = BASE_COLUMNS + APPROVED_REFINED_COLUMNS
            row = {column: "" for column in refined_header}
            row["agent_match_status"] = "suggested_match"
            row["human_verified_status"] = "verified"
            with (run_root / "refined-recon-report" / "refined.csv").open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=refined_header)
                writer.writeheader()
                writer.writerow(row)

            result = subprocess.run(
                [sys.executable, str(SCRIPTS / "validate_run.py"), "--config", str(config), "--run-root", str(run_root)],
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertNotEqual(0, result.returncode)
        self.assertIn("human_verified_invoice_number is required", result.stderr + result.stdout)


if __name__ == "__main__":
    unittest.main()
