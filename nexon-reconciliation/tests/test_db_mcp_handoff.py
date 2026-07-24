from __future__ import annotations

import json
import sys
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from recon_core import db_mcp_handoff, run_recon  # noqa: E402
from recon_core.common import read_json, sha256_file, write_json  # noqa: E402
from test_orchestrator_validator_lifecycle import (  # noqa: E402
    RuntimeHarness,
    _capability_receipt,
    _download_receipt,
)


RUN_ID = "AAPT_20260724_123456_ABC12"
SQL = (
    "SELECT Carrier_Name AS provider, AccountNumber AS provider_account, "
    "BillingDate AS transaction_date, ServiceNumber AS service_id, "
    "GenericNexonBillingId AS candidate_id "
    "FROM Finance.GenericNexonBilling"
)
CONFIG = {
    "features": {"billing_query_enabled": True},
    "billing": {
        "mode": "read_only_sql",
        "agent_sql_allowed": True,
        "audit_required": True,
    },
    "limits": {
        "investigation_rows_per_batch": 100,
        "billing_query_row_limit": 5000,
        "billing_query_timeout_seconds": 30,
        "investigation_candidates_per_row": 20,
    },
}


def normalized() -> dict:
    return {
        "lines": [
            {
                "run_id": RUN_ID,
                "provider": "AAPT",
                "line_id": "line-1",
                "provider_account": "ACC-1",
                "service_id_raw": "SVC-1",
                "service_id_normalized": "SVC-1",
                "billing_period_start": "2026-06-01",
                "billing_period_end": "2026-06-30",
            }
        ]
    }


def write(path: Path, payload: object) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_validate_database_mcp_contract() -> None:
    capabilities = {
        "service": "nexon-recon-db-mcp",
        "environment": "dev",
        "capabilities": {
            "read_queries": True,
            "core_persistence": True,
        },
        "query_policy": {
            "read_only": True,
            "schema_qualified_allowlist": True,
            "comments_allowed": False,
            "wildcard_projection_allowed": False,
            "row_limit": 5000,
            "audit_required": True,
        },
    }
    probe = {
        "environment": "dev",
        "reachable": True,
        "database_name": "test_database",
    }
    result = db_mcp_handoff.validate_database_mcp(
        capabilities,
        probe,
        environment="dev",
        require_persistence=True,
        row_limit=5000,
    )
    assert result["core_persistence"] is True
    for mutated in (
        {**capabilities, "service": "wrong"},
        {**capabilities, "capabilities": {"read_queries": False}},
    ):
        with pytest.raises(RuntimeError, match="database_mcp_capability_invalid"):
            db_mcp_handoff.validate_database_mcp(
                mutated,
                probe,
                environment="dev",
                require_persistence=False,
                row_limit=5000,
            )
    no_write = {
        **capabilities,
        "capabilities": {"read_queries": True, "core_persistence": False},
    }
    with pytest.raises(RuntimeError, match="core persistence"):
        db_mcp_handoff.validate_database_mcp(
            no_write,
            probe,
            environment="dev",
            require_persistence=True,
            row_limit=5000,
        )
    with pytest.raises(RuntimeError, match="database_mcp_probe_invalid"):
        db_mcp_handoff.validate_database_mcp(
            capabilities,
            {**probe, "reachable": False},
            environment="dev",
            require_persistence=False,
            row_limit=5000,
        )


def test_plan_consume_and_persistence_receipts(tmp_path: Path) -> None:
    sql_file = tmp_path / "billing.sql"
    sql_file.write_text(SQL, encoding="utf-8")
    plan = db_mcp_handoff.prepare_billing_query_plan(
        normalized=normalized(),
        sql_file=sql_file,
        config=CONFIG,
        environment="dev",
    )
    request = plan["requests"][0]
    assert "SELECT *" not in request["sql"].upper()
    assert "OPENJSON" not in request["sql"].upper()
    assert request["parameters"]["service_id_0"] == "SVC-1"

    receipt = {
        "environment": "dev",
        "run_id": RUN_ID,
        "query_sha256": request["query_sha256"],
        "parameter_sha256": request["parameter_sha256"],
        "tables": ["finance.genericnexonbilling"],
        "row_count": 1,
        "rows": [
            {
                "provider": "AAPT",
                "provider_account": "ACC-1",
                "transaction_date": "2026-06-15",
                "service_id": "SVC-1",
                "candidate_id": 7,
            }
        ],
    }
    candidates, query_log = db_mcp_handoff.consume_billing_query_receipts(
        plan=plan,
        receipt_paths=[write(tmp_path / "query.json", receipt)],
        normalized=normalized(),
        config=CONFIG,
    )
    assert candidates["candidates_by_line"]["line-1"][0]["service_id_match"] is True
    assert query_log[0]["billing_mode"] == "database_mcp"

    persistence_request = db_mcp_handoff.prepare_persistence_request(
        environment="dev",
        run_id=RUN_ID,
        normalized=normalized(),
        candidates=candidates,
        matches={"rows": [{"line_id": "line-1"}]},
        provider_account_id=9,
        run_path="/recon-result-space/AAPT/2026/07/run",
    )
    persistence_receipt = {
        "environment": "dev",
        "run_id": RUN_ID,
        "payload_sha256": persistence_request["payload_sha256"],
        "persisted": {"rows": [{"line_id": "line-1"}]},
        "manifest": {
            "request_count": 1,
            "invoice_count": 1,
            "supplier_line_count": 1,
            "result_count": 1,
        },
    }
    persisted, manifest = db_mcp_handoff.consume_persistence_receipt(
        persistence_request,
        write(tmp_path / "persist.json", persistence_receipt),
    )
    assert persisted["rows"][0]["line_id"] == "line-1"
    assert manifest["result_count"] == 1


