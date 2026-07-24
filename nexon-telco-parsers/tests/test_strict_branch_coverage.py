from __future__ import annotations

import builtins
import csv
import io
import json
import sys
import tempfile
import types
import unittest
import zipfile
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from parser_core import parse_provider_invoice  # noqa: E402
from provider_adapters import common  # noqa: E402
from provider_adapters.aapt import parser as aapt  # noqa: E402
from provider_adapters.equinix import parser as equinix  # noqa: E402
from provider_adapters.megaport import parser as megaport  # noqa: E402
from provider_adapters.optus import parser_excel_voice as optus_voice  # noqa: E402
from provider_adapters.optus import parser_pdf as optus_pdf  # noqa: E402
from provider_adapters.telstra import parser as telstra  # noqa: E402
from provider_adapters.vocus import parser as vocus  # noqa: E402


RUN_IDS = {
    provider: f"{provider}_20260723_200000_A1B2C"
    for provider in ("AAPT", "Equinix", "Optus", "Telstra", "Megaport", "Vocus")
}


def context(provider: str) -> dict[str, str]:
    return {"provider": provider, "run_id": RUN_IDS[provider]}


def write_zip(path: Path, members: dict[str, str], *, directory: str | None = None) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        if directory:
            archive.writestr(f"{directory.rstrip('/')}/", "")
        for name, payload in members.items():
            archive.writestr(name, payload)


