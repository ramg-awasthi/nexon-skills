from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from recon_core.match_recon import classify_line  # noqa: E402


class MatchEngineTests(unittest.TestCase):
    def test_no_candidate_requires_review(self) -> None:
        row = classify_line({"line_id": "1"}, [])
        self.assertEqual("no_match", row["agent_match_status"])
        self.assertEqual("no_candidate", row["agent_match_rule"])
        self.assertIn("No billing candidate", row["agent_evidence_summary"])
        self.assertTrue(row["agent_review_required"])

    def test_exact_single_candidate_auto_matches(self) -> None:
        row = classify_line(
            {"line_id": "1"},
            [
                {
                    "customer_account": "A",
                    "subscription_id": "SUB-1",
                    "invoice_number": "INV-1",
                    "service_id": "SVC-1",
                    "service_id_match": True,
                    "provider_match": True,
                    "billing_period_match": True,
                }
            ],
        )
        self.assertEqual("auto_matched", row["agent_match_status"])
        self.assertEqual("deterministic_exact_candidate_v1", row["agent_match_rule"])
        self.assertEqual("Matched on service_id, provider, billing_period.", row["agent_evidence_summary"])
        self.assertEqual("A", row["agent_suggested_customer_account"])
        self.assertEqual("SUB-1", row["agent_suggested_subscription_id"])
        self.assertEqual("INV-1", row["agent_suggested_invoice_number"])
        self.assertEqual("SVC-1", row["agent_suggested_service_id"])
        self.assertFalse(row["agent_review_required"])

    def test_exact_single_candidate_can_blank_auto_match_evidence(self) -> None:
        row = classify_line(
            {"line_id": "1"},
            [
                {
                    "service_id_match": True,
                    "provider_match": True,
                    "billing_period_match": True,
                }
            ],
            auto_matched_evidence="blank",
        )
        self.assertEqual("auto_matched", row["agent_match_status"])
        self.assertEqual("", row["agent_evidence_summary"])

    def test_evidence_summary_is_shortened(self) -> None:
        row = classify_line({"line_id": "1"}, [], evidence_max_chars=40)
        self.assertLessEqual(len(row["agent_evidence_summary"]), 40)
        self.assertTrue(row["agent_evidence_summary"].endswith("..."))

    def test_weak_single_candidate_requires_review(self) -> None:
        row = classify_line({"line_id": "1"}, [{"customer": "A"}])
        self.assertEqual("needs_review", row["agent_match_status"])
        self.assertEqual("candidate_evidence_incomplete", row["agent_match_rule"])
        self.assertIn("incomplete", row["agent_evidence_summary"])
        self.assertTrue(row["agent_review_required"])

    def test_multiple_candidates_require_review(self) -> None:
        row = classify_line({"line_id": "1"}, [{"customer": "A"}, {"customer": "B"}])
        self.assertEqual("multi_match", row["agent_match_status"])
        self.assertEqual("multiple_candidates", row["agent_match_rule"])
        self.assertIn("2 billing candidates", row["agent_evidence_summary"])
        self.assertTrue(row["agent_review_required"])


if __name__ == "__main__":
    unittest.main()
