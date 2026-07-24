from __future__ import annotations

import os
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
import sys

sys.path.insert(0, str(SCRIPTS))

from recon_core import run_recon, validate_run  # noqa: E402
from recon_core.common import RAW_WORKBOOK_COLUMNS, read_json, write_json  # noqa: E402


CONFIG = Path(__file__).resolve().parents[1] / "config" / "recon_settings.yaml"


def _sharepoint_binding(root: Path) -> Path:
    path = root / "sharepoint-target-binding.json"
    write_json(
        path,
        {
            "contract_version": 1,
            "site_name": "Nexon Reconciliation Automation",
            "site_id": "nexonap.sharepoint.com,site-guid,web-guid",
            "site_url": "https://nexonap.sharepoint.com/sites/NexonReconciliationAutomation",
            "hostname": "nexonap.sharepoint.com",
            "site_path": "/sites/NexonReconciliationAutomation",
            "drive_id": "drive-id",
            "drive_name": "Documents",
            "drive_web_url": (
                "https://nexonap.sharepoint.com/sites/"
                "NexonReconciliationAutomation/Shared%20Documents"
            ),
            "discovery_sha256": "a" * 64,
        },
    )
    return path


def _all_capabilities() -> dict:
    return {
        "capabilities": {
            "provider_parsing": True,
            "archive_validation": True,
            "core_supplier_persistence": True,
            "request_scoped_billing_preparation": True,
            "deterministic_comparison": True,
            "core_result_persistence": True,
            "current_workbook_generation": True,
        }
    }


def _download_receipt(root: Path) -> Path:
    path = root / "download-receipt.json"
    write_json(path, {"status": "test-fixture"})
    return path


def _args(
    *,
    source: Path | None = None,
    result_root: Path | None = None,
    run_mode: str | None = None,
    resume_root: Path | None = None,
    investigation: Path | None = None,
    publication_receipt: Path | None = None,
    local_only: bool = True,
) -> Namespace:
    return Namespace(
        resume_run_root=resume_root,
        config=CONFIG,
        provider="AAPT" if resume_root is None else None,
        source_file=source,
        result_root=result_root,
        run_mode=run_mode,
        intake_mode="manual_upload",
        source_identity="test-source",
        provider_api_manifest=None,
        provider_api_account_id=None,
        provider_api_document_id=None,
        copy=run_mode == "parser_validation",
        billing_sql_file=source.parent / "billing.sql" if source and run_mode == "reconciliation" else None,
        billing_period="2026-06",
        provider_account_id=7 if run_mode == "reconciliation" else None,
        investigation=investigation,
        publication_receipt=publication_receipt,
        sharepoint_binding=(
            _sharepoint_binding(source.parent)
            if source is not None and not local_only
            else None
        ),
        source_download_receipt=(
            _download_receipt(source.parent)
            if source is not None and not local_only
            else None
        ),
        sharepoint_auth_mode="auth_proxy",
        local_only=local_only,
        output=None,
    )


def _line(run_id: str) -> dict:
    return {
        "line_id": "line-1",
        "invoice_identity": "AAPT:INV-1",
        "request_key": f"{run_id}:AAPT:INV-1",
        "run_id": run_id,
        "provider": "AAPT",
        "invoice_number": "INV-1",
        "provider_account": "AAPT-ACCOUNT",
        "service_id_normalized": "SVC-1",
        "billing_period_start": "2026-06-01",
        "billing_period_end": "2026-06-30",
        "supplier_amount": "12.34",
    }


def _candidate() -> dict:
    return {
        "candidate_id": "candidate-1",
        "generic_nexon_billing_id": 101,
        "service_id": "SVC-1",
        "provider": "AAPT",
        "provider_account": "AAPT-ACCOUNT",
        "billing_date": "2026-06-30",
        "amount_excl_gst": "12.34",
        "customer_account": "CUST-1",
        "subscription_id": "SUB-1",
        "invoice_number": "CUSTOMER-INV-1",
        "service_id_match": True,
        "provider_match": True,
        "billing_period_match": True,
    }


