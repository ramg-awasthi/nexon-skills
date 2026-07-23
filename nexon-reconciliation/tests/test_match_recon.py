from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from recon_core.match_recon import classify_line  # noqa: E402


def invoice_line() -> dict:
    return {
        "line_id": "line-1",
        "invoice_identity": "invoice-1",
        "request_key": "run:invoice-1",
        "run_id": "AAPT_20260709_153012_A1B2C",
        "provider": "AAPT",
        "provider_account": "ACC-1",
        "invoice_number": "INV-1",
        "service_id_raw": "00123",
        "service_id_normalized": "123",
        "supplier_amount": "10.00",
        "detail_description": "Service",
    }


class MatchEngineTests(unittest.TestCase):
    def test_no_candidate_is_supplier_only_without_agent_fields(self) -> None:
        row = classify_line(invoice_line(), [])
        self.assertEqual("Supplier Only", row["ReconMatchStatus"])
        self.assertEqual("supplier_without_billing_candidate_v1", row["deterministic_match_rule"])
        self.assertIn("No billing candidate", row["deterministic_evidence_summary"])
        self.assertNotIn("agent_match_status", row)
        self.assertEqual("123", row["InvoiceServiceNumber"])

    def test_exact_single_candidate_is_current_matched_status(self) -> None:
        row = classify_line(
            invoice_line(),
            [
                {
                    "candidate_id": "billing-1",
                    "customer_account": "CUST-1",
                    "service_id": "123",
                    "service_id_match": True,
                    "provider_match": True,
                    "billing_period_match": True,
                }
            ],
        )
        self.assertEqual("Matched", row["ReconMatchStatus"])
        self.assertEqual("deterministic_exact_candidate_v1", row["deterministic_match_rule"])
        self.assertEqual("billing-1", row["GenericNexonBillingId"])
        self.assertEqual("CUST-1", row["BillingCustomerName"])
        self.assertNotIn("agent_suggested_customer_account", row)

    def test_weak_single_candidate_is_not_matched(self) -> None:
        row = classify_line(invoice_line(), [{"candidate_id": "billing-1", "customer": "A"}])
        self.assertEqual("Not Matched", row["ReconMatchStatus"])
        self.assertEqual("single_candidate_evidence_incomplete_v1", row["deterministic_match_rule"])

    def test_multiple_candidates_are_not_matched(self) -> None:
        row = classify_line(invoice_line(), [{"candidate_id": "1"}, {"candidate_id": "2"}])
        self.assertEqual("Not Matched", row["ReconMatchStatus"])
        self.assertEqual("multiple_candidates_v1", row["deterministic_match_rule"])
        self.assertEqual(2, row["deterministic_candidate_count"])


if __name__ == "__main__":
    unittest.main()
