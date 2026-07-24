from __future__ import annotations

import hashlib
import copy
import sqlite3
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from recon_core import core_persistence, record_notification, run_recon, run_state  # noqa: E402
from recon_core.common import read_json, write_json  # noqa: E402


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
        {"run_id": run_id, "provider": "AAPT", "candidates_by_line": {"line-1": [candidate]}},
        {"rows": [match]},
    )


class RuntimeStateAndPersistenceTests(unittest.TestCase):
    def test_orchestrator_enforces_audit_update_and_provider_api_gates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "config.yaml"
            base = {
                "resume_run_root": None,
                "config": config,
                "provider": "AAPT",
                "source_file": root / "invoice.csv",
                "result_root": root / "results",
                "run_mode": "parser_validation",
                "intake_mode": "manual_upload",
                "source_identity": None,
                "provider_api_manifest": None,
                "provider_api_account_id": None,
                "provider_api_document_id": None,
                "copy": True,
                "billing_sql_file": None,
                "billing_period": None,
                "provider_account_id": None,
                "investigation": None,
                "publication_receipt": None,
                "local_only": True,
                "output": None,
            }
            config.write_text(
                "features:\n  db_update_enabled: true\nbilling:\n  audit_required: true\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "must remain false"):
                run_recon.run(Namespace(**base))

            config.write_text(
                "features:\n"
                "  db_update_enabled: false\n"
                "  provider_api_enabled: false\n"
                "provider_api_adapters:\n"
                "  equinix: true\n"
                "billing:\n"
                "  audit_required: true\n",
                encoding="utf-8",
            )
            base["provider"] = "Equinix"
            base["intake_mode"] = "provider_api"
            base["run_mode"] = "reconciliation"
            base["copy"] = False
            with self.assertRaisesRegex(RuntimeError, "provider_api_not_available"):
                run_recon.run(Namespace(**base))

    def test_provider_api_provenance_binds_file_checksum_and_invoice_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "invoice.pdf"
            source.write_bytes(b"invoice")
            manifest = root / "download.json"
            write_json(
                manifest,
                {
                    "provider": "Equinix",
                    "account_id": "acct-1",
                    "invoice_id": "INV-1",
                    "output_file": str(source),
                    "output_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                },
            )
            args = Namespace(
                provider_api_manifest=manifest,
                provider="Equinix",
                source_file=source,
                source_identity="INV-1",
                provider_api_account_id="acct-1",
                provider_api_document_id=None,
            )
            run_recon._validate_provider_api_provenance(args)
            args.source_identity = "OTHER"
            with self.assertRaisesRegex(RuntimeError, "source-identity"):
                run_recon._validate_provider_api_provenance(args)

    def test_run_state_records_attempts_failure_and_completion_guard(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            run_state.create_state(
                path,
                run_id="AAPT_20260709_153012_A1B2C",
                provider="AAPT",
                run_mode="reconciliation",
                source_identity="sha256",
            )
            run_state.update_stage(path, "provider_parsing", "running")
            run_state.update_stage(
                path,
                "provider_parsing",
                "completed",
                counts={"parsed_rows": 1},
                artifacts=["normalized.json"],
            )
            state = read_json(path)
            self.assertEqual(1, state["stages"]["provider_parsing"]["attempts"])
            self.assertEqual({"parsed_rows": 1}, state["stages"]["provider_parsing"]["counts"])
            run_state.update_stage(path, "validation", "failed", failure_code="bad", retryable=True)
            with self.assertRaisesRegex(RuntimeError, "failed stages"):
                run_state.finalize_state(path, "completed")
            self.assertEqual("failed", run_state.finalize_state(path, "failed")["run_status"])

    def test_shadow_persistence_is_transactional_and_idempotent(self) -> None:
        normalized, candidates, matches = sample_payloads()
        with tempfile.TemporaryDirectory() as tmp:
            database = str(Path(tmp) / "shadow.db")
            first, first_manifest = core_persistence.persist_shadow_run(
                dsn=database,
                normalized=normalized,
                candidates=candidates,
                matches=matches,
                provider_account_id=7,
                run_path="/run/AAPT_20260709_153012_A1B2C",
            )
            second, second_manifest = core_persistence.persist_shadow_run(
                dsn=database,
                normalized=normalized,
                candidates=candidates,
                matches=matches,
                provider_account_id=7,
                run_path="/run/AAPT_20260709_153012_A1B2C",
            )
            self.assertEqual(first["rows"][0]["AccountPayableReconRequestId"], second["rows"][0]["AccountPayableReconRequestId"])
            self.assertEqual(first_manifest, second_manifest)
            changed = copy.deepcopy(normalized)
            changed["lines"][0]["supplier_amount"] = "99.99"
            with self.assertRaisesRegex(RuntimeError, "idempotency_conflict"):
                core_persistence.persist_shadow_run(
                    dsn=database,
                    normalized=changed,
                    candidates=candidates,
                    matches=matches,
                    provider_account_id=7,
                    run_path="/run/AAPT_20260709_153012_A1B2C",
                )
            changed_status = copy.deepcopy(matches)
            changed_status["rows"][0]["ReconMatchStatus"] = "Not Matched"
            with self.assertRaisesRegex(RuntimeError, "idempotency_conflict"):
                core_persistence.persist_shadow_run(
                    dsn=database,
                    normalized=normalized,
                    candidates=candidates,
                    matches=changed_status,
                    provider_account_id=7,
                    run_path="/run/AAPT_20260709_153012_A1B2C",
                )
            next_normalized, next_candidates, next_matches = sample_payloads()
            next_run_id = "AAPT_20260710_153012_C3D4E"
            next_normalized["invoice_headers"][0]["request_key"] = f"{next_run_id}:invoice-1"
            next_normalized["lines"][0]["request_key"] = f"{next_run_id}:invoice-1"
            next_normalized["lines"][0]["run_id"] = next_run_id
            next_normalized["lines"][0]["line_id"] = "line-2"
            next_candidates["run_id"] = next_run_id
            next_candidates["candidates_by_line"]["line-2"] = next_candidates["candidates_by_line"].pop("line-1")
            next_matches["rows"][0]["line_id"] = "line-2"
            next_matches["rows"][0]["run_id"] = next_run_id
            core_persistence.persist_shadow_run(
                dsn=database,
                normalized=next_normalized,
                candidates=next_candidates,
                matches=next_matches,
                provider_account_id=7,
                run_path=f"/run/{next_run_id}",
            )
            connection = sqlite3.connect(database)
            try:
                for table in (
                    "AccountPayableReconRequest",
                    "GenericSupplierInvoice",
                    "GenericSupplierInvoiceLineItem",
                    "GenericNexonBilling",
                    "AccountPayableReconResult",
                ):
                    self.assertEqual(2, connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            finally:
                connection.close()

    def test_shadow_persistence_rolls_back_invalid_status(self) -> None:
        normalized, candidates, matches = sample_payloads()
        matches["rows"][0]["ReconMatchStatus"] = "Invented"
        with tempfile.TemporaryDirectory() as tmp:
            database = str(Path(tmp) / "shadow.db")
            with self.assertRaisesRegex(RuntimeError, "unsupported ReconMatchStatus"):
                core_persistence.persist_shadow_run(
                    dsn=database,
                    normalized=normalized,
                    candidates=candidates,
                    matches=matches,
                    provider_account_id=7,
                    run_path="/run/AAPT_20260709_153012_A1B2C",
                )
            connection = sqlite3.connect(database)
            try:
                self.assertEqual(0, connection.execute("SELECT COUNT(*) FROM AccountPayableReconRequest").fetchone()[0])
            finally:
                connection.close()

    def test_investigation_envelope_is_run_bound_and_cannot_auto_match(self) -> None:
        rows = [{"line_id": "line-1", "ReconMatchStatus": "Not Matched"}]
        payload = run_recon._unresolved_payload(
            rows,
            {"candidates_by_line": {"line-1": [{"candidate_id": "candidate-1"}]}},
            run_id="AAPT_20260709_153012_A1B2C",
            parser_warnings=[],
            query_log_identity={"sha256": "abc", "chunk_count": 1},
            remaining_query_rounds=2,
        )
        self.assertEqual("AAPT_20260709_153012_A1B2C", payload["run_id"])
        investigation = {
            "run_id": payload["run_id"],
            "rows": [
                {
                    "line_id": "line-1",
                    "agent_match_status": "auto_matched",
                    "agent_evidence_summary": "Not allowed.",
                }
            ],
        }
        with self.assertRaisesRegex(RuntimeError, "unsupported agent_match_status"):
            run_recon._apply_investigation(rows, investigation, expected_run_id=payload["run_id"])

    def test_notification_receipt_updates_audit_without_attachments(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_root = Path(tmp)
            (run_root / "manifest").mkdir()
            write_json(run_root / "manifest" / "audit_manifest.json", {"run_id": "run-1"})
            old_argv = sys.argv
            try:
                sys.argv = [
                    "record_notification",
                    "--run-root",
                    str(run_root),
                    "--status",
                    "sent",
                    "--message-id",
                    "message-1",
                    "--recipient-count",
                    "2",
                ]
                self.assertEqual(0, record_notification.main())
            finally:
                sys.argv = old_argv
            receipt = read_json(run_root / "manifest" / "notification_receipt.json")
            self.assertEqual([], receipt["attachments"])
            self.assertEqual("message-1", read_json(run_root / "manifest" / "audit_manifest.json")["notification_receipt"]["message_id"])

    def test_core_persistence_cli_requires_explicit_shadow_capability(self) -> None:
        normalized, candidates, matches = sample_payloads()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, payload in (
                ("normalized.json", normalized),
                ("candidates.json", candidates),
                ("matches.json", matches),
            ):
                write_json(root / name, payload)
            config = root / "config.yaml"
            config.write_text("features:\n  core_persistence_enabled: false\n", encoding="utf-8")
            old_argv = sys.argv
            try:
                sys.argv = [
                    "core_persistence",
                    "--config",
                    str(config),
                    "--normalized",
                    str(root / "normalized.json"),
                    "--candidates",
                    str(root / "candidates.json"),
                    "--matches",
                    str(root / "matches.json"),
                    "--output",
                    str(root / "output.json"),
                    "--manifest",
                    str(root / "manifest.json"),
                    "--provider-account-id",
                    "7",
                    "--run-path",
                    "/run/one",
                ]
                with self.assertRaisesRegex(RuntimeError, "feature is disabled"):
                    core_persistence.main()
            finally:
                sys.argv = old_argv


if __name__ == "__main__":
    unittest.main()