class RuntimeHarness:
    def __init__(self, matched: bool) -> None:
        self.matched = matched

    @staticmethod
    def _value(command: list[str], flag: str) -> str:
        return command[command.index(flag) + 1]

    def command(self, command: list[str]) -> None:
        output = Path(self._value(command, "--output"))
        run_root = output.parents[1]
        run_id = run_root.name
        provider = run_root.parent.parent.parent.name
        if Path(command[1]).name == "parse_provider_invoice.py":
            write_json(
                output,
                {
                    "invoice_headers": [
                        {
                            "invoice_identity": "AAPT:INV-1",
                            "request_key": f"{run_id}:AAPT:INV-1",
                        }
                    ],
                    "lines": [_line(run_id)],
                },
            )
            write_json(Path(self._value(command, "--warnings")), [])
            write_json(
                Path(self._value(command, "--manifest")),
                {
                    "run_id": run_id,
                    "provider": provider,
                    "accounting_complete": True,
                    "source_rows": 1,
                    "parsed_rows": 1,
                    "documented_exclusions": 0,
                },
            )
            return

        candidates = [_candidate()] if self.matched else []
        write_json(
            output,
            {
                "run_id": run_id,
                "provider": provider,
                "candidates_by_line": {"line-1": candidates},
            },
        )
        write_json(
            Path(self._value(command, "--query-log")),
            [
                {
                    "run_id": run_id,
                    "provider": provider,
                    "chunk": 1,
                    "row_count": len(candidates),
                }
            ],
        )

    @staticmethod
    def persist(**kwargs):
        rows = [dict(row) for row in kwargs["matches"]["rows"]]
        run_id = kwargs["normalized"]["lines"][0]["run_id"]
        for row in rows:
            row["AccountPayableReconRequestId"] = 11
            row["GenericSupplierInvoiceLineItemId"] = 22
        return (
            {"rows": rows},
            {
                "run_id": run_id,
                "provider": "AAPT",
                "transaction": "committed",
                "request_count": 1,
                "invoice_count": 1,
                "supplier_line_count": len(rows),
                "result_count": len(rows),
            },
        )


