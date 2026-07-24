from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
import sys

sys.path.insert(0, str(SCRIPTS))

from recon_core.common import (  # noqa: E402
    APPROVED_REFINED_COLUMNS,
    RAW_WORKBOOK_COLUMNS,
    RUN_SUBDIRS,
    create_run_layout,
    generate_run_id,
    load_config,
    write_json,
)
from recon_core.run_state import create_state  # noqa: E402
from recon_core.validate_run import validate_run, workbook_rows  # noqa: E402
from recon_core.write_reports import write_reports  # noqa: E402


CONFIG = Path(__file__).resolve().parents[1] / "config" / "recon_settings.yaml"


def current_row(status: str = "Matched") -> dict:
    row = {column: "" for column in RAW_WORKBOOK_COLUMNS}
    row.update(
        {
            "line_id": "line-1",
            "run_id": "AAPT_20260709_153012_A1B2C",
            "AccountPayableReconRequestId": 1,
            "GenericSupplierInvoiceLineItemId": 2,
            "ServiceProviderInvoiceNumber": "INV-1",
            "InvoiceServiceNumber": "123",
            "ReconMatchStatus": status,
            "deterministic_match_rule": "deterministic_exact_candidate_v1",
            "deterministic_evidence_summary": "Matched on service_id, provider, billing_period.",
            "candidate_snapshot": {
                "customer_account": "CUST-1",
                "subscription_id": "SUB-1",
                "invoice_number": "CINV-1",
                "service_id": "123",
            },
        }
    )
    return row


class WorkbookAndValidationTests(unittest.TestCase):
    def test_raw_and_refined_xlsx_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = root / "raw.xlsx"
            refined = root / "refined.xlsx"
            manifest = root / "manifest.json"
            write_reports(
                raw_rows=[current_row()],
                refined_input_rows=[current_row()],
                raw_output=raw,
                refined_output=refined,
                manifest=manifest,
                config=load_config(CONFIG),
                run_path="/run",
                period="2026-07",
            )
            raw_header, raw_rows, raw_sheets = workbook_rows(raw)
            refined_header, refined_rows, refined_sheets = workbook_rows(refined)
            self.assertEqual(RAW_WORKBOOK_COLUMNS, raw_header)
            self.assertEqual(["Result", "Adjustment", "Do not change"], raw_sheets)
            self.assertFalse(any(column.startswith(("agent_", "human_")) for column in raw_header))
            self.assertEqual(RAW_WORKBOOK_COLUMNS + APPROVED_REFINED_COLUMNS, refined_header)
            self.assertEqual(["Result", "Adjustment", "Do not change"], refined_sheets)
            self.assertEqual("auto_matched", refined_rows[0]["agent_match_status"])
            self.assertEqual(1, len(raw_rows))

    def test_unresolved_refined_row_requires_investigator_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            row = current_row("Supplier Only")
            row.update(
                {
                    "agent_match_status": "no_match",
                    "agent_match_rule": "no_supported_candidate",
                    "agent_evidence_summary": "No supported candidate was found.",
                    "agent_review_required": True,
                }
            )
            write_reports(
                raw_rows=[current_row("Supplier Only")],
                refined_input_rows=[row],
                raw_output=root / "raw.xlsx",
                refined_output=root / "refined.xlsx",
                manifest=root / "manifest.json",
                config=load_config(CONFIG),
                run_path="/run",
                period="2026-07",
            )
            _, rows, _ = workbook_rows(root / "refined.xlsx")
            self.assertEqual("no_match", rows[0]["agent_match_status"])
            self.assertEqual("No supported candidate was found.", rows[0]["agent_evidence_summary"])

    def test_parser_validation_requires_audit_state_and_accounting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_id = generate_run_id(
                "AAPT",
                "source",
                datetime.fromisoformat("2026-07-09T15:30:12+10:00"),
            )
            run_root = Path(tmp) / "AAPT" / "2026" / "07" / run_id
            create_run_layout(run_root)
            write_json(
                run_root / "manifest" / "run_manifest.json",
                {"run_id": run_id, "db_update_enabled": False},
            )
            write_json(
                run_root / "normalized" / "provider_lines.json",
                {
                    "invoice_headers": [{"invoice_identity": "invoice-1"}],
                    "lines": [
                        {
                            "line_id": "line-1",
                            "invoice_identity": "invoice-1",
                            "run_id": run_id,
                            "provider": "AAPT",
                        }
                    ],
                },
            )
            write_json(run_root / "logs" / "parser_warnings.json", [])
            write_json(
                run_root / "manifest" / "parser_manifest.json",
                {
                    "run_id": run_id,
                    "provider": "AAPT",
                    "accounting_complete": True,
                    "source_rows": 1,
                    "parsed_rows": 1,
                    "documented_exclusions": 0,
                },
            )
            write_json(
                run_root / "manifest" / "audit_manifest.json",
                {
                    "run_id": run_id,
                    "accepted_resolution_update_attempted": False,
                },
            )
            create_state(
                run_root / "manifest" / "run_state.json",
                run_id=run_id,
                provider="AAPT",
                run_mode="parser_validation",
                source_identity="source",
            )
            result = validate_run(run_root, load_config(CONFIG), "parser_validation")
            self.assertEqual(1, result["parsed_rows"])
            self.assertEqual("passed", result["validation"])


if __name__ == "__main__":
    unittest.main()
