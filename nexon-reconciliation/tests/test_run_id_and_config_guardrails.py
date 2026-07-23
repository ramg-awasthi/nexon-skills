from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from recon_core.common import ensure_db_update_disabled, generate_run_id, resolve_run_id_collision, validate_run_id  # noqa: E402


def write_reviewed_workbook(path: Path, *, status: str = "deferred", invoice_number: str = "") -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Result"
    sheet.append(
        [
            "line_id",
            "provider",
            "service_id_raw",
            "human_verified_status",
            "human_verified_invoice_number",
        ]
    )
    sheet.append(["line-1", "AAPT", "SVC-1", status, invoice_number])
    workbook.save(path)


def write_approval(path: Path, report: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "run_id": "AAPT_20260709_153012_A1B2C",
                "report_id": hashlib.sha256(report.read_bytes()).hexdigest(),
                "approved_row_ids": ["line-1"],
                "approved_by": "reviewer@nexon.com.au",
                "approved_at": "2026-07-09T15:35:00+10:00",
                "eligibility_policy_version": "accepted-resolution-v1",
                "dry_run_hash": "dry-run-sha256",
                "change_ticket": "CAB-123",
                "batch_idempotency_key": "AAPT_20260709_153012_A1B2C:CAB-123",
            }
        ),
        encoding="utf-8",
    )


class RunIdAndConfigGuardrailTests(unittest.TestCase):
    def test_generated_run_id_is_valid(self) -> None:
        run_id = generate_run_id("AAPT", "source-checksum", datetime.fromisoformat("2026-07-09T15:30:12+10:00"))
        self.assertRegex(run_id, r"^AAPT_20260709_153012_[A-F0-9]{5}$")
        self.assertTrue(validate_run_id(run_id))

    def test_rejects_invalid_calendar_time(self) -> None:
        self.assertFalse(validate_run_id("AAPT_20261340_999999_A1B2C"))

    def test_rejects_unknown_provider_and_bad_hash(self) -> None:
        self.assertFalse(validate_run_id("Unknown_20260709_153012_A1B2C"))
        self.assertFalse(validate_run_id("AAPT_20260709_153012_a1b2c"))
        self.assertFalse(validate_run_id("AAPT_20260709_153012_A1B2C_EXTRA"))

    def test_collision_uses_next_hash_window_and_records_attempts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            created_at = datetime.fromisoformat("2026-07-09T15:30:12+10:00")
            first_run_id = generate_run_id("AAPT", "source-checksum", created_at)
            (parent / first_run_id).mkdir()

            resolved, collision = resolve_run_id_collision("AAPT", "source-checksum", parent, created_at)

        self.assertNotEqual(first_run_id, resolved)
        self.assertTrue(validate_run_id(resolved))
        self.assertTrue(collision["collision_detected"])
        self.assertEqual(1, collision["hash_offset"])
        self.assertEqual(2, len(collision["attempts"]))

    def test_db_update_must_be_explicitly_false(self) -> None:
        ensure_db_update_disabled({"features": {"db_update_enabled": False}})
        with self.assertRaises(RuntimeError):
            ensure_db_update_disabled({"features": {"db_update_enabled": True}})
        with self.assertRaises(RuntimeError):
            ensure_db_update_disabled({"features": {}})

    def test_optional_db_update_refuses_default_false(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "recon_settings.yaml"
            config.write_text("features:\n  db_update_enabled: false\n", encoding="utf-8")
            refined = Path(tmp) / "refined.xlsx"
            write_reviewed_workbook(refined)
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "optional_db_update.py"),
                    "--config",
                    str(config),
                    "--refined-report",
                    str(refined),
                    "--audit-output",
                    str(Path(tmp) / "audit.json"),
                ],
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertNotEqual(0, result.returncode)
        self.assertIn("DB update is disabled", result.stderr + result.stdout)

    def test_optional_db_update_dry_run_allows_deferred_without_invoice_when_configured(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "recon_settings.yaml"
            config.write_text(
                """
features:
  db_update_enabled: true
db_update_policy:
  allow_deferred_without_invoice_number: true
""".lstrip(),
                encoding="utf-8",
            )
            refined = root / "refined.xlsx"
            write_reviewed_workbook(refined)
            approval = root / "approval.json"
            write_approval(approval, refined)
            audit = root / "audit.json"

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "optional_db_update.py"),
                    "--config",
                    str(config),
                    "--refined-report",
                    str(refined),
                    "--audit-output",
                    str(audit),
                    "--approval-artifact",
                    str(approval),
                    "--dry-run",
                ],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(0, result.returncode, result.stderr + result.stdout)
            plan = json.loads(audit.read_text(encoding="utf-8"))
            self.assertEqual(1, plan["planned_update_count"])
            self.assertTrue(plan["planned_updates"][0]["partial_update"])
            self.assertEqual("write_partial_deferred_update", plan["planned_updates"][0]["update_action"])

    def test_optional_db_update_dry_run_blocks_deferred_without_invoice_when_configured(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "recon_settings.yaml"
            config.write_text(
                """
features:
  db_update_enabled: true
db_update_policy:
  allow_deferred_without_invoice_number: false
""".lstrip(),
                encoding="utf-8",
            )
            refined = root / "refined.xlsx"
            write_reviewed_workbook(refined)
            approval = root / "approval.json"
            write_approval(approval, refined)

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "optional_db_update.py"),
                    "--config",
                    str(config),
                    "--refined-report",
                    str(refined),
                    "--audit-output",
                    str(root / "audit.json"),
                    "--approval-artifact",
                    str(approval),
                    "--dry-run",
                ],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertNotEqual(0, result.returncode)
            self.assertIn("Deferred rows without invoice number are not allowed", result.stderr + result.stdout)


if __name__ == "__main__":
    unittest.main()