class OrchestratorValidatorLifecycleTests(unittest.TestCase):
    def _new_run(
        self,
        root: Path,
        *,
        run_mode: str,
        matched: bool = False,
        local_only: bool = True,
    ) -> tuple[dict, Path]:
        source = root / "invoice.csv"
        source.write_text("invoice", encoding="utf-8")
        (root / "billing.sql").write_text("SELECT 1", encoding="utf-8")
        result_root = root / "results"
        (result_root / "AAPT").mkdir(parents=True)
        harness = RuntimeHarness(matched)
        args = _args(
            source=source,
            result_root=result_root,
            run_mode=run_mode,
            local_only=local_only,
        )
        core_mode = "sqlite_shadow" if local_only else "azure_sql"
        persistence = (
            patch.object(run_recon, "persist_shadow_run", side_effect=harness.persist)
            if local_only
            else patch.object(run_recon, "persist_sqlserver_run", side_effect=harness.persist)
        )
        with (
            patch.object(run_recon, "capability_manifest", return_value=_all_capabilities()),
            patch.object(
                run_recon,
                "_verify_download_receipt",
                return_value={"source_item_id": "source-item"},
            ),
            patch.object(run_recon, "_run_command", side_effect=harness.command),
            persistence,
            patch.dict(
                os.environ,
                {
                    "NEXON_RECON_CORE_MODE": core_mode,
                    "NEXON_RECON_CORE_DSN": str(root / "shadow.db"),
                },
                clear=False,
            ),
        ):
            result = run_recon.run(args)
        return result, Path(result["run_root"])

    def _resume(
        self,
        run_root: Path,
        *,
        investigation: Path | None = None,
        publication_receipt: Path | None = None,
        local_only: bool,
    ) -> dict:
        with patch.object(run_recon, "_verify_published_item"):
            return run_recon.run(
                _args(
                    resume_root=run_root,
                    investigation=investigation,
                    publication_receipt=publication_receipt,
                    local_only=local_only,
                )
            )

    def _mutate_json(
        self,
        run_root: Path,
        relative_path: str,
        mutation,
        message: str,
    ) -> None:
        path = run_root / relative_path
        original = path.read_bytes()
        try:
            payload = read_json(path)
            mutation(payload)
            write_json(path, payload)
            with self.assertRaisesRegex(RuntimeError, message):
                validate_run.validate_run(
                    run_root,
                    validate_run.load_config(CONFIG),
                    "reconciliation",
                )
        finally:
            path.write_bytes(original)

    def test_parser_validation_run_completes_and_validates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result, run_root = self._new_run(
                Path(tmp),
                run_mode="parser_validation",
            )
            self.assertEqual("passed", result["validation"])
            self.assertEqual(1, result["parsed_rows"])
            state = read_json(run_root / "manifest" / "run_state.json")
            self.assertEqual("completed", state["run_status"])
            self.assertEqual("completed", state["stages"]["validation"]["status"])
            self.assertEqual("skipped", state["stages"]["supplier_persistence"]["status"])

    def test_reconciliation_pauses_for_investigation_then_resumes_locally(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result, run_root = self._new_run(
                Path(tmp),
                run_mode="reconciliation",
                matched=False,
                local_only=True,
            )
            self.assertEqual("awaiting_exception_investigation", result["status"])
            self.assertEqual(1, result["unresolved_rows"])
            self.assertTrue(Path(result["raw_workbook"]).is_file())

            investigation = Path(tmp) / "investigation.json"
            write_json(
                investigation,
                {
                    "run_id": run_root.name,
                    "rows": [
                        {
                            "line_id": "line-1",
                            "agent_match_status": "no_match",
                            "agent_match_rule": "no_supported_candidate",
                            "agent_evidence_summary": "No supported billing candidate was found.",
                            "agent_review_required": True,
                        }
                    ],
                },
            )
            resumed = self._resume(
                run_root,
                investigation=investigation,
                local_only=True,
            )
            self.assertEqual("completed", resumed["run_status"])
            self.assertEqual("passed", resumed["validation"])
            self.assertTrue(
                (run_root / "refined-recon-report" / "refined-reconciliation.xlsx").is_file()
            )

            self._mutate_json(
                run_root,
                "manifest/run_manifest.json",
                lambda payload: payload.update({"run_id": "AAPT_20260709_153012_A1B2C"}),
                "Run manifest run_id",
            )
            self._mutate_json(
                run_root,
                "logs/parser_warnings.json",
                lambda payload: payload.append({"severity": "error"}),
                "Parser error warnings",
            )
            self._mutate_json(
                run_root,
                "manifest/parser_manifest.json",
                lambda payload: payload.update({"accounting_complete": False}),
                "complete member/row accounting",
            )
            self._mutate_json(
                run_root,
                "normalized/provider_lines.json",
                lambda payload: payload["lines"][0].update({"line_id": ""}),
                "requires line_id",
            )
            self._mutate_json(
                run_root,
                "manifest/persistence_manifest.json",
                lambda payload: payload.update({"transaction": "rolled_back"}),
                "committed transaction",
            )
            self._mutate_json(
                run_root,
                "evidence/billing_candidates.json",
                lambda payload: payload.update({"provider": "Optus"}),
                "Billing candidate evidence identity",
            )
            self._mutate_json(
                run_root,
                "manifest/audit_manifest.json",
                lambda payload: payload.update({"query_logs": []}),
                "query-log provenance",
            )
            self._mutate_json(
                run_root,
                "manifest/run_state.json",
                lambda payload: payload["stages"]["raw_workbook"].update({"status": "pending"}),
                "Required reconciliation stages",
            )
            self._mutate_json(
                run_root,
                "manifest/report_manifest.json",
                lambda payload: payload.update({"row_count": 99}),
                "row_count",
            )

    def test_publication_receipt_resumes_frozen_all_matched_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result, run_root = self._new_run(
                Path(tmp),
                run_mode="reconciliation",
                matched=True,
                local_only=False,
            )
            self.assertEqual("awaiting_publication", result["status"])
            publication_set = read_json(run_root / "manifest" / "publication_set.json")
            self.assertFalse(
                any(
                    item["relative_path"].startswith("source/")
                    for item in publication_set["artifacts"]
                )
            )
            run_prefix = (
                f"/recon-result-space/AAPT/{run_root.parent.parent.name}/"
                f"{run_root.parent.name}/{run_root.name}/"
            )
            uploaded = [
                {
                    "local_path": item["local_path"],
                    "sha256": item["sha256"],
                    "item_id": f"item-{index}",
                    "sharepoint_url": (
                        "https://nexonap.sharepoint.com/sites/"
                        "NexonReconciliationAutomation/Shared%20Documents"
                        f"{run_prefix}{item['relative_path']}"
                    ),
                }
                for index, item in enumerate(publication_set["artifacts"], start=1)
            ]
            run_manifest = read_json(run_root / "manifest" / "run_manifest.json")
            source_name = Path(run_manifest["source_file"]).name
            receipt = Path(tmp) / "publication-receipt.json"
            write_json(
                receipt,
                {
                    "run_id": run_root.name,
                    "uploaded_artifacts": uploaded,
                    "source_move_receipt": {
                        "status": "moved",
                        "item_id": "source-item",
                        "sha256": run_manifest["source_checksum_sha256"],
                        "sharepoint_url": (
                            "https://nexonap.sharepoint.com/sites/"
                            "NexonReconciliationAutomation/Shared%20Documents"
                            f"{run_prefix}source/{source_name}"
                        ),
                    },
                },
            )
            resumed = self._resume(
                run_root,
                publication_receipt=receipt,
                local_only=False,
            )
            self.assertEqual("completed", resumed["run_status"])
            self.assertEqual("passed", resumed["validation"])
            self.assertTrue((run_root / "manifest" / "publication_receipt.json").is_file())

    def test_injected_parser_failure_records_failed_stage_and_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "invoice.csv"
            source.write_text("invoice", encoding="utf-8")
            result_root = root / "results"
            (result_root / "AAPT").mkdir(parents=True)
            args = _args(
                source=source,
                result_root=result_root,
                run_mode="parser_validation",
            )
            with (
                patch.object(run_recon, "capability_manifest", return_value=_all_capabilities()),
                patch.object(
                    run_recon,
                    "_run_command",
                    side_effect=RuntimeError("parser_failure: injected"),
                ),
                self.assertRaisesRegex(RuntimeError, "parser_failure"),
            ):
                run_recon.run(args)

            failure_path = next(result_root.rglob("failure_manifest.json"))
            run_root = failure_path.parents[1]
            failure = read_json(failure_path)
            state = read_json(run_root / "manifest" / "run_state.json")
            self.assertEqual("provider_parsing", failure["failed_stage"])
            self.assertEqual("parser_failure", failure["failure_code"])
            self.assertEqual("failed", state["run_status"])
            self.assertEqual("failed", state["stages"]["provider_parsing"]["status"])

    def test_validator_rejects_workbook_and_provenance_mutations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result, run_root = self._new_run(
                Path(tmp),
                run_mode="reconciliation",
                matched=True,
                local_only=True,
            )
            self.assertEqual("passed", result["validation"])

            self._mutate_json(
                run_root,
                "manifest/run_manifest.json",
                lambda payload: payload.update({"db_update_enabled": True}),
                "update was not proven disabled",
            )
            self._mutate_json(
                run_root,
                "manifest/parser_manifest.json",
                lambda payload: payload.update({"parsed_rows": 2}),
                "parsed_rows",
            )
            self._mutate_json(
                run_root,
                "manifest/persistence_manifest.json",
                lambda payload: payload.update({"supplier_line_count": 2}),
                "supplier-line count",
            )
            self._mutate_json(
                run_root,
                "logs/billing_query_log.json",
                lambda payload: payload.clear(),
                "at least one audited query chunk",
            )
            self._mutate_json(
                run_root,
                "normalized/match_results.json",
                lambda payload: payload["rows"][0].update({"line_id": "other-line"}),
                "line identities",
            )
            self._mutate_json(
                run_root,
                "manifest/run_state.json",
                lambda payload: payload["stages"]["publication"].update({"status": "running"}),
                "not terminal",
            )

            manifest_path = run_root / "manifest" / "report_manifest.json"
            original_manifest = manifest_path.read_bytes()
            try:
                manifest = read_json(manifest_path)
                manifest["raw_output"] = str(run_root / "raw-recon-report" / "other.xlsx")
                write_json(manifest_path, manifest)
                with self.assertRaisesRegex(RuntimeError, "raw path"):
                    validate_run.validate_run(
                        run_root,
                        validate_run.load_config(CONFIG),
                        "reconciliation",
                    )
            finally:
                manifest_path.write_bytes(original_manifest)

            raw_path = run_root / "raw-recon-report" / "raw-reconciliation.xlsx"
            original_raw = raw_path.read_bytes()
            try:
                workbook = validate_run._load_workbook(raw_path)
                workbook["Do not change"]["B1"] = "/wrong/run"
                workbook.save(raw_path)
                with self.assertRaisesRegex(RuntimeError, "RunPath metadata"):
                    validate_run.validate_run(
                        run_root,
                        validate_run.load_config(CONFIG),
                        "reconciliation",
                    )
            finally:
                raw_path.write_bytes(original_raw)

            original_raw = raw_path.read_bytes()
            try:
                workbook = validate_run._load_workbook(raw_path)
                workbook["Result"].cell(1, 1).value = "WrongHeader"
                workbook.save(raw_path)
                with self.assertRaisesRegex(RuntimeError, "35-column contract"):
                    validate_run.validate_run(
                        run_root,
                        validate_run.load_config(CONFIG),
                        "reconciliation",
                    )
            finally:
                raw_path.write_bytes(original_raw)

            original_raw = raw_path.read_bytes()
            try:
                workbook = validate_run._load_workbook(raw_path)
                column = RAW_WORKBOOK_COLUMNS.index("ServiceProviderInvoiceNumber") + 1
                workbook["Result"].cell(2, column).value = "TAMPERED"
                workbook.save(raw_path)
                with self.assertRaisesRegex(RuntimeError, "persisted reconciliation results"):
                    validate_run.validate_run(
                        run_root,
                        validate_run.load_config(CONFIG),
                        "reconciliation",
                    )
            finally:
                raw_path.write_bytes(original_raw)

if __name__ == "__main__":
    unittest.main()
