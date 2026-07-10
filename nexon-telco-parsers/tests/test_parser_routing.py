from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PACK_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from parser_core.parse_provider_invoice import PARSER_MODULES, select_parser  # noqa: E402


class ParserRoutingTests(unittest.TestCase):
    def test_non_optus_uses_canonical_provider_parser(self) -> None:
        self.assertEqual("aapt", select_parser("AAPT", [Path("invoice.zip")]))

    def test_optus_pdf_uses_pdf_route(self) -> None:
        self.assertEqual("optus_pdf", select_parser("Optus", [Path("invoice.pdf")]))

    def test_optus_excel_or_voice_uses_excel_voice_route(self) -> None:
        self.assertEqual("optus_excel_voice", select_parser("Optus", [Path("invoice.xlsx")]))

    def test_optus_mixed_package_is_ambiguous(self) -> None:
        with self.assertRaises(ValueError):
            select_parser("Optus", [Path("invoice.pdf"), Path("voice.xlsx")])

    def test_parser_modules_route_to_provider_adapters(self) -> None:
        self.assertEqual("provider_adapters.aapt.parser", PARSER_MODULES["aapt"])
        self.assertEqual("provider_adapters.optus.parser_pdf", PARSER_MODULES["optus_pdf"])
        self.assertEqual("provider_adapters.optus.parser_excel_voice", PARSER_MODULES["optus_excel_voice"])

    def test_cli_default_config_resolves_from_pack_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_dir = root / "input"
            input_dir.mkdir()
            output = root / "provider_lines.json"
            warnings = root / "parser_warnings.json"
            manifest = root / "parser_manifest.json"

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "parse_provider_invoice.py"),
                    "--provider",
                    "AAPT",
                    "--input-dir",
                    str(input_dir),
                    "--output",
                    str(output),
                    "--warnings",
                    str(warnings),
                    "--run-id",
                    "AAPT_20260709_153012_A1B2C",
                    "--manifest",
                    str(manifest),
                ],
                cwd=PACK_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(3, result.returncode, result.stderr + result.stdout)
            warning_rows = json.loads(warnings.read_text(encoding="utf-8"))
            manifest_payload = json.loads(manifest.read_text(encoding="utf-8"))

        self.assertEqual("aapt", warning_rows[0]["parser"])
        self.assertEqual("parser_warning", manifest_payload["status"])
        self.assertEqual("AAPT_20260709_153012_A1B2C", manifest_payload["run_id"])


if __name__ == "__main__":
    unittest.main()
