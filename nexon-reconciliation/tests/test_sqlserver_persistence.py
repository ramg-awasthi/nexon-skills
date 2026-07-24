from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from recon_core import core_persistence, preflight_check, sqlserver_persistence  # noqa: E402


def sample_payloads() -> tuple[dict, dict, dict]:
    run_id = "AAPT_20260709_153012_A1B2C"
    header = {
        "request_key": f"{run_id}:invoice-1",
        "invoice_identity": "AAPT:INV-1",
        "invoice_number": "INV-1",
        "billing_period_start": "2026-06-01",
        "billing_period_end": "2026-06-30",
    }
    line = {
        "line_id": "line-1",
        "run_id": run_id,
        "provider": "AAPT",
        "request_key": header["request_key"],
        "invoice_identity": header["invoice_identity"],
        "service_id_normalized": "SVC-1",
        "billing_period_start": "2026-06-01",
        "billing_period_end": "2026-06-30",
        "supplier_amount": "12.34",
    }
    candidate = {
        "candidate_id": "candidate-1",
        "invoice_number": "CUST-INV-1",
        "customer_name": "Customer",
        "account_number": "ACC-1",
        "service_id": "SVC-1",
        "billing_date": "2026-06-30",
        "amount_excl_gst": "12.34",
    }
    match = {
        "line_id": "line-1",
        "run_id": run_id,
        "provider": "AAPT",
        "ReconMatchStatus": "Matched",
        "candidate_snapshot": candidate,
    }
    return (
        {"invoice_headers": [header], "lines": [line]},
        {
            "run_id": run_id,
            "provider": "AAPT",
            "candidates_by_line": {"line-1": [candidate]},
        },
        {"rows": [match]},
    )


class FakeSqlServer:
    def __init__(self) -> None:
        self.tables: dict[str, list[dict]] = {
            "AccountPayableReconRequest": [],
            "GenericSupplierInvoice": [],
            "GenericSupplierInvoiceLineItem": [],
            "GenericNexonBilling": [],
            "AccountPayableReconResult": [],
        }
        self.next_id = {table: 1 for table in self.tables}
        self.connections: list[FakeConnection] = []

    def connect(self) -> "FakeConnection":
        connection = FakeConnection(self)
        self.connections.append(connection)
        return connection

    def insert(self, table: str, row: dict) -> int:
        identity = self.next_id[table]
        self.next_id[table] += 1
        self.tables[table].append({"Id": identity, **row})
        return identity


class FakeConnection:
    def __init__(self, database: FakeSqlServer) -> None:
        self.database = database
        self.commits = 0
        self.rollbacks = 0
        self.closed = False
        self._cursor = FakeCursor(database)

    def cursor(self) -> "FakeCursor":
        return self._cursor

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closed = True


