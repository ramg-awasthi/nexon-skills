from __future__ import annotations

import builtins
import json
import sys
import tempfile
from types import SimpleNamespace
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from provider_adapters.aapt import parser as aapt_parser  # noqa: E402
from provider_adapters.equinix import parser as equinix_parser  # noqa: E402
from provider_adapters.megaport import parser as megaport_parser  # noqa: E402
from provider_adapters.optus import parser_excel_voice, parser_pdf  # noqa: E402
from provider_adapters.telstra import parser as telstra_parser  # noqa: E402
from provider_adapters.vocus import parser as vocus_parser  # noqa: E402
from parser_core import parse_provider_invoice  # noqa: E402
from parser_core.common import ensure_provider, load_config, write_json  # noqa: E402


class ParserAdapterStubTests(unittest.TestCase):
    def test_all_provider_stubs_fail_closed_until_migrated(self) -> None:
        modules = [
            aapt_parser,
            telstra_parser,
            parser_pdf,
            parser_excel_voice,
            vocus_parser,
            megaport_parser,
            equinix_parser,
        ]
        for module in modules:
            with self.subTest(module=module.__name__):
                with self.assertRaisesRegex(NotImplementedError, "parser_unavailable"):
                    module.parse([], {"provider": module.__name__})

    def test_common_provider_validation_and_json_writer(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bad_config = root / "bad.yaml"
            bad_config.write_text("- not\n- a\n- mapping\n", encoding="utf-8")

            self.assertEqual({"provider": "AAPT"}, ensure_provider({}, "AAPT"))
            with self.assertRaisesRegex(ValueError, "Provider is not supported"):
                ensure_provider({}, "Unknown")
            with self.assertRaisesRegex(ValueError, "Config must be a mapping"):
                load_config(bad_config)

            output = Path(tmp) / "nested" / "payload.json"
            write_json(output, {"b": 1})
            self.assertEqual('{\n  "b": 1\n}\n', output.read_text(encoding="utf-8"))

    def test_select_parser_rejects_unknown_provider(self) -> None:
        with self.assertRaisesRegex(ValueError, "Provider is not supported"):
            parse_provider_invoice.select_parser("Unknown", [])

    def test_parser_cli_success_path_with_fake_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "config.yaml"
            input_dir = root / "input"
            output = root / "provider_lines.json"
            warnings = root / "warnings.json"
            manifest = root / "manifest.json"
            config.write_text("features: {}\n", encoding="utf-8")
            input_dir.mkdir()
            (input_dir / "invoice.csv").write_text("invoice", encoding="utf-8")

            old_import_module = parse_provider_invoice.importlib.import_module
            old_argv = sys.argv[:]
            try:
                parse_provider_invoice.importlib.import_module = lambda name: SimpleNamespace(
                    parse=lambda source_files, context: {
                        "headers": ["line_id"],
                        "lines": [{"line_id": "line-1", "source_file_count": len(source_files), "parser_key": context["parser_key"]}],
                    }
                )
                sys.argv = [
                    "parse_provider_invoice",
                    "--config",
                    str(config),
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
                ]
                self.assertEqual(0, parse_provider_invoice.main())
            finally:
                parse_provider_invoice.importlib.import_module = old_import_module
                sys.argv = old_argv

            self.assertEqual("line-1", json.loads(output.read_text(encoding="utf-8"))["lines"][0]["line_id"])
            self.assertEqual([], json.loads(warnings.read_text(encoding="utf-8")))
            self.assertEqual("parsed", json.loads(manifest.read_text(encoding="utf-8"))["status"])

    def test_parser_common_load_config_requires_pyyaml(self) -> None:
        old_import = builtins.__import__
        try:
            def fake_import(name: str, *args: object, **kwargs: object) -> object:
                if name == "yaml":
                    raise ImportError("no yaml")
                return old_import(name, *args, **kwargs)

            builtins.__import__ = fake_import
            with tempfile.TemporaryDirectory() as tmp:
                config = Path(tmp) / "config.yaml"
                config.write_text("features: {}\n", encoding="utf-8")
                with self.assertRaisesRegex(RuntimeError, "PyYAML is required"):
                    load_config(config)
        finally:
            builtins.__import__ = old_import


if __name__ == "__main__":
    unittest.main()
