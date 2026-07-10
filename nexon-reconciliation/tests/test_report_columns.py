from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from recon_core.common import APPROVED_REFINED_COLUMNS, EXCLUDED_PHASE1_COLUMNS  # noqa: E402
from recon_core.write_reports import BASE_COLUMNS  # noqa: E402


class ReportColumnTests(unittest.TestCase):
    def test_refined_columns_are_approved(self) -> None:
        expected = [
            "agent_match_status",
            "agent_match_rule",
            "agent_suggested_customer_account",
            "agent_suggested_subscription_id",
            "agent_suggested_invoice_number",
            "agent_suggested_service_id",
            "agent_evidence_summary",
            "agent_review_required",
            "human_verified_status",
            "human_verified_by",
            "human_verified_at",
            "human_verified_invoice_number",
        ]
        self.assertEqual(expected, APPROVED_REFINED_COLUMNS)

    def test_excluded_columns_do_not_leak(self) -> None:
        refined_columns = BASE_COLUMNS + APPROVED_REFINED_COLUMNS
        self.assertFalse(EXCLUDED_PHASE1_COLUMNS.intersection(refined_columns))


if __name__ == "__main__":
    unittest.main()