class FakeCursor:
    def __init__(self, database: FakeSqlServer) -> None:
        self.database = database
        self.timeout = 0
        self.description: list[tuple[str]] = []
        self._rows: list[tuple] = []
        self._inserted_id: int | None = None

    @staticmethod
    def _compact(sql: str) -> str:
        return " ".join(sql.split())

    def _select(self, rows: list[dict]) -> None:
        columns = list(rows[0]) if rows else ["Id"]
        self.description = [(column,) for column in columns]
        self._rows = [tuple(row.get(column) for column in columns) for row in rows]

    def execute(self, sql: str, values: tuple = ()) -> "FakeCursor":
        statement = self._compact(sql)
        self._rows = []
        self._inserted_id = None
        if statement.startswith(("SET ", "BEGIN TRANSACTION")):
            return self

        if "FROM [Finance].[AccountPayableReconRequest]" in statement:
            run_path = values[0]
            self._select(
                [
                    row
                    for row in self.database.tables["AccountPayableReconRequest"]
                    if row["OneDrivePath"] == run_path
                ]
            )
        elif statement.startswith("INSERT INTO [Finance].[AccountPayableReconRequest]"):
            self._inserted_id = self.database.insert(
                "AccountPayableReconRequest",
                {
                    "ServiceProviderAccountId": values[0],
                    "PeriodStartDate": values[1],
                    "PeriodEndDate": values[2],
                    "OneDrivePath": values[3],
                },
            )
        elif "FROM [Finance].[GenericSupplierInvoice]" in statement:
            request_id = values[0]
            self._select(
                [
                    row
                    for row in self.database.tables["GenericSupplierInvoice"]
                    if row["AccountPayableReconRequestId"] == request_id
                ]
            )
        elif statement.startswith("INSERT INTO [Finance].[GenericSupplierInvoice]"):
            self._inserted_id = self.database.insert(
                "GenericSupplierInvoice",
                {
                    "AccountPayableReconRequestId": values[0],
                    "ServiceProviderAccountId": values[1],
                    "ServiceProviderInvoiceNumber": values[2],
                },
            )
        elif "FROM [Finance].[GenericSupplierInvoiceLineItem]" in statement:
            request_id = values[0]
            invoice_ids = {
                row["Id"]
                for row in self.database.tables["GenericSupplierInvoice"]
                if row["AccountPayableReconRequestId"] == request_id
            }
            self._select(
                [
                    row
                    for row in self.database.tables["GenericSupplierInvoiceLineItem"]
                    if row["GenericSupplierInvoiceId"] in invoice_ids
                ]
            )
        elif statement.startswith(
            "INSERT INTO [Finance].[GenericSupplierInvoiceLineItem]"
        ):
            columns = (
                "GenericSupplierInvoiceId",
                "ServiceNumber",
                "ChargeType",
                "DetailDescription",
                "NexonCustomerReference",
                "InvoiceStartDate",
                "InvoiceEndDate",
                "AmountExclGST",
                "InfrastructureCost",
                "ServiceType",
                "ServiceLocationCenter",
            )
            self._inserted_id = self.database.insert(
                "GenericSupplierInvoiceLineItem", dict(zip(columns, values, strict=True))
            )
        elif "FROM [Finance].[GenericNexonBilling]" in statement:
            request_id = values[0]
            self._select(
                [
                    row
                    for row in self.database.tables["GenericNexonBilling"]
                    if row["AccountPayableReconRequestId"] == request_id
                ]
            )
        elif statement.startswith("INSERT INTO [Finance].[GenericNexonBilling]"):
            columns = (
                "AccountPayableReconRequestId",
                "BillingSystemId",
                "InvoiceNumber",
                "CustomerName",
                "AccountNumber",
                "ServiceNumber",
                "ServiceDescription",
                "BillingDate",
                "FinancialQuarter",
                "FinancialYear",
                "FinancialMonth",
                "AmountExclGST",
                "EmeraldCarrierDetail",
                "RecurringAmountExclGST",
                "UsageAmountExclGST",
                "ServiceSpecName",
                "ChargeType",
            )
            self._inserted_id = self.database.insert(
                "GenericNexonBilling", dict(zip(columns, values, strict=True))
            )
        elif "FROM [Finance].[AccountPayableReconResult]" in statement:
            request_id = values[0]
            self._select(
                [
                    row
                    for row in self.database.tables["AccountPayableReconResult"]
                    if row["AccountPayableReconRequestId"] == request_id
                ]
            )
        elif statement.startswith("INSERT INTO [Finance].[AccountPayableReconResult]"):
            columns = (
                "AccountPayableReconRequestId",
                "GenericSupplierInvoiceLineItemId",
                "GenericNexonBillingId",
                "ReconMatchStatusId",
                "serviceLogin",
                "serviceLastInvoiceDate",
                "serviceLastInvoiceNumber",
                "serviceLastInvoiceAmount",
            )
            self._inserted_id = self.database.insert(
                "AccountPayableReconResult", dict(zip(columns, values, strict=True))
            )
        else:
            raise AssertionError(f"Unexpected SQL in test fake: {statement}")
        return self

    def fetchall(self) -> list[tuple]:
        return self._rows

    def fetchone(self) -> tuple[int] | None:
        if self._inserted_id is None:
            return None
        return (self._inserted_id,)