class StrictParserBranchCoverageTests(unittest.TestCase):
    def test_aapt_composite_zip_covers_all_record_families_and_charge_rules(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            package = Path(tmp) / "aapt-composite.zip"
            write_zip(
                package,
                {
                    "README.txt": "not an invoice member",
                    "invoice-rec001.csv": (
                        "Record Type,Account Number,Statement Date,Payment Due Date,"
                        "Bill Period From Date,Bill Period To Date,Bill Number,Total (ex GST)\n"
                        "001,2000005729,01 Apr 2026,30 Apr 2026,01 Mar 2026,"
                        "31 Mar 2026,23362528,$500.00\n"
                    ),
                    "invoice-rec005.csv": (
                        "Record Type,Account Number,Service Number,Service Type,Charge Type,"
                        "Details,Date From,Date To,Charge(ex GST)\n"
                        "005,2000005729,SVC-1,NBN,Recurring Charge,Rental,"
                        "01 Mar 2026,31 Mar 2026,$10.00\n"
                        "005,2000005729,SVC-2,NBN,Adjustment,Credit,,,-$2.00\n"
                        "005,2000005729,SVC-3,NBN,Discount,Discount,,,$-1.00\n"
                        "005,2000005729,SVC-4,NBN,Install,Install,,,$5.00\n"
                    ),
                    "invoice-rec002.csv": (
                        "Record Type,Account Number,Origin,Date,Charge(ex GST),Service Type\n"
                        "002,2000005729,,01 Mar 2026,$99.00,Voice\n"
                        "002,2000005729,6123456700,01 Mar 2026,$1.25,Voice\n"
                        "002,2000005729,6123456700,02 Mar 2026,$2.75,Voice\n"
                    ),
                    "invoice-rec006.csv": (
                        "Record Type,Account Number,Service Number,Date,Charge(ex GST),Service Type\n"
                        "006,2000005729,1800123456,01 Mar 2026,$3.00,Inbound\n"
                    ),
                    "invoice-rec010.csv": (
                        "Record Type,Account Number,Service Number,Date,Charge(ex GST),Service Type\n"
                        "010,2000005729,6000000,01 Mar 2026,$0.00,IP-Line\n"
                        "010,2000005729,6000001,01 Mar 2026,$4.00,IP-Line\n"
                    ),
                    "invoice-rec004.csv": (
                        "Record Type,Account Number,Description,Details,Date From,Date To,Charge(ex GST)\n"
                        "004,2000005729,Adjustment,Account credit,01 Mar 2026,,-$3.00\n"
                        "004,2000005729,Discount,Account discount,,,2.00\n"
                        "004,2000005729,Fee,Account fee,,,7.00\n"
                    ),
                },
            )

            result = aapt.parse([package, Path(tmp) / "ignored.txt"], context("AAPT"))

        lines = result["lines"]
        self.assertEqual(10, len(lines))
        self.assertEqual(
            {"Recurring", "Adjustment", "Discount", "Non-recurring", "Usage"},
            {line["charge_type"] for line in lines},
        )
        self.assertFalse(any(line["service_id_raw"] == "6000000" for line in lines))
        self.assertEqual(
            {"Call Usage", "1800/1300/13 Usage", "Internet Usage"},
            {
                line["detail_description"]
                for line in lines
                if line["charge_type"] == "Usage"
            },
        )
        self.assertEqual(
            {"Adjustment", "Discount", "Non-recurring"},
            {
                line["charge_type"]
                for line in lines
                if line["service_type"] == "Account Level Charges/Adj/Disc"
            },
        )

    def test_aapt_rejects_wrong_input_missing_header_and_empty_header(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaisesRegex(ValueError, "expects supplier ZIP"):
                aapt.parse([root / "invoice.csv"], context("AAPT"))

            missing = root / "missing.zip"
            write_zip(missing, {"invoice-rec005.csv": "Service Number\nSVC-1\n"})
            with self.assertRaisesRegex(ValueError, "missing rec001"):
                aapt.parse([missing], context("AAPT"))

            empty = root / "empty.zip"
            write_zip(empty, {"invoice-rec001.csv": "Account Number,Bill Number\n"})
            with self.assertRaisesRegex(ValueError, "no account header"):
                aapt.parse([empty], context("AAPT"))

    def test_equinix_allocation_rules_me2_and_normal_rows(self) -> None:
        base = {
            "Customer Account #": 116257.0,
            "Transaction #": 131210216346.0,
            "Recurring From Date": datetime(2025, 9, 1),
            "Recurring To Date": datetime(2025, 9, 30),
            "Product Description": "Equinix service",
            "Ibx Center": "SY3",
            "Product Category": "Infrastructure",
            "Activity Type": "Recurring Charges",
            "Recurring Amount": 40,
            "Non Recurring Amount": 25,
            "Adjustments": 0,
        }
        rows = [
            {**base, "Serial #": "00110-13929691.1", "Recurring Amount": 400},
            {**base, "Serial #": "4-24685509452", "Recurring Amount": 100},
            {
                **base,
                "Serial #": "17_13103686568",
                "Activity Type": "One Time",
                "Non Recurring Amount": 200,
                "Adjustments": -20,
            },
            {**base, "Serial #": "ME2-55555.0", "Ibx Center": "ME2"},
            {
                **base,
                "Serial #": "",
                "Ibx Center": "ME2",
                "Activity Type": "One Time",
                "Adjustments": None,
            },
            {**base, "Serial #": "20690489-A_B", "Adjustments": -5},
            {
                **base,
                "Serial #": "10001",
                "Activity Type": "One Time",
                "Adjustments": "",
            },
        ]

        lines, next_index = equinix._rows_to_lines(
            rows=rows,
            source_file=Path("equinix.xlsx"),
            context=context("Equinix"),
            line_index_start=7,
        )

        self.assertEqual(7 + len(lines), next_index)
        self.assertEqual("116257", lines[0]["provider_account"])
        self.assertEqual("131210216346", lines[0]["invoice_number"])
        self.assertEqual(4, sum(line["service_id_raw"] == "13929691" for line in lines))
        self.assertEqual(4, sum(line["service_id_raw"] == "24685509452" for line in lines))
        self.assertEqual(4, sum(line["service_id_raw"] == "13103686568" for line in lines))
        self.assertTrue(
            any(
                line["service_location"] == "ME2"
                and line["charge_type"] == "Non-recurring"
                and "infrastructure_cost" in line
                for line in lines
            )
        )
        self.assertTrue(
            any(
                line["service_id_raw"] == "20690489"
                and "infrastructure_cost" not in line
                for line in lines
            )
        )

    def test_equinix_xlsx_loading_parse_and_failures(self) -> None:
        import openpyxl

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            valid = root / "invoice.xlsx"
            workbook = openpyxl.Workbook()
            sheet = workbook.active
            headers = [
                "Customer Account #",
                "Transaction #",
                "Recurring From Date",
                "Recurring To Date",
                "Serial #",
                "Product Description",
                "Ibx Center",
                "Product Category",
                "Activity Type",
                "Recurring Amount",
                "Non Recurring Amount",
                "Adjustments",
            ]
            sheet.append(headers)
            sheet.append(
                [
                    116257,
                    131210216346,
                    datetime(2025, 9, 1),
                    datetime(2025, 9, 30),
                    '"20690489-A"',
                    "Cross Connect",
                    "ME1",
                    "Interconnection",
                    "Recurring Charges",
                    190,
                    None,
                    -15.2,
                    "ignored beyond headers",
                ]
            )
            workbook.save(valid)

            result = equinix.parse([valid, root / "ignored.csv"], context("Equinix"))
            self.assertEqual(1, len(result["lines"]))
            self.assertEqual("20690489", result["lines"][0]["service_id_raw"])

            empty = root / "empty.xlsx"
            empty_workbook = openpyxl.Workbook()
            empty_workbook.save(empty)
            with self.assertRaisesRegex(ValueError, "workbook is empty"):
                equinix.parse([empty], context("Equinix"))

            with self.assertRaisesRegex(ValueError, "expects XLSX"):
                equinix.parse([root / "invoice.csv"], context("Equinix"))
            with self.assertRaisesRegex(ValueError, "no detail rows"):
                equinix._rows_to_lines(
                    rows=[],
                    source_file=valid,
                    context=context("Equinix"),
                    line_index_start=1,
                )

    def test_equinix_fails_closed_when_xlsx_dependency_is_unavailable(self) -> None:
        original_import = builtins.__import__

        def reject_openpyxl(name: str, *args: object, **kwargs: object) -> object:
            if name == "openpyxl":
                raise ImportError("not installed")
            return original_import(name, *args, **kwargs)

        with mock.patch.object(builtins, "__import__", side_effect=reject_openpyxl):
            with self.assertRaisesRegex(ValueError, "requires the openpyxl package"):
                equinix._load_xlsx_rows(Path("invoice.xlsx"))

    def test_equinix_value_number_and_service_helpers(self) -> None:
        self.assertEqual("fallback", equinix._value({"first": "", "second": "fallback"}, "first", "second"))
        self.assertEqual("", equinix._value({}, "missing"))
        self.assertEqual(Decimal("0.00"), equinix._amount(None))
        self.assertEqual(Decimal("1.25"), equinix._amount("$1.25"))
        self.assertEqual("12", equinix._number_text("12.0"))
        self.assertEqual("12", equinix._number_text(12.0))
        self.assertEqual("12", equinix._service_number('"12.0"'))
        self.assertEqual(["11111"], equinix._explode_service_number(""))
        self.assertEqual(["10001"], equinix._explode_service_number("10001"))

    def test_optus_pdf_context_summary_helpers_and_failures(self) -> None:
        self.assertEqual("", optus_pdf._parse_short_date("not a date"))
        self.assertEqual("2026-07-01", optus_pdf._parse_short_date("01 Jul 26"))
        self.assertEqual("2025-09-30", optus_pdf._parse_short_date("30 Sept 2025"))
        self.assertEqual([], optus_pdf._summary_sections([]))

        migrated = [
            "Migrated Account 0099 8877",
            "Invoice No: 0000",
            "Invoice Period: 01 Jul 22 to 31 Jul 2022",
        ]
        self.assertEqual(
            ("00998877", "0", "2022-07-01", "2022-07-31"),
            optus_pdf._extract_invoice_context(migrated),
        )
        self.assertEqual(("", "", "", ""), optus_pdf._extract_invoice_context(["no context"]))

        summary = [
            "outside summary",
            "SERVICE SUMMARY",
            "",
            "continued",
            "NEXON ASIA PACIFIC",
            "Total cost $1.00",
            "$1.00",
            "Issue Date 01 Jul 22",
            "Page 1",
            "Optus Evolve Internet (continued)",
            "SERVICE 001 3 100.00",
            "UNKNOWN HEADING",
            "SERVICE 002 4 5.00 CR",
            "SERVICE DETAILS",
            "SERVICE 003 5 99.00",
        ]
        rows = optus_pdf._summary_sections(summary)
        self.assertEqual(
            [
                ("SERVICE 001", "Optus Evolve Internet", "100.00"),
                ("SERVICE 002", "Optus Evolve Internet", "-5.00"),
            ],
            rows,
        )

        with self.assertRaisesRegex(ValueError, "missing account or invoice"):
            optus_pdf._lines_from_pdf_text(
                lines=["SERVICE SUMMARY", "SVC 1 1.00", "SERVICE DETAILS"],
                source_file=Path("invoice.pdf"),
                context=context("Optus"),
                line_index_start=1,
            )
        with self.assertRaisesRegex(ValueError, "no extractable service"):
            optus_pdf._lines_from_pdf_text(
                lines=[
                    "Customer account number 9124 3228 26",
                    "Invoice number 24851489",
                    "SERVICE SUMMARY",
                    "SERVICE DETAILS",
                ],
                source_file=Path("invoice.pdf"),
                context=context("Optus"),
                line_index_start=1,
            )

    def test_optus_pdf_reader_parse_and_dependency_failure(self) -> None:
        page_one = SimpleNamespace(
            extract_text=lambda: (
                "Customer account number 9124 3228 26\n"
                "Invoice number 24851489\n"
                "SERVICE SUMMARY\n"
                "Optus Evolve Ethernet WAN\n"
                "EVC00080698 6 1,250.00\n"
                "SERVICE DETAILS\n"
            )
        )
        page_two = SimpleNamespace(extract_text=lambda: None)
        fake_pypdf = SimpleNamespace(PdfReader=lambda _path: SimpleNamespace(pages=[page_one, page_two]))

        with mock.patch.dict(sys.modules, {"pypdf": fake_pypdf}):
            result = optus_pdf.parse([Path("invoice.pdf"), Path("ignored.txt")], context("Optus"))
        self.assertEqual(1, len(result["lines"]))
        self.assertEqual("Optus Evolve Ethernet WAN", result["lines"][0]["service_type"])

        with self.assertRaisesRegex(ValueError, "expects PDF"):
            optus_pdf.parse([Path("invoice.txt")], context("Optus"))

        original_import = builtins.__import__

        def reject_pypdf(name: str, *args: object, **kwargs: object) -> object:
            if name == "pypdf":
                raise ImportError("not installed")
            return original_import(name, *args, **kwargs)

        with mock.patch.object(builtins, "__import__", side_effect=reject_pypdf):
            with self.assertRaisesRegex(ValueError, "requires the pypdf package"):
                optus_pdf._read_pdf_lines(Path("invoice.pdf"))

    def test_optus_voice_dat_and_zip_variants_and_failures(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dat = root / "voice.dat"
            rows = [
                "1|HDR|MDX|",
                "2|ACCS|NOT_NUMERIC|||||||||||||||||||",
                "3|SRVS|A|INV-A|0|20230801|10.00||SERVICE-A|||"
                "|10.00||0.00|",
                "4|SRVS|A|INV-A|0|20230801|5.00||000123|||"
                "Voice service|0.00||5.00|",
                "5|SRVS|A|INV-A|0|20230801|0.00||SKIPPED|||"
                "Voice service|0.00||0.00|",
                "6|WUSG|A|INV-A|0|20230801|2.50|",
                "7|WUSG|A|INV-A|",
            ]
            dat.write_text("\n".join(rows), encoding="utf-8")
            result = optus_voice.parse([dat], context("Optus"))
            self.assertEqual(4, len(result["lines"]))
            self.assertEqual("", result["lines"][0]["billing_period_start"])
            self.assertEqual("SERVICE-A", result["lines"][0]["service_id_raw"])
            self.assertEqual("Optus Evolve Voice", result["lines"][0]["service_type"])
            self.assertEqual("123", result["lines"][1]["service_id_normalized"])
            self.assertEqual("", result["lines"][-1]["amount"])

            package = root / "voice.zip"
            write_zip(
                package,
                {
                    "voice.dat": (
                        "1|HDR|\n"
                        "2|ACCS|21887946000127||||||||||||||||20230701|20230731|\n"
                        "3|SRVS|A|943803681|0|20230801|30.00||0240323500|||"
                        "Voice service|20.00||10.00|\n"
                    )
                },
                directory="empty",
            )
            zipped = optus_voice.parse([package], context("Optus"))
            self.assertEqual(["Recurring", "Usage"], [line["charge_type"] for line in zipped["lines"]])
            self.assertEqual("2023-07-01", zipped["lines"][0]["billing_period_start"])
            self.assertEqual("2023-07-31", zipped["lines"][0]["billing_period_end"])

            with self.assertRaisesRegex(ValueError, "expects ZIP or extracted DAT"):
                optus_voice.parse([root / "voice.csv"], context("Optus"))
            with self.assertRaisesRegex(ValueError, "no account summary"):
                optus_voice._parse_pipe_rows(
                    [["HDR"]],
                    source_file=dat,
                    context=context("Optus"),
                    line_index_start=1,
                )
            with self.assertRaisesRegex(ValueError, "no SRVS/WUSG"):
                optus_voice._parse_pipe_rows(
                    [["HDR"], ["ACCS"]],
                    source_file=dat,
                    context=context("Optus"),
                    line_index_start=1,
                )

            zero_dat = root / "zero.dat"
            zero_dat.write_text(
                "1|HDR|\n"
                "2|ACCS|1||||||||||||||||20230701|20230731|\n"
                "3|SRVS|A|1|0|20230801|0.00||1|||Voice|0.00||0.00|\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "produced no billable rows"):
                optus_voice.parse([zero_dat], context("Optus"))

            with self.assertRaises(IndexError):
                optus_voice._parse_pipe_rows(
                    [["HDR"], ["ACCS"], ["WUSG", "", "", "", "", "", "1.00"]],
                    source_file=dat,
                    context=context("Optus"),
                    line_index_start=1,
                )

        self.assertEqual("", optus_voice._period_date(""))

    def test_csv_adapters_fail_closed_and_skip_header_only_files(self) -> None:
        cases = [
            (telstra, "Telstra", "expects CSV detail report"),
            (megaport, "Megaport", "expects CSV invoice"),
            (vocus, "Vocus", "expects CSV invoice"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for module, provider, message in cases:
                with self.subTest(provider=provider, case="wrong extension"):
                    with self.assertRaisesRegex(ValueError, message):
                        module.parse([root / "invoice.txt"], context(provider))
                header_only = root / f"{provider}.csv"
                header_only.write_text("Header\n", encoding="utf-8")
                with self.subTest(provider=provider, case="header only"):
                    self.assertEqual([], module.parse([header_only], context(provider))["lines"])

        self.assertEqual("Non-recurring", telstra._charge_type("Telstra other charges and credits"))
        self.assertEqual("fallback", telstra._charge_type("", "fallback"))

    def test_common_helpers_cover_invoice_contract_edges(self) -> None:
        malformed = io.StringIO("a,b\n1,2,extra\n")
        self.assertEqual([{"a": "1", "b": "2"}], common.csv_rows(malformed))

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rows.csv"
            path.write_text("\ufeffa,b\n 1 ,\n", encoding="utf-8")
            self.assertEqual([{"a": "1", "b": ""}], common.csv_rows(path))

        money_cases = {
            "": "",
            "$1,234.50": "1234.50",
            "(12.30)": "-12.30",
            "12.30 CR": "-12.30",
        }
        for raw, expected in money_cases.items():
            with self.subTest(money=raw):
                self.assertEqual(expected, common.parse_money(raw))
        self.assertEqual(Decimal("0.00"), common.decimal_amount(""))
        self.assertEqual(Decimal("1.25"), common.decimal_amount("1.25"))

        date_cases = {
            datetime(2026, 7, 23, 12, 30): "2026-07-23",
            date(2026, 7, 23): "2026-07-23",
            "": "",
            "23/07/2026": "2026-07-23",
            "23/07/26": "2026-07-23",
            "2026/07/23": "2026-07-23",
            "2026-07-23": "2026-07-23",
            "23 Jul 2026": "2026-07-23",
            "23 July 2026": "2026-07-23",
            "07/23/2026": "2026-07-23",
            "07/23/26": "2026-07-23",
            "unparsed": "unparsed",
        }
        for raw, expected in date_cases.items():
            with self.subTest(date=raw):
                self.assertEqual(expected, common.parse_date(raw))

        self.assertEqual(("", ""), common.period_from_date(""))
        self.assertEqual(("", ""), common.period_from_date("not-iso"))
        self.assertEqual(("2026-07-01", "2026-07-31"), common.period_from_date("2026-07-23"))
        self.assertEqual(
            ("2026-06-01", "2026-06-30"),
            common.period_from_date("2026-07-23", previous_month=True),
        )
        self.assertEqual(
            ("2025-12-01", "2025-12-31"),
            common.period_from_date("2026-01-23", previous_month=True),
        )

        self.assertEqual("", common.normalize_service_id(""))
        self.assertEqual("0", common.normalize_service_id("000"))
        self.assertEqual("ABC1", common.normalize_service_id(" ABC 1 "))
        self.assertEqual("", common.first_non_empty({}, "a", "b"))
        self.assertEqual("value", common.first_non_empty({"a": "", "b": " value "}, "a", "b"))

        with self.assertRaisesRegex(ValueError, "missing invoice_identity"):
            common.build_result([{"source_file": "invoice.csv"}])

        lines = [
            {
                "invoice_identity": "invoice-1",
                "source_file": "one.csv",
                "request_key": "request-1",
                "provider": "AAPT",
                "provider_account": "1",
                "invoice_number": "10",
                "custom": "x",
            },
            {
                "invoice_identity": "invoice-1",
                "source_file": "one.csv",
                "custom": "y",
            },
            {"invoice_identity": "invoice-1", "source_file": ""},
            {"invoice_identity": "invoice-1", "source_file": "two.csv"},
        ]
        result = common.build_result(lines)
        self.assertEqual(["one.csv", "two.csv"], result["invoice_headers"][0]["source_members"])
        self.assertIn("custom", result["headers"])

        with self.assertRaisesRegex(ValueError, "run_id is required"):
            common.make_line(
                context={"provider": "AAPT"},
                source_file=Path("invoice.csv"),
                source_row=1,
                provider_account="1",
                service_id="1",
                invoice_number="1",
                amount="1",
                line_index=1,
            )
        line = common.make_line(
            context=context("AAPT"),
            source_file=Path("invoice.csv"),
            source_row=1,
            provider_account="",
            service_id="",
            invoice_number="",
            amount="1",
            currency="",
            line_index=1,
            retained="yes",
            omitted=None,
        )
        self.assertEqual("AUD", line["currency"])
        self.assertEqual("yes", line["retained"])
        self.assertNotIn("omitted", line)

    def test_router_covers_all_provider_and_optus_package_decisions(self) -> None:
        expected = {
            "AAPT": "aapt",
            "Telstra": "telstra",
            "Vocus": "vocus",
            "Megaport": "megaport",
            "Equinix": "equinix",
        }
        for provider, parser_key in expected.items():
            with self.subTest(provider=provider):
                self.assertEqual(parser_key, parse_provider_invoice.select_parser(provider, []))

        with self.assertRaisesRegex(ValueError, "no source files"):
            parse_provider_invoice.select_parser("Optus", [])
        with self.assertRaisesRegex(ValueError, "Unsupported Optus"):
            parse_provider_invoice.select_parser("Optus", [Path("invoice.xlsx")])
        with self.assertRaisesRegex(ValueError, "Ambiguous Optus"):
            parse_provider_invoice.select_parser("Optus", [Path("invoice.pdf"), Path("voice.dat")])
        self.assertEqual("optus_pdf", parse_provider_invoice.select_parser("Optus", [Path("invoice.PDF")]))
        self.assertEqual("optus_excel_voice", parse_provider_invoice.select_parser("Optus", [Path("voice.ZIP")]))

        class ChangingSuffix:
            name = "unstable-source"

            def __init__(self) -> None:
                self.calls = 0

            @property
            def suffix(self) -> str:
                self.calls += 1
                return {1: ".txt", 2: ".txt", 3: ".pdf"}.get(self.calls, ".pdf")

        with self.assertRaisesRegex(ValueError, "no supported PDF"):
            parse_provider_invoice.select_parser("Optus", [ChangingSuffix()])  # type: ignore[list-item]

    def test_parser_cli_contract_validation_and_error_receipts(self) -> None:
        cases = [
            (
                "non-list-lines",
                {"lines": {}, "invoice_headers": [], "accounting": {}},
                ValueError("unused"),
                "parser_unavailable",
            ),
            (
                "missing-headers",
                {"lines": [{"line_id": "1"}], "invoice_headers": [], "accounting": {}},
                ValueError("unused"),
                "parser_unavailable",
            ),
            (
                "bad-accounting",
                {
                    "lines": [],
                    "invoice_headers": [],
                    "accounting": {"source_rows_considered": 1, "documented_exclusions": 0},
                },
                ValueError("unused"),
                "parser_unavailable",
            ),
            ("not-implemented", None, NotImplementedError("adapter disabled"), "parser_unavailable"),
            ("unexpected", None, RuntimeError("secret detail"), "parser_failed"),
        ]
        for label, result, raised, expected_code in cases:
            with self.subTest(case=label):
                code, warnings, manifest = self._run_cli_case(
                    result=result,
                    raised=raised if result is None else None,
                    with_manifest=True,
                )
                self.assertEqual(3, code)
                self.assertEqual(expected_code, warnings[0]["code"])
                self.assertEqual("parser_warning", manifest["status"])
                if expected_code == "parser_failed":
                    self.assertNotIn("secret detail", warnings[0]["message"])
                    self.assertIn("RuntimeError", warnings[0]["message"])

        code, warnings, manifest = self._run_cli_case(
            result={"lines": [], "invoice_headers": [], "accounting": {}},
            with_manifest=False,
        )
        self.assertEqual(0, code)
        self.assertEqual([], warnings)
        self.assertIsNone(manifest)

        code, warnings, manifest = self._run_cli_case(
            result=None,
            raised=ValueError("supplier file rejected"),
            with_manifest=False,
        )
        self.assertEqual(3, code)
        self.assertEqual("parser_unavailable", warnings[0]["code"])
        self.assertIsNone(manifest)

        code, warnings, manifest = self._run_cli_case(
            result=None,
            raised=RuntimeError("secret detail"),
            with_manifest=False,
        )
        self.assertEqual(3, code)
        self.assertEqual("parser_failed", warnings[0]["code"])
        self.assertIsNone(manifest)

    def _run_cli_case(
        self,
        *,
        result: dict | None,
        raised: Exception | None = None,
        with_manifest: bool,
    ) -> tuple[int, list[dict], dict | None]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "config.yaml"
            input_dir = root / "input"
            output_path = root / "output.json"
            warnings_path = root / "warnings.json"
            manifest_path = root / "manifest.json"
            config_path.write_text("features: {}\n", encoding="utf-8")
            input_dir.mkdir()
            (input_dir / "invoice.csv").write_text("invoice", encoding="utf-8")

            def parse(_source_files: list[Path], _context: dict) -> dict:
                if raised is not None:
                    raise raised
                assert result is not None
                return result

            argv = [
                "parse_provider_invoice",
                "--config",
                str(config_path),
                "--provider",
                "Telstra",
                "--input-dir",
                str(input_dir),
                "--output",
                str(output_path),
                "--warnings",
                str(warnings_path),
                "--run-id",
                RUN_IDS["Telstra"],
            ]
            if with_manifest:
                argv.extend(["--manifest", str(manifest_path)])

            with (
                mock.patch.object(sys, "argv", argv),
                mock.patch.object(
                    parse_provider_invoice.importlib,
                    "import_module",
                    return_value=SimpleNamespace(parse=parse),
                ),
            ):
                code = parse_provider_invoice.main()

            warnings = json.loads(warnings_path.read_text(encoding="utf-8"))
            manifest = (
                json.loads(manifest_path.read_text(encoding="utf-8"))
                if manifest_path.exists()
                else None
            )
            return code, warnings, manifest


if __name__ == "__main__":
    unittest.main()
