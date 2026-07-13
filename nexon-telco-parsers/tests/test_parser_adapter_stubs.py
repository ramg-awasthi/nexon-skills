from __future__ import annotations

import builtins
import csv
import json
import sys
import tempfile
import zipfile
from datetime import datetime
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
from provider_adapters.common import parse_money  # noqa: E402
from parser_core import parse_provider_invoice  # noqa: E402
from parser_core.common import ensure_provider, load_config, write_json  # noqa: E402


class ParserAdapterStubTests(unittest.TestCase):
    def test_provider_adapters_are_implemented_not_placeholders(self) -> None:
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
                self.assertNotIn("NotImplementedError", Path(module.__file__).read_text(encoding="utf-8"))

    def test_telstra_csv_parser_maps_current_and_legacy_columns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "detail_report.csv"
            with path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(
                    [
                        "Month",
                        "ACC_NUM",
                        "BILL_NUM",
                        "SERVICE_NUMBER",
                        "Service Description 1",
                        "Service Description 2",
                        "CHARGE_TYPE",
                        "CALL_TYPE",
                        "FROM_DATE",
                        "TO_DATE",
                        "NUMBER_OF_CALLS",
                        "EXCL_GST",
                        "GST",
                        "INCL_GST",
                    ]
                )
                writer.writerow(
                    [
                        "July 2022",
                        "1436132800",
                        "K 240 131 101-5",
                        "N2702671Q",
                        "",
                        "",
                        "Services & equipment rental",
                        "Service & equipment",
                        "06/07/2022",
                        "09/09/2022",
                        "",
                        "$209.86",
                        "$20.99",
                        "$230.85",
                    ]
                )

            result = telstra_parser.parse([path], {"provider": "Telstra", "run_id": "Telstra_20260709_153012_A1B2C"})

        line = result["lines"][0]
        self.assertEqual("1436132800", line["provider_account"])
        self.assertEqual("N2702671Q", line["service_id_raw"])
        self.assertEqual("K 240 131 101-5", line["invoice_number"])
        self.assertEqual("Recurring", line["charge_type"])
        self.assertEqual("2022-07-06", line["billing_period_start"])
        self.assertEqual("209.86", line["amount"])

    def test_megaport_csv_parser_uses_previous_month_period(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "AUS00021607.csv"
            path.write_text(
                "Customer number,Managed Account,Invoice number,Deal id,Deal %,Product,Description,From,To,Invoice Date,Due Date,Currency,Amount,Tax,Service id,Your reference,Purchase Order\n"
                "24,,AUS00021607,,0,VXC,VXC Virtual Cross Connect,2099/01/01,2099/01/31,2022/12/01,2022/12/31,AUD,85,8.5,0972e091,,\n",
                encoding="utf-8",
            )
            result = megaport_parser.parse([path], {"provider": "Megaport", "run_id": "Megaport_20260709_153012_A1B2C"})

        line = result["lines"][0]
        self.assertEqual("24", line["provider_account"])
        self.assertEqual("AUS00021607", line["invoice_number"])
        self.assertEqual("0972e091", line["service_id_raw"])
        self.assertEqual("0972e091", line["service_id_normalized"])
        self.assertEqual("2022-11-01", line["billing_period_start"])
        self.assertEqual("2022-11-30", line["billing_period_end"])
        self.assertEqual("85.00", line["amount"])

    def test_vocus_csv_parser_maps_invoice_columns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "Invoice_P944726.csv"
            with path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(
                    [
                        "Account_ID",
                        "Account_Name",
                        "Invoice_ID",
                        "Invoice_Issue_Date",
                        "Purchase_Order_Reference",
                        "Charge_level",
                        "Charge_Type",
                        "Contract_Term",
                        "Service_ID_Primary",
                        "Service_ID_Secondary",
                        "Customer_Supplied_Ref",
                        "Service_Invoice_Description",
                        "Site A ID",
                        "Site B ID",
                        "Service_Type",
                        "Product",
                        "Charge_Description",
                        "Charge_Event_Date",
                        "Charge_Period_From_Date",
                        "Charge_Period_To_Date",
                        "Charge_Quantity",
                        "Charge_ex_Tax_Total_Amount",
                        "Charge_Tax_Total_Amount",
                        "Charge_Currency",
                        "Cost_Centre_Level1",
                        "Cost_Centre_Level2",
                        "Cost_Centre_Level3",
                    ]
                )
                writer.writerow(
                    [
                        "CN10712",
                        "Nexon Asia Pacific Pty Ltd",
                        "P944726",
                        "2022-12-01",
                        "",
                        "Service-level",
                        "Charge",
                        "Contract expired",
                        "CC052702",
                        "CC052702",
                        "",
                        "Point A =  || Point B =  || Bandwidth = ",
                        "",
                        "",
                        "Cross Connect",
                        "",
                        "Cross Connect (01/12/2022 - 31/12/2022): Rack 30 Port 6",
                        "2022-12-01",
                        "2099-01-01",
                        "2099-01-31",
                        "1.00",
                        "51.50",
                        "5.15",
                        "AUD",
                        "",
                        "",
                        "",
                    ]
                )
            result = vocus_parser.parse([path], {"provider": "Vocus", "run_id": "Vocus_20260709_153012_A1B2C"})

        line = result["lines"][0]
        self.assertEqual("CN10712", line["provider_account"])
        self.assertEqual("P944726", line["invoice_number"])
        self.assertEqual("CC052702", line["service_id_raw"])
        self.assertEqual("51.50", line["amount"])
        self.assertEqual("2022-12-01", line["billing_period_start"])
        self.assertEqual("2022-12-31", line["billing_period_end"])

    def test_optus_pdf_text_parser_maps_service_summary_rows(self) -> None:
        lines = [
            "Invoice number",
            "24851489",
            "Account period",
            "01 Jul 22 to 31 Jul 22",
            "Customer account number",
            "9124 3228 26",
            "SERVICE SUMMARY",
            "Optus Evolve Ethernet WAN",
            "Service number Page ref Amount",
            "EVC00080698 6 1,250.00",
            "EVC00003195 11 164.58 CR",
            "Optus Evolve Internet",
            "Service number Page ref Amount",
            "33VS J32MD21 53AS EVC002 18 399.00",
            "SERVICE DETAILS",
        ]
        result, _line_index = parser_pdf._lines_from_pdf_text(
            lines=lines,
            source_file=Path("optus.pdf"),
            context={"provider": "Optus", "run_id": "Optus_20260709_153012_A1B2C"},
            line_index_start=1,
        )

        self.assertEqual(3, len(result))
        self.assertEqual("9124322826", result[0]["provider_account"])
        self.assertEqual("24851489", result[0]["invoice_number"])
        self.assertEqual("EVC00080698", result[0]["service_id_raw"])
        self.assertEqual("1250.00", result[0]["amount"])
        self.assertEqual("-164.58", result[1]["amount"])
        self.assertEqual("Non-recurring", result[1]["charge_type"])
        self.assertEqual("33VSJ32MD2153ASEVC002", result[2]["service_id_normalized"])

    def test_optus_voice_zip_parser_maps_srvs_and_wusg_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mdx_NEX1_21887946000127_943803681_20230802_000172.dat.zip"
            lines = [
                "1|HDR|MDX|ARBR|NEX1|20230802|023001|000172|21887946000127|01|",
                "2|ACCS|21887946000127|943803681|0|20230801|NEXON ASIA PACIFIC|||||||||||0|20230701|20230731|0|0|0|0|0|0|0|0|20230815||",
                "3|SRVS|21887946000127|943803681|0|20230801|30.0000||0240323500|0175093239000000|400|Optus Evolve Voice|20.0000|0.0000|10.0000|",
                "4|WUSG|21887946000127|943803681|0|20230801|5.5000|",
            ]
            with zipfile.ZipFile(path, "w") as zfile:
                zfile.writestr("sample.dat", "\n".join(lines))
            result = parser_excel_voice.parse([path], {"provider": "Optus", "run_id": "Optus_20260709_153012_A1B2C"})

        self.assertEqual(3, len(result["lines"]))
        self.assertEqual("21887946000127", result["lines"][0]["provider_account"])
        self.assertEqual("943803681", result["lines"][0]["invoice_number"])
        self.assertEqual("240323500", result["lines"][0]["service_id_normalized"])
        self.assertEqual("20.00", result["lines"][0]["amount"])
        self.assertEqual("Usage", result["lines"][1]["charge_type"])
        self.assertEqual("10000", result["lines"][2]["service_id_raw"])
        self.assertEqual("Withdrawn Usage", result["lines"][2]["detail_description"])

    def test_equinix_rows_parser_maps_normal_and_infrastructure_split_rows(self) -> None:
        rows = [
            {
                "Customer Account #": 116257,
                "Transaction #": 131210216346,
                "Recurring From Date": datetime(2025, 9, 1),
                "Recurring To Date": datetime(2025, 9, 30),
                "Serial #": "20690489-A",
                "Product Description": "Cross Connect- Single-Mode Fiber",
                "Ibx Center": "ME1",
                "Product Category": "Interconnection",
                "Activity Type": "Recurring Charges",
                "Recurring Amount": 190,
                "Non Recurring Amount": None,
                "Adjustments": -15.2,
            },
            {
                "Customer Account #": 116257,
                "Transaction #": 131210216346,
                "Recurring From Date": datetime(2025, 9, 1),
                "Recurring To Date": datetime(2025, 9, 30),
                "Serial #": "4-24685509452",
                "Product Description": "Power",
                "Ibx Center": "SY3",
                "Product Category": "Power",
                "Activity Type": "Recurring Charges",
                "Recurring Amount": 100,
                "Non Recurring Amount": None,
                "Adjustments": 0,
            },
        ]
        result, _line_index = equinix_parser._rows_to_lines(
            rows=rows,
            source_file=Path("equinix.xlsx"),
            context={"provider": "Equinix", "run_id": "Equinix_20260709_153012_A1B2C"},
            line_index_start=1,
        )

        self.assertEqual(5, len(result))
        self.assertEqual("116257", result[0]["provider_account"])
        self.assertEqual("131210216346", result[0]["invoice_number"])
        self.assertEqual("20690489", result[0]["service_id_raw"])
        self.assertEqual("174.80", result[0]["amount"])
        self.assertEqual("24685509452", result[1]["service_id_raw"])
        self.assertEqual("40.00", result[1]["amount"])
        self.assertEqual("50.00", result[2]["infrastructure_cost"])
        self.assertEqual("Equinix Power - Cloud Infrastructure Cost", result[2]["service_type"])

    def test_aapt_zip_parser_reads_rec_files_without_db_side_effects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "23362528.0.zip"
            with zipfile.ZipFile(path, "w") as zfile:
                zfile.writestr(
                    "sample-rec001.csv",
                    "Record Type,Account Number,Statement Date,Payment Due Date,Bill Period From Date,Bill Period To Date,Bill Number,Total (ex GST)\n"
                    "001,2000005729,01 Apr 2026,30 Apr 2026,01 Mar 2026,31 Mar 2026,23362528,\"$179,730.26\"\n",
                )
                zfile.writestr(
                    "sample-rec005.csv",
                    "Record Type,Account Number,Service Number,Service Type,Charge Type,Details,Date From,Date To,Charge(ex GST)\n"
                    "005,2000005729,100252742,NBN-FTTP (NTU),Adjustment,Disconnect of NBN,24 Mar 2026,31 Mar 2026,-$59.35\n",
                )
                zfile.writestr(
                    "sample-rec010.csv",
                    "Record Type,Account Number,Service Number,Service Type,Usage Type Description,Date,Charge(ex GST)\n"
                    "010,2000005729,6009527,IP-Line,IP Download,01 Mar 2026,$1.25\n"
                    "010,2000005729,6009527,IP-Line,IP Download,02 Mar 2026,$2.75\n",
                )
            result = aapt_parser.parse([path], {"provider": "AAPT", "run_id": "AAPT_20260709_153012_A1B2C"})

        self.assertEqual(2, len(result["lines"]))
        self.assertEqual("2000005729#1", result["lines"][0]["provider_account"])
        self.assertEqual("23362528", result["lines"][0]["invoice_number"])
        self.assertEqual("-59.35", result["lines"][0]["amount"])
        self.assertEqual("4.00", result["lines"][1]["amount"])

    def test_aapt_rec002_special_account_rolls_up_origin_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "2000060308.zip"
            with zipfile.ZipFile(path, "w") as zfile:
                zfile.writestr(
                    "sample-rec001.csv",
                    "Record Type,Account Number,Statement Date,Payment Due Date,Bill Period From Date,Bill Period To Date,Bill Number,Total (ex GST)\n"
                    "001,2000060308,01 Apr 2026,30 Apr 2026,01 Mar 2026,31 Mar 2026,23362528,\"$179,730.26\"\n",
                )
                zfile.writestr(
                    "sample-rec002.csv",
                    "Record Type,Account Number,Origin,Date,Charge(ex GST),Service Type\n"
                    "002,2000060308,6123456700,01 Mar 2026,$1.25,Voice\n"
                    "002,2000060308,6123456799,02 Mar 2026,$2.75,Voice\n",
                )
            result = aapt_parser.parse([path], {"provider": "AAPT", "run_id": "AAPT_20260709_153012_A1B2C"})

        self.assertEqual(1, len(result["lines"]))
        self.assertEqual("6123456700", result["lines"][0]["service_id_raw"])
        self.assertEqual("4.00", result["lines"][0]["amount"])

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

    def test_common_money_parser_fails_closed_on_invalid_amount(self) -> None:
        with self.assertRaisesRegex(ValueError, "invalid money value"):
            parse_money("not-a-number")

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

    def test_parser_cli_unexpected_adapter_error_fails_closed(self) -> None:
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

            def raise_runtime_error(_source_files: object, _context: object) -> object:
                raise RuntimeError("raw parser detail that should not be copied")

            old_import_module = parse_provider_invoice.importlib.import_module
            old_argv = sys.argv[:]
            try:
                parse_provider_invoice.importlib.import_module = lambda name: SimpleNamespace(parse=raise_runtime_error)
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
                self.assertEqual(3, parse_provider_invoice.main())
            finally:
                parse_provider_invoice.importlib.import_module = old_import_module
                sys.argv = old_argv

            self.assertEqual([], json.loads(output.read_text(encoding="utf-8"))["lines"])
            warning = json.loads(warnings.read_text(encoding="utf-8"))[0]
            self.assertEqual("error", warning["severity"])
            self.assertIn("parser_failed: unexpected parser error", warning["message"])
            self.assertNotIn("raw parser detail", warning["message"])
            self.assertEqual("parser_warning", json.loads(manifest.read_text(encoding="utf-8"))["status"])

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