def test_handoff_rejects_malformed_inputs(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="expected an object"):
        db_mcp_handoff._result([], name="receipt")
    with pytest.raises(RuntimeError, match="result must be an object"):
        db_mcp_handoff._result({"result": []}, name="receipt")
    with pytest.raises(RuntimeError, match="unique safe name"):
        db_mcp_handoff._projection_names(
            "SELECT Name AS provider, Name AS provider FROM Finance.ServiceProvider"
        )
    with pytest.raises(RuntimeError, match="no SELECT projection"):
        db_mcp_handoff._projection_names(
            "DELETE FROM Finance.ServiceProvider WHERE Name = 'x'"
        )


def test_fleet_run_pauses_for_database_tools_and_resumes(tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    config.write_text(
        """
timezone: Australia/Sydney
sharepoint_intake:
  environment: dev
database_mcp:
  environment: dev
features:
  provider_api_enabled: false
  billing_query_enabled: true
  core_persistence_enabled: true
  deterministic_matching_enabled: true
  db_update_enabled: false
  failure_notifications_enabled: false
billing:
  mode: read_only_sql
  agent_sql_allowed: true
  audit_required: true
reports:
  evidence_summary:
    auto_matched: short
    max_chars: 160
limits:
  investigation_rows_per_batch: 100
  investigation_candidates_per_row: 20
  investigation_query_rounds: 2
  billing_query_row_limit: 5000
  billing_query_timeout_seconds: 30
  max_zip_members: 2000
  max_single_file_mb: 250
  max_total_expanded_mb: 1000
  max_compression_ratio: 100
db_update_policy:
  allow_deferred_without_invoice_number: false
""".lstrip(),
        encoding="utf-8",
    )
    source = tmp_path / "invoice.csv"
    source.write_text("invoice", encoding="utf-8")
    sql_file = tmp_path / "billing.sql"
    sql_file.write_text(SQL, encoding="utf-8")
    result_root = tmp_path / "results"
    (result_root / "AAPT").mkdir(parents=True)
    database_capabilities = write(
        tmp_path / "database-capabilities.json",
        {
            "service": "nexon-recon-db-mcp",
            "environment": "dev",
            "capabilities": {
                "read_queries": True,
                "core_persistence": True,
            },
            "query_policy": {
                "read_only": True,
                "schema_qualified_allowlist": True,
                "comments_allowed": False,
                "wildcard_projection_allowed": False,
                "row_limit": 5000,
                "audit_required": True,
            },
        },
    )
    database_probe = write(
        tmp_path / "database-probe.json",
        {
            "environment": "dev",
            "reachable": True,
            "database_name": "test_database",
        },
    )
    args = Namespace(
        resume_run_root=None,
        config=config,
        provider="AAPT",
        source_file=source,
        result_root=result_root,
        run_mode="reconciliation",
        intake_mode="manual_upload",
        source_identity="test-source",
        provider_api_manifest=None,
        provider_api_account_id=None,
        provider_api_document_id=None,
        copy=False,
        billing_sql_file=sql_file,
        billing_period="2026-06",
        provider_account_id=7,
        investigation=None,
        publication_receipt=None,
        publication_verification_receipt=None,
        source_download_receipt=_download_receipt(source),
        sharepoint_mcp_capabilities=_capability_receipt(tmp_path),
        database_mcp_capabilities=database_capabilities,
        database_mcp_probe=database_probe,
        billing_mcp_receipt=None,
        database_persistence_receipt=None,
        local_only=False,
        output=None,
    )
    harness = RuntimeHarness(True)
    missing_environment_config = tmp_path / "missing-database-environment.yaml"
    missing_environment_config.write_text(
        config.read_text(encoding="utf-8").replace(
            "database_mcp:\n  environment: dev",
            "database_mcp: {}",
        ),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="database_mcp_config_invalid"):
        run_recon.run(
            Namespace(**{**vars(args), "config": missing_environment_config})
        )
    with (
        patch.object(
            run_recon,
            "_verify_download_receipt",
            side_effect=lambda **kwargs: read_json(kwargs["receipt_path"]),
        ),
        patch.object(run_recon, "_run_command", side_effect=harness.command),
    ):
        first = run_recon.run(args)
    assert first["status"] == "awaiting_billing_query"
    run_root = Path(first["run_root"])
    with pytest.raises(RuntimeError, match="--billing-mcp-receipt"):
        run_recon.run(
            Namespace(
                **{
                    **vars(args),
                    "resume_run_root": run_root,
                    "source_file": None,
                }
            )
        )
    plan = read_json(Path(first["billing_mcp_plan"]))
    request = plan["requests"][0]
    billing_receipt = write(
        tmp_path / "billing-receipt.json",
        {
            "environment": "dev",
            "run_id": run_root.name,
            "query_sha256": request["query_sha256"],
            "parameter_sha256": request["parameter_sha256"],
            "tables": ["finance.genericnexonbilling"],
            "row_count": 1,
            "rows": [
                {
                    "candidate_id": "candidate-1",
                    "generic_nexon_billing_id": 101,
                    "service_id": "SVC-1",
                    "provider": "AAPT",
                    "provider_account": "AAPT-ACCOUNT",
                    "transaction_date": "2026-06-30",
                    "amount_excl_gst": "12.34",
                    "customer_account": "CUST-1",
                    "subscription_id": "SUB-1",
                    "invoice_number": "CUSTOMER-INV-1",
                }
            ],
        },
    )
    resume = Namespace(
        **{
            **vars(args),
            "resume_run_root": run_root,
            "source_file": None,
            "billing_mcp_receipt": [billing_receipt],
        }
    )
    second = run_recon.run(resume)
    assert second["status"] == "awaiting_core_persistence"
    assert not Path(first["billing_mcp_plan"]).exists()
    with pytest.raises(RuntimeError, match="--database-persistence-receipt"):
        run_recon.run(
            Namespace(
                **{
                    **vars(resume),
                    "billing_mcp_receipt": None,
                }
            )
        )
    persistence_request = read_json(Path(second["database_persistence_request"]))
    rows = read_json(run_root / "normalized" / "match_results.json")["rows"]
    for row in rows:
        row["AccountPayableReconRequestId"] = 11
        row["GenericSupplierInvoiceLineItemId"] = 22
    persistence_receipt = write(
        tmp_path / "persistence-receipt.json",
        {
            "environment": "dev",
            "run_id": run_root.name,
            "payload_sha256": persistence_request["payload_sha256"],
            "persisted": {"rows": rows},
            "manifest": {
                "run_id": run_root.name,
                "provider": "AAPT",
                "transaction": "committed",
                "request_count": 1,
                "invoice_count": 1,
                "supplier_line_count": 1,
                "result_count": 1,
            },
        },
    )
    third = run_recon.run(
        Namespace(
            **{
                **vars(resume),
                "billing_mcp_receipt": None,
                "database_persistence_receipt": persistence_receipt,
            }
        )
    )
    assert third["status"] == "awaiting_publication"
    assert not Path(second["database_persistence_request"]).exists()
    assert (run_root / "raw-recon-report" / "raw-reconciliation.xlsx").is_file()
    assert sha256_file(run_root / "manifest" / "database_mcp_capabilities.json")
    assert set(
        read_json(run_root / "manifest" / "audit_manifest.json")[
            "database_handoffs"
        ]
    ) == {"billing_query", "core_persistence"}

    sql_file = tmp_path / "billing.sql"
    sql_file.write_text(SQL, encoding="utf-8")
    with pytest.raises(RuntimeError, match="non-empty list"):
        db_mcp_handoff.prepare_billing_query_plan(
            normalized={"lines": []},
            sql_file=sql_file,
            config=CONFIG,
            environment="dev",
        )
    invalid_scope = normalized()
    invalid_scope["lines"].append(
        {
            **invalid_scope["lines"][0],
            "line_id": "line-2",
            "provider": "Optus",
        }
    )
    with pytest.raises(RuntimeError, match="one run/provider"):
        db_mcp_handoff.prepare_billing_query_plan(
            normalized=invalid_scope,
            sql_file=sql_file,
            config=CONFIG,
            environment="dev",
        )
    no_service = normalized()
    no_service["lines"][0]["service_id_raw"] = ""
    no_service["lines"][0]["service_id_normalized"] = ""
    with pytest.raises(RuntimeError, match="no service identifiers"):
        db_mcp_handoff.prepare_billing_query_plan(
            normalized=no_service,
            sql_file=sql_file,
            config=CONFIG,
            environment="dev",
        )
    plan = db_mcp_handoff.prepare_billing_query_plan(
        normalized=normalized(),
        sql_file=sql_file,
        config=CONFIG,
        environment="dev",
    )
    with pytest.raises(RuntimeError, match="exactly one receipt"):
        db_mcp_handoff.consume_billing_query_receipts(
            plan=plan,
            receipt_paths=[],
            normalized=normalized(),
            config=CONFIG,
        )
    with pytest.raises(RuntimeError, match="requests are missing"):
        db_mcp_handoff.consume_billing_query_receipts(
            plan={},
            receipt_paths=[],
            normalized=normalized(),
            config=CONFIG,
        )
    bad = {
        "environment": "wrong",
        "run_id": RUN_ID,
        "query_sha256": "x",
        "parameter_sha256": "x",
        "tables": [],
        "row_count": 0,
        "rows": [],
    }
    with pytest.raises(RuntimeError, match="identity or limits"):
        db_mcp_handoff.consume_billing_query_receipts(
            plan=plan,
            receipt_paths=[write(tmp_path / "bad.json", bad)],
            normalized=normalized(),
            config=CONFIG,
        )
    extra = {
        **bad,
        "environment": "dev",
        "query_sha256": plan["requests"][0]["query_sha256"],
        "parameter_sha256": plan["requests"][0]["parameter_sha256"],
        "tables": ["finance.genericnexonbilling"],
        "unexpected": True,
    }
    with pytest.raises(RuntimeError, match="fields do not match"):
        db_mcp_handoff.consume_billing_query_receipts(
            plan=plan,
            receipt_paths=[write(tmp_path / "extra.json", extra)],
            normalized=normalized(),
            config=CONFIG,
        )
    non_object_row = {
        key: value for key, value in extra.items() if key != "unexpected"
    }
    non_object_row["rows"] = ["bad"]
    non_object_row["row_count"] = 1
    with pytest.raises(RuntimeError, match="every returned row"):
        db_mcp_handoff.consume_billing_query_receipts(
            plan=plan,
            receipt_paths=[write(tmp_path / "row.json", non_object_row)],
            normalized=normalized(),
            config=CONFIG,
        )
    unknown_plan = json.loads(json.dumps(plan))
    unknown_plan["requests"][0]["line_ids"] = ["unknown"]
    good_empty = {
        **non_object_row,
        "rows": [],
        "row_count": 0,
    }
    with pytest.raises(RuntimeError, match="unknown line"):
        db_mcp_handoff.consume_billing_query_receipts(
            plan=unknown_plan,
            receipt_paths=[write(tmp_path / "unknown.json", good_empty)],
            normalized=normalized(),
            config=CONFIG,
        )
    limited_config = json.loads(json.dumps(CONFIG))
    limited_config["limits"]["investigation_candidates_per_row"] = 1
    duplicate_rows = {
        **good_empty,
        "row_count": 2,
        "rows": [
            {
                "service_id": "SVC-1",
                "provider": "AAPT",
                "transaction_date": "2026-06-01",
                "candidate_id": "1",
            },
            {
                "service_id": "SVC-1",
                "provider": "AAPT",
                "transaction_date": "2026-06-02",
                "candidate_id": "2",
            },
        ],
    }
    with pytest.raises(RuntimeError, match="more than 1 candidates"):
        db_mcp_handoff.consume_billing_query_receipts(
            plan=plan,
            receipt_paths=[write(tmp_path / "many.json", duplicate_rows)],
            normalized=normalized(),
            config=limited_config,
        )
    request = db_mcp_handoff.prepare_persistence_request(
        environment="dev",
        run_id=RUN_ID,
        normalized=normalized(),
        candidates={},
        matches={"rows": []},
        provider_account_id=9,
        run_path="/run",
    )
    with pytest.raises(RuntimeError, match="identity or payload"):
        db_mcp_handoff.consume_persistence_receipt(
            request,
            write(
                tmp_path / "bad-persist.json",
                {
                    "environment": "wrong",
                    "run_id": RUN_ID,
                    "payload_sha256": request["payload_sha256"],
                    "persisted": {"rows": []},
                    "manifest": {},
                },
            ),
        )
    with pytest.raises(RuntimeError, match="fields do not match"):
        db_mcp_handoff.consume_persistence_receipt(
            request,
            write(
                tmp_path / "extra-persist.json",
                {
                    "environment": "dev",
                    "run_id": RUN_ID,
                    "payload_sha256": request["payload_sha256"],
                    "persisted": {"rows": []},
                    "manifest": {},
                    "unexpected": True,
                },
            ),
        )