class SqlServerPersistenceTests(unittest.TestCase):
    run_path = "/runs/AAPT_20260709_153012_A1B2C"

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

    def test_insert_commits_complete_run_and_derives_financial_period(self) -> None:
        normalized, candidates, matches = sample_payloads()
        database = FakeSqlServer()

        persisted, manifest = self.persist(
            database, normalized, candidates, matches
        )

        self.assertEqual(
            {
                "AccountPayableReconRequest": 1,
                "GenericSupplierInvoice": 1,
                "GenericSupplierInvoiceLineItem": 1,
                "GenericNexonBilling": 1,
                "AccountPayableReconResult": 1,
            },
            {table: len(rows) for table, rows in database.tables.items()},
        )
        billing = database.tables["GenericNexonBilling"][0]
        self.assertEqual("2025Q4", billing["FinancialQuarter"])
        self.assertEqual("FY2025-2026", billing["FinancialYear"])
        self.assertEqual(6, billing["FinancialMonth"])
        self.assertEqual("inserted", manifest["write_disposition"])
        self.assertEqual("sqlserver", manifest["mode"])
        self.assertEqual(1, database.connections[0].commits)
        self.assertEqual(0, database.connections[0].rollbacks)
        self.assertTrue(database.connections[0].closed)
        self.assertEqual(1, persisted["rows"][0]["AccountPayableReconRequestId"])

    def test_identical_rerun_validates_existing_rows_without_new_inserts(self) -> None:
        normalized, candidates, matches = sample_payloads()
        database = FakeSqlServer()
        self.persist(database, normalized, candidates, matches)
        counts_before = {table: len(rows) for table, rows in database.tables.items()}

        persisted, manifest = self.persist(
            database, normalized, candidates, matches
        )

        self.assertEqual(
            counts_before,
            {table: len(rows) for table, rows in database.tables.items()},
        )
        self.assertEqual("validated_existing", manifest["write_disposition"])
        self.assertEqual(1, database.connections[-1].commits)
        self.assertEqual(0, database.connections[-1].rollbacks)
        self.assertEqual(1, persisted["rows"][0]["GenericSupplierInvoiceLineItemId"])

    def test_conflicting_rerun_rolls_back_and_closes_connection(self) -> None:
        normalized, candidates, matches = sample_payloads()
        database = FakeSqlServer()
        self.persist(database, normalized, candidates, matches)
        database.tables["GenericSupplierInvoiceLineItem"][0]["AmountExclGST"] = "99.99"

        with self.assertRaisesRegex(RuntimeError, "idempotency_conflict"):
            self.persist(database, normalized, candidates, matches)

        failed_connection = database.connections[-1]
        self.assertEqual(0, failed_connection.commits)
        self.assertEqual(1, failed_connection.rollbacks)
        self.assertTrue(failed_connection.closed)

    def test_malformed_payload_is_rejected_before_opening_connection(self) -> None:
        normalized, candidates, matches = sample_payloads()
        malformed = copy.deepcopy(normalized)
        malformed["lines"] = []
        database = FakeSqlServer()

        with patch.object(
            sqlserver_persistence, "_connect", side_effect=database.connect
        ) as connect:
            with self.assertRaisesRegex(RuntimeError, "payloads are malformed"):
                sqlserver_persistence.persist_sqlserver_run(
                    dsn="Driver=fake",
                    normalized=malformed,
                    candidates=candidates,
                    matches=matches,
                    provider_account_id=7,
                    run_path=self.run_path,
                )

        connect.assert_not_called()

    def test_core_cli_routes_sqlserver_and_azure_sql_to_production_adapter(self) -> None:
        normalized, candidates, matches = sample_payloads()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "config.yaml"
            config.write_text(
                "features:\n  core_persistence_enabled: true\n", encoding="utf-8"
            )
            paths = {}
            for name, payload in (
                ("normalized", normalized),
                ("candidates", candidates),
                ("matches", matches),
            ):
                paths[name] = root / f"{name}.json"
                paths[name].write_text(json.dumps(payload), encoding="utf-8")

            for mode in ("sqlserver", "azure_sql"):
                with self.subTest(mode=mode):
                    output = root / f"{mode}-output.json"
                    manifest = root / f"{mode}-manifest.json"
                    argv = [
                        "core_persistence",
                        "--config",
                        str(config),
                        "--normalized",
                        str(paths["normalized"]),
                        "--candidates",
                        str(paths["candidates"]),
                        "--matches",
                        str(paths["matches"]),
                        "--output",
                        str(output),
                        "--manifest",
                        str(manifest),
                        "--provider-account-id",
                        "7",
                        "--run-path",
                        self.run_path,
                    ]
                    adapter_result = (
                        {"rows": [{"line_id": "line-1"}]},
                        {"mode": "sqlserver", "transaction": "committed"},
                    )
                    with (
                        patch.object(sys, "argv", argv),
                        patch.dict(
                            os.environ,
                            {
                                "NEXON_RECON_CORE_MODE": mode,
                                "NEXON_RECON_CORE_DSN": "Driver=fake",
                            },
                            clear=False,
                        ),
                        patch.object(
                            core_persistence,
                            "persist_sqlserver_run",
                            return_value=adapter_result,
                        ) as persist,
                    ):
                        self.assertEqual(0, core_persistence.main())

                    persist.assert_called_once()
                    self.assertEqual(
                        "Driver=fake", persist.call_args.kwargs["dsn"]
                    )
                    self.assertEqual(adapter_result[0], json.loads(output.read_text()))
                    self.assertEqual(
                        adapter_result[1], json.loads(manifest.read_text())
                    )

    def test_preflight_marks_both_production_modes_ready_only_with_dsn(self) -> None:
        config = {
            "features": {
                "core_persistence_enabled": True,
                "billing_query_enabled": False,
            }
        }
        for mode in ("sqlserver", "azure_sql"):
            with self.subTest(mode=mode):
                with patch.dict(
                    os.environ,
                    {
                        "NEXON_RECON_CORE_MODE": mode,
                        "NEXON_RECON_CORE_DSN": "Driver=fake",
                    },
                    clear=True,
                ):
                    capabilities = preflight_check.capability_manifest(
                        config, local_check=False
                    )
                self.assertTrue(
                    capabilities["capabilities"]["core_supplier_persistence"]
                )
                self.assertTrue(
                    capabilities["capabilities"]["core_result_persistence"]
                )

        with patch.dict(
            os.environ,
            {"NEXON_RECON_CORE_MODE": "azure_sql"},
            clear=True,
        ):
            capabilities = preflight_check.capability_manifest(
                config, local_check=False
            )
        self.assertFalse(capabilities["capabilities"]["core_supplier_persistence"])


if __name__ == "__main__":
    unittest.main()
