from __future__ import annotations

import json
import importlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PACK_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from parser_core.parse_provider_invoice import PARSER_MODULES, PROVIDER_PARSER_KEYS, select_parser  # noqa: E402


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

    def test_optus_empty_package_fails_before_branch_selection(self) -> None:
        with self.assertRaisesRegex(ValueError, "no source files"):
            select_parser("Optus", [])

    def test_optus_voice_is_not_a_provider_name(self) -> None:
        with self.assertRaisesRegex(ValueError, "Provider is not supported"):
            select_parser("OptusVoice", [Path("voice.dat")])

    def test_parser_modules_route_to_provider_adapters(self) -> None:
        self.assertEqual(
            {
                "aapt": "provider_adapters.aapt.parser",
                "telstra": "provider_adapters.telstra.parser",
                "optus_pdf": "provider_adapters.optus.parser_pdf",
                "optus_excel_voice": "provider_adapters.optus.parser_excel_voice",
                "vocus": "provider_adapters.vocus.parser",
                "megaport": "provider_adapters.megaport.parser",
                "equinix": "provider_adapters.equinix.parser",
            },
            PARSER_MODULES,
        )

    def test_provider_parser_key_registry_is_complete(self) -> None:
        self.assertEqual(
            {
                "AAPT": "aapt",
                "Telstra": "telstra",
                "Vocus": "vocus",
                "Megaport": "megaport",
                "Equinix": "equinix",
            },
            PROVIDER_PARSER_KEYS,
        )
        for parser_key in PROVIDER_PARSER_KEYS.values():
            self.assertIn(parser_key, PARSER_MODULES)

    def test_optus_is_one_provider_with_two_isolated_parser_branches(self) -> None:
        self.assertNotIn("Optus", PROVIDER_PARSER_KEYS)
        self.assertEqual(
            {
                "optus_pdf": "provider_adapters.optus.parser_pdf",
                "optus_excel_voice": "provider_adapters.optus.parser_excel_voice",
            },
            {
                parser_key: module_name
                for parser_key, module_name in PARSER_MODULES.items()
                if module_name.startswith("provider_adapters.optus.")
            },
        )

    def test_parser_modules_are_importable_adapter_modules(self) -> None:
        for parser_key, module_name in PARSER_MODULES.items():
            with self.subTest(parser_key=parser_key):
                self.assertTrue(module_name.startswith("provider_adapters."))
                module = importlib.import_module(module_name)
                self.assertTrue(callable(module.parse))

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
