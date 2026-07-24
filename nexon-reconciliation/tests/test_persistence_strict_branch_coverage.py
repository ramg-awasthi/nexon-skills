from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
import types
import unittest
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
TESTS = SKILL_ROOT / "tests"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(TESTS))

from recon_core import core_persistence, sqlserver_persistence  # noqa: E402
from test_sqlserver_persistence import (  # noqa: E402
    FakeSqlServer,
    sample_payloads,
)


class PersistenceStrictBranchCoverageTests(unittest.TestCase):
    run_id = "AAPT_20260709_153012_A1B2C"
    run_path = f"/runs/{run_id}"

    def persist(
        self,
        database: FakeSqlServer,
        normalized: dict,
        candidates: dict,
        matches: dict,
    ) -> tuple[dict, dict]:
        with patch.object(
            sqlserver_persistence, "_connect", side_effect=lambda *_: database.connect()
        ):
            return sqlserver_persistence.persist_sqlserver_run(
                dsn="Driver=fake",
                normalized=normalized,
                candidates=candidates,
                matches=matches,
                provider_account_id=7,
                run_path=self.run_path,
            )

    def assert_prepare_error(
        self,
        normalized: dict,
        candidates: dict,
        matches: dict,
        message: str,
        *,
        run_path: str | None = None,
    ) -> None:
        with self.assertRaisesRegex(RuntimeError, message):
            sqlserver_persistence._prepare(
                normalized=normalized,
                candidates=candidates,
                matches=matches,
                provider_account_id=7,
                run_path=run_path or self.run_path,
            )

    def test_low_level_value_date_insert_and_driver_paths(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "field is required"):
            sqlserver_persistence._required_text(None, "field")

        self.assertEqual("", sqlserver_persistence._normalized_value(None))
        self.assertEqual(
            "2026-07-09",
            sqlserver_persistence._normalized_value(datetime(2026, 7, 9)),
        )
        self.assertEqual(
            "2026-07-09 01:02:03",
            sqlserver_persistence._normalized_value(datetime(2026, 7, 9, 1, 2, 3)),
        )
        self.assertEqual(
            "2026-07-09", sqlserver_persistence._normalized_value(date(2026, 7, 9))
        )
        self.assertEqual("12.34", sqlserver_persistence._normalized_value(Decimal("12.340")))
        self.assertEqual("1.5", sqlserver_persistence._normalized_value(1.5))
        self.assertEqual("True", sqlserver_persistence._normalized_value(True))
        self.assertEqual("+", sqlserver_persistence._normalized_value("+"))
        self.assertEqual("plain", sqlserver_persistence._normalized_value("plain"))

        with self.assertRaisesRegex(RuntimeError, "must be an ISO date"):
            sqlserver_persistence._date_value("not-a-date", "billing_date")

        cursor = MagicMock()
        cursor.fetchone.return_value = None
        with self.assertRaisesRegex(RuntimeError, "INSERT returned no identity"):
            sqlserver_persistence._insert_id(cursor, "INSERT", ())

        with patch.dict(sys.modules, {"pyodbc": None}):
            with self.assertRaisesRegex(RuntimeError, "pyodbc is required"):
                sqlserver_persistence._connect("dsn", 11)

        connect = MagicMock(return_value=object())
        module = types.SimpleNamespace(connect=connect)
        with patch.dict(sys.modules, {"pyodbc": module}):
            connection = sqlserver_persistence._connect("dsn", 11)
        self.assertIs(connection, connect.return_value)
        connect.assert_called_once_with(
            "dsn", readonly=False, autocommit=False, timeout=11
        )

    def test_prepare_rejects_every_malformed_payload_shape(self) -> None:
        normalized, candidates, matches = sample_payloads()

        malformed_shapes = (
            ({"invoice_headers": {}, "lines": normalized["lines"]}, matches),
            (
                {
                    "invoice_headers": normalized["invoice_headers"],
                    "lines": {},
                },
                matches,
            ),
            (
                normalized,
                {"rows": {}},
            ),
            (
                {"invoice_headers": [], "lines": normalized["lines"]},
                matches,
            ),
            (
                {
                    "invoice_headers": normalized["invoice_headers"],
                    "lines": [],
                },
                matches,
            ),
        )
        for malformed_normalized, malformed_matches in malformed_shapes:
            with self.subTest(
                normalized=type(malformed_normalized.get("lines")).__name__,
                matches=type(malformed_matches.get("rows")).__name__,
            ):
                self.assert_prepare_error(
                    malformed_normalized,
                    candidates,
                    malformed_matches,
                    "payloads are malformed",
                )

        identity_mutations = (
            (
                lambda payload: payload["lines"].append(
                    {
                        **payload["lines"][0],
                        "line_id": "line-2",
                        "run_id": "OTHER_RUN",
                    }
                ),
                "one run must map",
            ),
            (
                lambda payload: payload["lines"].append(
                    {
                        **payload["lines"][0],
                        "line_id": "line-2",
                        "provider": "Optus",
                    }
                ),
                "one run must map",
            ),
            (
                lambda payload: payload["invoice_headers"].append(
                    {
                        **payload["invoice_headers"][0],
                        "request_key": f"{self.run_id}:invoice-2",
                        "invoice_identity": "AAPT:INV-2",
                    }
                ),
                "one run must map",
            ),
            (
                lambda payload: payload["invoice_headers"].append(
                    {
                        **payload["invoice_headers"][0],
                        "invoice_identity": "AAPT:INV-2",
                        "billing_period_start": "2026-05-01",
                    }
                ),
                "one run must map",
            ),
        )
        for mutate, message in identity_mutations:
            changed = copy.deepcopy(normalized)
            mutate(changed)
            with self.subTest(mutation=message, payload=changed):
                self.assert_prepare_error(changed, candidates, matches, message)

        changed_candidates = copy.deepcopy(candidates)
        changed_candidates["run_id"] = "OTHER_RUN"
        self.assert_prepare_error(
            normalized, changed_candidates, matches, "candidate evidence identity"
        )
        changed_candidates = copy.deepcopy(candidates)
        changed_candidates["provider"] = "Optus"
        self.assert_prepare_error(
            normalized, changed_candidates, matches, "candidate evidence identity"
        )

        changed_matches = copy.deepcopy(matches)
        changed_matches["rows"][0]["run_id"] = "OTHER_RUN"
        self.assert_prepare_error(
            normalized, candidates, changed_matches, "match rows are not bound"
        )
        changed_matches = copy.deepcopy(matches)
        changed_matches["rows"][0]["provider"] = "Optus"
        self.assert_prepare_error(
            normalized, candidates, changed_matches, "match rows are not bound"
        )

        self.assert_prepare_error(
            normalized,
            candidates,
            matches,
            "run_path does not match",
            run_path="/runs/OTHER_RUN",
        )

        missing_result = copy.deepcopy(matches)
        missing_result["rows"] = []
        self.assert_prepare_error(
            normalized, candidates, missing_result, "every parser line"
        )

        missing_header = copy.deepcopy(normalized)
        missing_header["lines"][0]["invoice_identity"] = "AAPT:UNKNOWN"
        self.assert_prepare_error(
            missing_header, candidates, matches, "supplier line has no invoice header"
        )

        bad_candidate_map = copy.deepcopy(candidates)
        bad_candidate_map["candidates_by_line"] = []
        self.assert_prepare_error(
            normalized, bad_candidate_map, matches, "must be a mapping"
        )
        bad_candidate_rows = copy.deepcopy(candidates)
        bad_candidate_rows["candidates_by_line"]["line-1"] = {}
        self.assert_prepare_error(
            normalized, bad_candidate_rows, matches, "candidate rows must be a list"
        )

        missing_required = copy.deepcopy(normalized)
        missing_required["lines"][0]["run_id"] = ""
        self.assert_prepare_error(
            missing_required, candidates, matches, "run_id is required"
        )

    def test_count_conflicts_all_roll_back_and_close(self) -> None:
        cases = (
            (
                "multiple requests",
                lambda database: database.insert(
                    "AccountPayableReconRequest",
                    {
                        "ServiceProviderAccountId": 7,
                        "PeriodStartDate": "2026-06-01",
                        "PeriodEndDate": "2026-06-30",
                        "OneDrivePath": self.run_path,
                    },
                ),
            ),
            (
                "invoice count differs",
                lambda database: database.insert(
                    "GenericSupplierInvoice",
                    {
                        "AccountPayableReconRequestId": 1,
                        "ServiceProviderAccountId": 7,
                        "ServiceProviderInvoiceNumber": "EXTRA",
                    },
                ),
            ),
            (
                "supplier line count differs",
                lambda database: database.insert(
                    "GenericSupplierInvoiceLineItem",
                    {
                        "GenericSupplierInvoiceId": 1,
                        "ServiceNumber": "EXTRA",
                    },
                ),
            ),
            (
                "billing candidate count differs",
                lambda database: database.insert(
                    "GenericNexonBilling",
                    {
                        "AccountPayableReconRequestId": 1,
                        "InvoiceNumber": "EXTRA",
                    },
                ),
            ),
            (
                "result count differs",
                lambda database: database.insert(
                    "AccountPayableReconResult",
                    {
                        "AccountPayableReconRequestId": 1,
                        "GenericSupplierInvoiceLineItemId": 1,
                        "GenericNexonBillingId": None,
                        "ReconMatchStatusId": 3,
                    },
                ),
            ),
        )
        for message, mutate in cases:
            with self.subTest(message=message):
                normalized, candidates, matches = sample_payloads()
                database = FakeSqlServer()
                self.persist(database, normalized, candidates, matches)
                mutate(database)
                with self.assertRaisesRegex(RuntimeError, message):
                    self.persist(database, normalized, candidates, matches)
                failed = database.connections[-1]
                self.assertEqual(1, failed.rollbacks)
                self.assertEqual(0, failed.commits)
                self.assertTrue(failed.closed)

    def test_optional_fields_fallbacks_and_multiple_result_cardinality(self) -> None:
        normalized, candidates, matches = sample_payloads()
        line = normalized["lines"][0]
        line.update(
            {
                "service_id_normalized": "",
                "service_id_raw": "RAW-SVC",
                "charge_type": "Recurring",
                "detail_description": "Optional detail",
                "nexon_customer_reference": "NEXON-1",
                "billing_period_start": "",
                "billing_period_end": "",
                "supplier_amount": "",
                "amount": "45.67",
                "infrastructure_cost": "1.25",
                "service_type": "Data",
                "service_location_center": "SYD",
            }
        )
        candidate = candidates["candidates_by_line"]["line-1"][0]
        candidate.clear()
        candidate.update(
            {
                "candidate_id": "fallback-candidate",
                "customer_invoice_number": "CUST-INV-FALLBACK",
                "customer_account": "Customer fallback",
                "service_number": "RAW-SVC",
                "service_description": "Service detail",
                "transaction_date": "2026-07-01",
                "customer_invoice_amount": "45.67",
                "billing_system_id": 9,
                "financial_quarter": "CUSTOM-Q",
                "financial_year": "CUSTOM-FY",
                "financial_month": 12,
                "emerald_carrier_detail": "Carrier",
                "recurring_amount_excl_gst": "40",
                "usage_amount_excl_gst": "5.67",
                "service_spec_name": "Spec",
                "charge_type": "Recurring",
            }
        )
        matches["rows"][0]["candidate_snapshot"] = {
            "candidate_id": "fallback-candidate",
            "service_login": "login",
            "service_last_invoice_date": "2026-06-01",
            "service_last_invoice_number": "LAST-1",
            "service_last_invoice_amount": "44.00",
        }
        matches["rows"].append(
            {
                "line_id": "line-1",
                "run_id": self.run_id,
                "provider": "AAPT",
                "ReconMatchStatus": "Supplier Only",
                "candidate_snapshot": "not-a-mapping",
            }
        )
        database = FakeSqlServer()

        persisted, manifest = self.persist(database, normalized, candidates, matches)

        supplier = database.tables["GenericSupplierInvoiceLineItem"][0]
        self.assertEqual("RAW-SVC", supplier["ServiceNumber"])
        self.assertEqual("45.67", supplier["AmountExclGST"])
        self.assertIsNone(supplier["InvoiceStartDate"])
        self.assertIsNone(supplier["InvoiceEndDate"])
        billing = database.tables["GenericNexonBilling"][0]
        self.assertEqual("CUST-INV-FALLBACK", billing["InvoiceNumber"])
        self.assertEqual("Customer fallback", billing["CustomerName"])
        self.assertEqual("Customer fallback", billing["AccountNumber"])
        self.assertEqual("CUSTOM-Q", billing["FinancialQuarter"])
        self.assertEqual("CUSTOM-FY", billing["FinancialYear"])
        self.assertEqual(12, billing["FinancialMonth"])
        self.assertEqual(2, manifest["result_count"])
        self.assertEqual(2, len(persisted["rows"]))
        self.assertEqual(1, persisted["rows"][0]["GenericNexonBillingId"])
        self.assertEqual("", persisted["rows"][1]["GenericNexonBillingId"])

        rerun, rerun_manifest = self.persist(
            database, normalized, candidates, matches
        )
        self.assertEqual(2, len(rerun["rows"]))
        self.assertEqual("validated_existing", rerun_manifest["write_disposition"])

    def test_candidate_ids_are_required_deduplicated_and_conflict_checked(self) -> None:
        normalized, candidates, matches = sample_payloads()
        missing_id = copy.deepcopy(candidates)
        missing_id["candidates_by_line"]["line-1"][0]["candidate_id"] = ""
        self.assert_prepare_error(
            normalized,
            missing_id,
            matches,
            "candidate.candidate_id is required",
        )

        second_line = {
            **normalized["lines"][0],
            "line_id": "line-2",
        }
        normalized["lines"].append(second_line)
        matches["rows"].append(
            {
                **matches["rows"][0],
                "line_id": "line-2",
            }
        )
        duplicate = copy.deepcopy(candidates["candidates_by_line"]["line-1"][0])
        candidates["candidates_by_line"]["line-2"] = [duplicate]

        prepared = sqlserver_persistence._prepare(
            normalized=normalized,
            candidates=candidates,
            matches=matches,
            provider_account_id=7,
            run_path=self.run_path,
        )
        self.assertEqual(1, len(prepared["candidate_rows"]))

        conflicting = copy.deepcopy(candidates)
        conflicting["candidates_by_line"]["line-2"][0]["amount_excl_gst"] = "99.99"
        self.assert_prepare_error(
            normalized,
            conflicting,
            matches,
            "duplicate candidate_id has conflicting persisted values",
        )

    def test_defensive_missing_id_branch_requires_bypassing_prepare_invariant(self) -> None:
        normalized, candidates, matches = sample_payloads()
        prepared = sqlserver_persistence._prepare(
            normalized=normalized,
            candidates=candidates,
            matches=matches,
            provider_account_id=7,
            run_path=self.run_path,
        )
        prepared["candidate_rows"][0][1].pop("candidate_id")
        database = FakeSqlServer()

        with (
            patch.object(sqlserver_persistence, "_prepare", return_value=prepared),
            patch.object(
                sqlserver_persistence,
                "_connect",
                side_effect=lambda *_: database.connect(),
            ),
        ):
            persisted, manifest = sqlserver_persistence.persist_sqlserver_run(
                dsn="Driver=fake",
                normalized=normalized,
                candidates=candidates,
                matches=matches,
                provider_account_id=7,
                run_path=self.run_path,
            )

        self.assertEqual(1, manifest["billing_candidate_count"])
        self.assertEqual("", persisted["rows"][0]["GenericNexonBillingId"])

    def test_invalid_status_rolls_back_after_connect(self) -> None:
        normalized, candidates, matches = sample_payloads()
        matches["rows"][0]["ReconMatchStatus"] = "Unknown"
        database = FakeSqlServer()

        with self.assertRaisesRegex(RuntimeError, "unsupported ReconMatchStatus"):
            self.persist(database, normalized, candidates, matches)

        failed = database.connections[-1]
        self.assertEqual(1, failed.rollbacks)
        self.assertTrue(failed.closed)

    def test_core_cli_shadow_route_and_guard_paths(self) -> None:
        normalized, candidates, matches = sample_payloads()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "config.yaml"
            normalized_path = root / "normalized.json"
            candidates_path = root / "candidates.json"
            matches_path = root / "matches.json"
            output = root / "output.json"
            manifest = root / "manifest.json"
            normalized_path.write_text(json.dumps(normalized), encoding="utf-8")
            candidates_path.write_text(json.dumps(candidates), encoding="utf-8")
            matches_path.write_text(json.dumps(matches), encoding="utf-8")
            argv = [
                "core_persistence",
                "--config",
                str(config),
                "--normalized",
                str(normalized_path),
                "--candidates",
                str(candidates_path),
                "--matches",
                str(matches_path),
                "--output",
                str(output),
                "--manifest",
                str(manifest),
                "--provider-account-id",
                "7",
                "--run-path",
                self.run_path,
            ]

            config.write_text(
                "features:\n  core_persistence_enabled: false\n", encoding="utf-8"
            )
            with patch.object(sys, "argv", argv):
                with self.assertRaisesRegex(RuntimeError, "feature is disabled"):
                    core_persistence.main()

            config.write_text(
                "features:\n  core_persistence_enabled: true\n", encoding="utf-8"
            )
            with (
                patch.object(sys, "argv", argv),
                patch.dict(os.environ, {}, clear=True),
            ):
                with self.assertRaisesRegex(RuntimeError, "DSN is missing"):
                    core_persistence.main()

            with (
                patch.object(sys, "argv", argv),
                patch.dict(
                    os.environ,
                    {
                        "NEXON_RECON_CORE_MODE": "unsupported",
                        "NEXON_RECON_CORE_DSN": "unused",
                    },
                    clear=True,
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "must be sqlite_shadow"):
                    core_persistence.main()

            adapter_result = (
                {"rows": [{"line_id": "line-1"}]},
                {"mode": "sqlite_shadow", "transaction": "committed"},
            )
            with (
                patch.object(sys, "argv", argv),
                patch.dict(
                    os.environ,
                    {
                        "NEXON_RECON_CORE_MODE": "sqlite_shadow",
                        "NEXON_RECON_CORE_DSN": str(root / "shadow.sqlite"),
                    },
                    clear=True,
                ),
                patch.object(
                    core_persistence,
                    "persist_shadow_run",
                    return_value=adapter_result,
                ) as persist,
            ):
                self.assertEqual(0, core_persistence.main())
            persist.assert_called_once()
            self.assertEqual(adapter_result[0], json.loads(output.read_text()))
            self.assertEqual(adapter_result[1], json.loads(manifest.read_text()))


if __name__ == "__main__":
    unittest.main()
