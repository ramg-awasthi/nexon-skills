from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from recon_core import apply_exception_investigation, record_failure  # noqa: E402


def call_main(module: object, argv: list[object]) -> int:
    old_argv = sys.argv[:]
    sys.argv = [getattr(module, "__name__", "tool")] + [str(item) for item in argv]
    try:
        return module.main()
    finally:
        sys.argv = old_argv


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


class RemainingWorkflowCoverageTests(unittest.TestCase):
    def test_investigation_rejects_non_collection_payload(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "row/update list"):
            apply_exception_investigation._rows(None)

    def test_apply_investigation_without_optional_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            matches = root / "matches.json"
            investigation = root / "investigation.json"
            output = root / "output.json"
            matches.write_text(
                json.dumps({"rows": [{"line_id": "line-1", "provider": "AAPT"}]}),
                encoding="utf-8",
            )
            investigation.write_text(json.dumps({"rows": []}), encoding="utf-8")

            result = call_main(
                apply_exception_investigation,
                [
                    "--matches",
                    matches,
                    "--investigation",
                    investigation,
                    "--output",
                    output,
                ],
            )

            self.assertEqual(0, result)
            self.assertEqual(
                {"rows": [{"line_id": "line-1", "provider": "AAPT"}]},
                json.loads(output.read_text(encoding="utf-8")),
            )

    def test_record_failure_honors_explicit_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "config.yaml"
            output = root / "explicit" / "failure.json"
            write_config(config)

            result = call_main(
                record_failure,
                [
                    "--config",
                    config,
                    "--provider",
                    "AAPT",
                    "--stage",
                    "parse",
                    "--reason",
                    "invalid_invoice",
                    "--output",
                    output,
                ],
            )

            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(0, result)
            self.assertEqual("invalid_invoice", payload["failure_code"])
            self.assertIsNone(payload["run_id"])
            self.assertFalse(payload["notification_required"])


if __name__ == "__main__":
    unittest.main()
