from __future__ import annotations

import runpy
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from recon_core import billing_query, intake_run, preflight_check, write_reports  # noqa: E402
from recon_core.common import (  # noqa: E402
    EXCLUDED_PHASE1_COLUMNS,
    RAW_WORKBOOK_COLUMNS,
    write_json,
)


@pytest.mark.parametrize(
    "module_name",
    [
        "recon_core.billing_query",
        "recon_core.intake_run",
        "recon_core.preflight_check",
        "recon_core.write_reports",
    ],
)
@pytest.mark.filterwarnings("ignore:.*found in sys.modules.*:RuntimeWarning")
def test_module_entry_points(module_name: str) -> None:
    with patch.object(sys, "argv", [module_name, "--help"]):
        with pytest.raises(SystemExit) as exc_info:
            runpy.run_module(module_name, run_name="__main__")
    assert exc_info.value.code == 0


def test_billing_query_remaining_guards_and_sqlite_paths(tmp_path: Path) -> None:
    sql_file = tmp_path / "query.sql"
    sql_file.write_text("SELECT 1", encoding="utf-8")
    assert billing_query._load_sql(None, sql_file) == ("SELECT 1", str(sql_file))

    with pytest.raises(RuntimeError, match="must start with SELECT or WITH"):
        billing_query._assert_read_only_sql("PRAGMA table_info(billing_candidates)")
    with pytest.raises(RuntimeError, match="forbidden SQL tokens"):
        billing_query._assert_read_only_sql("SELECT update FROM billing_candidates")

    assert billing_query._execute_sqlite(
        ":memory:",
        "SELECT 1 AS value",
        {},
        timeout_seconds=1,
        row_limit=1,
    ) == [{"value": 1}]

    with (
        patch.dict(
            billing_query.os.environ,
            {
                "NEXON_RECON_BILLING_MODE": "sqlite",
                "NEXON_RECON_BILLING_DSN": ":memory:",
            },
        ),
        patch.object(billing_query, "_execute_sqlite", return_value=[{"value": 2}]) as execute,
    ):
        assert billing_query._execute_query("SELECT 2 AS value", {}) == [{"value": 2}]
        execute.assert_called_once()


def test_intake_rejects_disabled_provider_api(tmp_path: Path) -> None:
    source = tmp_path / "invoice.csv"
    source.write_text("invoice", encoding="utf-8")
    config = {
        "features": {
            "db_update_enabled": False,
            "provider_api_enabled": False,
        },
        "provider_api_adapters": {"aapt": True},
    }

    with pytest.raises(RuntimeError, match="provider_api_not_available"):
        intake_run.create_run(
            config=config,
            provider="AAPT",
            source_file=source,
            result_root=tmp_path,
            intake_mode="provider_api",
            source_identity=None,
            copy_source=True,
        )


def test_preflight_remaining_main_paths(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    base_config = {
        "features": {"db_update_enabled": False},
        "billing": {"audit_required": True},
        "provider_api_adapters": {},
        "sharepoint_intake": {
            "environment": "dev",
            "gateway_host": "nexon-recon-sharepoint-dev.netbird.aaic.cc",
        },
    }
    output = tmp_path / "capabilities.json"
    mcp_capabilities = tmp_path / "mcp-capabilities.json"
    mcp_probe = tmp_path / "mcp-probe.json"
    write_json(
        mcp_capabilities,
        {
            "schema_version": "1.0",
            "kind": "capabilities",
            "result": {
                "status": "ok",
                "environment": "dev",
                "read_only": True,
                "download_contract_version": 1,
                "tools": sorted(preflight_check.SHAREPOINT_INTAKE_TOOLS),
                "binary_delivery": {
                    "method": "POST",
                    "endpoint": "https://nexon-recon-sharepoint-dev.netbird.aaic.cc/download",
                    "ticket_header": "X-Recon-Download-Ticket",
                    "single_use": True,
                },
                "attestation": {
                    "algorithm": "Ed25519",
                    "public_key": "A" * 43,
                    "public_key_sha256": "a" * 64,
                },
                "limits": {"max_candidates": 50},
                "providers": {
                    name: sorted(extensions)
                    for name, extensions in preflight_check.EXPECTED_PROVIDER_EXTENSIONS.items()
                },
            },
        },
    )
    write_json(
        mcp_probe,
        {
            "schema_version": "1.0",
            "kind": "probe",
            "result": {
                "status": "ok",
                "environment": "dev",
                "reachable": True,
                "site_name": "Nexon Reconciliation Automation",
                "hostname": "tenant.sharepoint.com",
                "path": "/sites/NexonReconciliationAutomation",
                "spaces": ["upload", "reference", "result"],
            },
        },
    )

    with (
        patch.object(
            sys,
            "argv",
            [
                "preflight_check.py",
                "--sharepoint-mcp-capabilities",
                str(mcp_capabilities),
                "--sharepoint-mcp-probe",
                str(mcp_probe),
                "--output",
                str(output),
            ],
        ),
        patch.object(preflight_check, "load_config", return_value=base_config),
        patch.object(preflight_check, "sharepoint_roots", return_value=(tmp_path, tmp_path)),
    ):
        assert preflight_check.main() == 0
    assert output.is_file()
    assert "SharePoint Intake MCP receipts validated" in capsys.readouterr().out

    invalid_config = {**base_config, "provider_api_adapters": {"unknown": True}}
    with (
        patch.object(sys, "argv", ["preflight_check.py"]),
        patch.object(preflight_check, "load_config", return_value=invalid_config),
        patch.object(preflight_check, "sharepoint_roots", return_value=(tmp_path, tmp_path)),
        pytest.raises(RuntimeError, match="Unsupported provider API adapter"),
    ):
        preflight_check.main()

    upload_root = tmp_path / "upload"
    result_root = tmp_path / "result"
    upload_root.mkdir()
    result_root.mkdir()
    with (
        patch.object(sys, "argv", ["preflight_check.py", "--local-check"]),
        patch.object(preflight_check, "load_config", return_value=base_config),
        patch.object(
            preflight_check,
            "sharepoint_roots",
            return_value=(upload_root, result_root),
        ),
    ):
        assert preflight_check.main() == 2
    output_text = capsys.readouterr().out
    assert "Missing upload folder" in output_text
    assert "Missing result folder" in output_text


def test_write_reports_remaining_guards_and_validation_skip(tmp_path: Path) -> None:
    columns = [column for column in RAW_WORKBOOK_COLUMNS if column != "Dispute or Not"]
    write_reports._write_workbook(
        tmp_path / "without-dispute-column.xlsx",
        [],
        columns,
        run_path="run",
        period="2026-07",
    )

    with pytest.raises(RuntimeError, match="same row count"):
        write_reports.write_reports(
            raw_rows=[{}],
            refined_input_rows=[],
            raw_output=tmp_path / "raw.xlsx",
            refined_output=None,
            manifest=tmp_path / "manifest.json",
            config={},
            run_path="run",
            period="2026-07",
        )

    excluded_column = next(iter(EXCLUDED_PHASE1_COLUMNS))
    with pytest.raises(RuntimeError, match="Excluded runtime columns"):
        write_reports.write_reports(
            raw_rows=[{excluded_column: "leaked"}],
            refined_input_rows=[{}],
            raw_output=tmp_path / "raw.xlsx",
            refined_output=None,
            manifest=tmp_path / "manifest.json",
            config={},
            run_path="run",
            period="2026-07",
        )
