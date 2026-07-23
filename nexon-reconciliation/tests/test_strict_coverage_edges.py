from __future__ import annotations

import builtins
import contextlib
import csv
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
import zipfile
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError

from openpyxl import Workbook


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from recon_core import apply_exception_investigation, billing_query, common, intake_run  # noqa: E402
from recon_core import notify_failure, optional_db_update, preflight_check, provider_api_download  # noqa: E402
from recon_core import record_failure, safe_unpack, sharepoint_connector, validate_run  # noqa: E402
from recon_core.common import APPROVED_REFINED_COLUMNS, EXCLUDED_PHASE1_COLUMNS, RUN_SUBDIRS, write_json  # noqa: E402
from recon_core.write_reports import BASE_COLUMNS, _write_workbook  # noqa: E402


def call_main(module: object, argv: list[object]) -> int:
    old_argv = sys.argv[:]
    sys.argv = [getattr(module, "__name__", "tool")] + [str(item) for item in argv]
    try:
        return module.main()
    finally:
        sys.argv = old_argv


def write_config(
    path: Path,
    *,
    db_update_enabled: bool = False,
    provider_api_enabled: bool = True,
    billing_query_enabled: bool = True,
    notification_enabled: bool = False,
    notify_operator: bool = False,
    notification_mode: str = "outlook",
    notification_content: str = "text_only",
    provider_api_adapters: str = "  equinix: true\n",
    db_update_policy: str = "",
) -> None:
    path.write_text(
        f"""
timezone: Australia/Sydney
providers:
  - AAPT
  - Telstra
  - Optus
  - Vocus
  - Megaport
  - Equinix
features:
  provider_api_enabled: {str(provider_api_enabled).lower()}
  billing_query_enabled: {str(billing_query_enabled).lower()}
  db_update_enabled: {str(db_update_enabled).lower()}
  failure_notifications_enabled: {str(notification_enabled).lower()}
provider_api_adapters:
{provider_api_adapters}billing:
  mode: read_only_sql
  agent_sql_allowed: true
  audit_required: true
reports:
  evidence_summary:
    auto_matched: short
    max_chars: 160
failure_handling:
  notify_operator: {str(notify_operator).lower()}
  notification_mode: {notification_mode}
  notification_content: {notification_content}
{db_update_policy}
""".lstrip(),
        encoding="utf-8",
    )


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def make_valid_run(root: Path, *, rows: list[dict[str, str]] | None = None, raw_count: int | None = None) -> Path:
    run_id = "AAPT_20260709_153012_A1B2C"
    run_root = root / "AAPT" / "2026" / "07" / run_id
    for subdir in RUN_SUBDIRS:
        (run_root / subdir).mkdir(parents=True, exist_ok=True)

    refined_header = BASE_COLUMNS + APPROVED_REFINED_COLUMNS
    if rows is None:
        row = {column: "" for column in refined_header}
        row["provider"] = "AAPT"
        row["line_id"] = "line-1"
        row["agent_match_status"] = "no_match"
        row["agent_evidence_summary"] = "No billing candidate was found."
        row["human_verified_status"] = "not_reviewed"
        rows = [row]

    if raw_count is None:
        raw_count = len(rows)
    for index, row in enumerate(rows):
        if not row.get("SupplierName"):
            row["SupplierName"] = "AAPT"
        if not row.get("InvoiceServiceNumber"):
            row["InvoiceServiceNumber"] = f"SVC-{index + 1}"

    normalized_lines = [
        {
            "line_id": f"line-{index + 1}",
            "invoice_identity": "invoice-1",
            "run_id": run_id,
            "provider": "AAPT",
        }
        for index in range(raw_count)
    ]
    raw_path = run_root / "raw-recon-report" / "raw-reconciliation.xlsx"
    refined_path = run_root / "refined-recon-report" / "refined-reconciliation.xlsx"
    logical_run_path = common.logical_sharepoint_run_path("AAPT", run_root)
    raw_runtime_rows = [
        (
            rows[index]
            if index < len(rows)
            else {
                **{column: "" for column in BASE_COLUMNS},
                "SupplierName": "AAPT",
                "InvoiceServiceNumber": f"SVC-{index + 1}",
            }
        )
        for index in range(raw_count)
    ]
    _write_workbook(
        raw_path,
        raw_runtime_rows,
        BASE_COLUMNS,
        run_path=logical_run_path,
        period="2026-07",
    )
    _write_workbook(
        refined_path,
        rows,
        refined_header,
        run_path=logical_run_path,
        period="2026-07",
    )

    write_json(
        run_root / "manifest" / "run_manifest.json",
        {"run_id": run_id, "db_update_enabled": False, "billing_period": "2026-07"},
    )
    write_json(
        run_root / "manifest" / "parser_manifest.json",
        {
            "provider": "AAPT",
            "run_id": run_id,
            "source_rows": raw_count,
            "parsed_rows": raw_count,
            "documented_exclusions": 0,
            "accounting_complete": True,
        },
    )
    write_json(
        run_root / "normalized" / "provider_lines.json",
        {"invoice_headers": [{"invoice_identity": "invoice-1"}], "lines": normalized_lines},
    )
    write_json(
        run_root / "manifest" / "report_manifest.json",
        {"row_count": raw_count, "raw_output": str(raw_path), "refined_output": str(refined_path)},
    )
    write_json(
        run_root / "evidence" / "billing_candidates.json",
        {"run_id": run_id, "provider": "AAPT", "candidates_by_line": {}},
    )
    write_json(
        run_root / "logs" / "billing_query_log.json",
        [{"sql_hash": "abc", "read_only_validation": "passed"}],
    )
    runtime_rows = [
        {**raw_runtime_rows[index], "line_id": line["line_id"]}
        for index, line in enumerate(normalized_lines)
    ]
    write_json(run_root / "normalized" / "match_results.json", {"rows": runtime_rows})
    write_json(
        run_root / "manifest" / "persistence_manifest.json",
        {
            "run_id": run_id,
            "provider": "AAPT",
            "transaction": "committed",
            "supplier_line_count": raw_count,
            "result_count": raw_count,
        },
    )
    write_json(run_root / "normalized" / "persisted_match_results.json", {"rows": runtime_rows})
    write_json(
        run_root / "manifest" / "audit_manifest.json",
        {
            "run_id": run_id,
            "accepted_resolution_update_attempted": False,
            "query_logs": [
                {
                    "sha256": common.sha256_file(
                        run_root / "logs" / "billing_query_log.json"
                    )
                }
            ],
        },
    )
    completed = {
        "source_staging",
        "run_creation",
        "archive_validation",
        "provider_parsing",
        "billing_preparation",
        "deterministic_comparison",
        "supplier_persistence",
        "result_persistence",
        "raw_workbook",
    }
    skipped = {"exception_investigation", "refined_workbook", "publication", "notification"}
    write_json(
        run_root / "manifest" / "run_state.json",
        {
            "run_id": run_id,
            "run_status": "running",
            "stages": {
                stage: {"status": "completed" if stage in completed else "skipped" if stage in skipped else "running"}
                for stage in common.RUN_STAGES
            },
        },
    )
    write_json(run_root / "logs" / "parser_warnings.json", [])

    return run_root


class StrictCoverageEdgeTests(unittest.TestCase):
    def test_exception_investigation_main_rejects_bad_match_and_update_shapes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            matches = root / "matches.json"
            investigation = root / "investigation.json"
            output = root / "output.json"
            matches.write_text(json.dumps({"rows": "bad"}), encoding="utf-8")
            investigation.write_text(json.dumps([]), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "rows list"):
                call_main(apply_exception_investigation, ["--matches", matches, "--investigation", investigation, "--output", output])

            matches.write_text(json.dumps({"rows": [{"line_id": "line-1"}]}), encoding="utf-8")
            investigation.write_text(json.dumps([1]), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "must be objects"):
                call_main(apply_exception_investigation, ["--matches", matches, "--investigation", investigation, "--output", output])

    def test_billing_query_defensive_helpers_and_postgres_paths(self) -> None:
        self.assertEqual(("select 1", "inline"), billing_query._load_sql("select 1", None))
        with self.assertRaisesRegex(RuntimeError, "sql_missing"):
            billing_query._load_sql(None, None)
        with self.assertRaisesRegex(RuntimeError, "SQL is empty"):
            billing_query._assert_read_only_sql("")
        with self.assertRaisesRegex(RuntimeError, "billing config must be a mapping"):
            billing_query._assert_billing_config({"features": {"billing_query_enabled": True}, "billing": []})

        self.assertIsNone(billing_query._parse_date(""))
        self.assertEqual(__import__("datetime").date(2026, 7, 9), billing_query._parse_date(__import__("datetime").date(2026, 7, 9)))
        self.assertIsNone(billing_query._parse_date("not-a-date"))
        self.assertTrue(billing_query._normalize_bool(True))
        self.assertFalse(billing_query._normalize_bool(None))
        self.assertFalse(billing_query._normalize_bool("no"))
        self.assertFalse(billing_query._date_in_window("", "2026-07-01", "2026-07-31"))
        self.assertEqual("select * from t where x=%(service_id)s", billing_query._postgres_sql("select * from t where x=:service_id"))
        scoped = billing_query._scoped_sql(
            "select service_id, provider, provider_account, transaction_date "
            "from Finance.GenericNexonBilling",
            "sqlserver",
        )
        self.assertIn("OPENJSON(:service_ids_json)", scoped)
        self.assertIn("candidate_scope.provider_account = :provider_account", scoped)
        with self.assertRaisesRegex(RuntimeError, "canonical candidate fields"):
            billing_query._assert_read_only_sql(
                "select 1 from Finance.GenericNexonBilling "
                "-- provider provider_account transaction_date service_id",
            )
        with self.assertRaisesRegex(RuntimeError, "unapproved tables"):
            billing_query._assert_read_only_sql(
                "select ServiceNumber as service_id, SupplierName as provider, "
                "AccountNumber as provider_account, BillingDate as transaction_date "
                "from Finance.GenericNexonBilling, UnauthorizedTable"
            )
        with self.assertRaisesRegex(RuntimeError, "source columns"):
            billing_query._assert_read_only_sql(
                "select ServiceNumber as service_id, 'AAPT' as provider, "
                "'ACC-1' as provider_account, BillingDate as transaction_date "
                "from Finance.GenericNexonBilling"
            )

        old_env = os.environ.copy()
        try:
            os.environ["NEXON_RECON_BILLING_DSN"] = "dsn"
            os.environ["NEXON_RECON_BILLING_MODE"] = "other"
            with self.assertRaisesRegex(RuntimeError, "Unsupported billing mode"):
                billing_query._execute_query("select 1", {})

            original_execute_postgres = billing_query._execute_postgres
            billing_query._execute_postgres = lambda dsn, sql, params, timeout_seconds, row_limit: [{"ok": True}]
            os.environ["NEXON_RECON_BILLING_MODE"] = "postgres"
            self.assertEqual([{"ok": True}], billing_query._execute_query("select 1", {}))
            billing_query._execute_postgres = original_execute_postgres
        finally:
            os.environ.clear()
            os.environ.update(old_env)

        old_import = builtins.__import__
        try:
            def fake_import(name: str, *args: object, **kwargs: object) -> object:
                if name == "psycopg":
                    raise ImportError("no psycopg")
                return old_import(name, *args, **kwargs)

            builtins.__import__ = fake_import
            with self.assertRaisesRegex(RuntimeError, "psycopg is required"):
                billing_query._execute_postgres(
                    "dsn", "select 1", {}, timeout_seconds=30, row_limit=100
                )
        finally:
            builtins.__import__ = old_import

        old_psycopg = sys.modules.get("psycopg")
        old_rows = sys.modules.get("psycopg.rows")
        try:
            class FakeCursor:
                def __enter__(self) -> "FakeCursor":
                    return self

                def __exit__(self, *_args: object) -> None:
                    return None

                def execute(self, sql: str, params: dict) -> None:
                    self.sql = sql
                    self.params = params

                def fetchmany(self, _limit: int) -> list[dict[str, str]]:
                    return [{"service_id": "SVC-1"}]

            class FakeConnection:
                def __enter__(self) -> "FakeConnection":
                    return self

                def __exit__(self, *_args: object) -> None:
                    return None

                def execute(self, _sql: str) -> None:
                    return None

                def cursor(self) -> FakeCursor:
                    return FakeCursor()

            sys.modules["psycopg"] = types.SimpleNamespace(connect=lambda dsn, row_factory=None: FakeConnection())
            sys.modules["psycopg.rows"] = types.SimpleNamespace(dict_row=object())
            self.assertEqual(
                [{"service_id": "SVC-1"}],
                billing_query._execute_postgres(
                    "dsn",
                    "select :service_id",
                    {"service_id": "SVC-1"},
                    timeout_seconds=30,
                    row_limit=100,
                ),
            )
        finally:
            if old_psycopg is None:
                sys.modules.pop("psycopg", None)
            else:
                sys.modules["psycopg"] = old_psycopg
            if old_rows is None:
                sys.modules.pop("psycopg.rows", None)
            else:
                sys.modules["psycopg.rows"] = old_rows

    def test_common_helpers_cover_config_validation_and_file_io(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_list = root / "bad.yaml"
            config_list.write_text("- not\n- mapping\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Config must be a mapping"):
                common.load_config(config_list)

            old_import = builtins.__import__
            try:
                def fake_import(name: str, *args: object, **kwargs: object) -> object:
                    if name == "yaml":
                        raise ImportError("no yaml")
                    return old_import(name, *args, **kwargs)

                builtins.__import__ = fake_import
                with self.assertRaisesRegex(RuntimeError, "PyYAML is required"):
                    common.load_config(config_list)
            finally:
                builtins.__import__ = old_import

            self.assertEqual({"auto_matched": "short", "max_chars": 160}, common.evidence_summary_policy({"reports": []}))
            self.assertEqual({"auto_matched": "short", "max_chars": 160}, common.evidence_summary_policy({"reports": {"evidence_summary": []}}))
            with self.assertRaisesRegex(ValueError, "max_chars must be an integer"):
                common.evidence_summary_policy({"reports": {"evidence_summary": {"max_chars": object()}}})
            self.assertEqual("abc", common.normalize_evidence_summary("abcdef", 3))
            with self.assertRaisesRegex(ValueError, "Unknown provider"):
                common.provider_slug("Unknown")
            created_at = datetime.fromisoformat("2026-07-09T15:30:12+10:00")
            timestamp = created_at.strftime("%Y%m%d_%H%M%S")
            seed = f"AAPT|source|{timestamp}"
            digest = __import__("hashlib").sha256(seed.encode("utf-8")).hexdigest().upper()
            run_parent = root / "runs"
            for offset in range(0, len(digest) - 4):
                (run_parent / f"AAPT_{timestamp}_{digest[offset : offset + 5]}").mkdir(parents=True, exist_ok=True)
            with self.assertRaisesRegex(RuntimeError, "Unable to resolve run ID collision"):
                common.resolve_run_id_collision("AAPT", "source", run_parent, created_at)

            source = root / "source.txt"
            source.write_text("hash me", encoding="utf-8")
            self.assertEqual(64, len(common.sha256_file(source)))
            with self.assertRaisesRegex(ValueError, "Provider is not supported"):
                common.ensure_provider({}, "Unknown")
            with self.assertRaisesRegex(ValueError, "Provider is not supported"):
                common.provider_api_adapter_enabled({}, "Unknown")
            self.assertFalse(common.provider_api_adapter_enabled({"provider_api_adapters": []}, "AAPT"))

            csv_path = root / "nested" / "rows.csv"
            common.write_csv(csv_path, [{"a": "1", "b": "2"}], ["a"])
            self.assertEqual("a\n1\n", csv_path.read_text(encoding="utf-8").replace("\r\n", "\n"))
            parser = common.config_arg()
            self.assertEqual(common.DEFAULT_CONFIG_PATH, parser.parse_args([]).config)

    def test_intake_move_and_missing_source_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "config.yaml"
            write_config(config)
            missing = root / "missing.csv"
            with self.assertRaises(FileNotFoundError):
                call_main(intake_run, ["--config", config, "--provider", "AAPT", "--source-file", missing, "--result-root", root / "result"])

            source = root / "invoice.csv"
            source.write_text("invoice", encoding="utf-8")
            (root / "result" / "AAPT").mkdir(parents=True)
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                self.assertEqual(
                    0,
                    call_main(
                        intake_run,
                        ["--config", config, "--provider", "AAPT", "--source-file", source, "--result-root", root / "result"],
                    ),
                )
            run_root = Path(stdout.getvalue().strip())
            self.assertFalse(source.exists())
            self.assertEqual("move", read_json(run_root / "manifest" / "run_manifest.json")["intake_action"])

    def test_notify_failure_rejects_non_outlook_or_attachment_modes_and_prints_disabled_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            failure_manifest = root / "failure.json"
            failure_manifest.write_text(json.dumps({"provider": "AAPT", "stage": "test", "reason": "boom"}), encoding="utf-8")

            disabled_config = root / "disabled.yaml"
            write_config(disabled_config)
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                self.assertEqual(0, call_main(notify_failure, ["--config", disabled_config, "--failure-manifest", failure_manifest]))
            self.assertEqual("disabled", json.loads(stdout.getvalue())["status"])

            bad_mode = root / "bad-mode.yaml"
            write_config(bad_mode, notification_enabled=True, notify_operator=True, notification_mode="smtp")
            with self.assertRaisesRegex(RuntimeError, "Unsupported failure notification mode"):
                call_main(notify_failure, ["--config", bad_mode, "--failure-manifest", failure_manifest])

            bad_content = root / "bad-content.yaml"
            write_config(bad_content, notification_enabled=True, notify_operator=True, notification_content="attachments")
            with self.assertRaisesRegex(RuntimeError, "Unsupported failure notification content"):
                call_main(notify_failure, ["--config", bad_content, "--failure-manifest", failure_manifest])

    def test_optional_db_update_defensive_paths(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "Invalid human_verified_status"):
            optional_db_update._build_update_plan([{"human_verified_status": "bad"}], {})
        skipped = optional_db_update._build_update_plan([{"human_verified_status": "not_reviewed"}], {})
        self.assertEqual(1, skipped["skipped_row_count"])
        rejected = optional_db_update._build_update_plan([{"human_verified_status": "rejected"}], {})
        self.assertEqual(1, rejected["skipped_row_count"])
        with self.assertRaisesRegex(RuntimeError, "requires human_verified_invoice_number"):
            optional_db_update._build_update_plan([{"human_verified_status": "verified"}], {})

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = root / "refined.xlsx"
            workbook = Workbook()
            sheet = workbook.active
            sheet.title = "Result"
            sheet.append(["line_id", "provider", "human_verified_status", "human_verified_invoice_number"])
            sheet.append(["line-1", "AAPT", "not_reviewed", ""])
            workbook.save(report)
            disabled = root / "disabled.yaml"
            write_config(disabled, db_update_enabled=False)
            with self.assertRaisesRegex(RuntimeError, "DB update is disabled"):
                call_main(optional_db_update, ["--config", disabled, "--refined-report", report, "--audit-output", root / "audit.json"])

            enabled = root / "enabled.yaml"
            write_config(enabled, db_update_enabled=True)
            with self.assertRaisesRegex(RuntimeError, "controlled approval artifact"):
                call_main(optional_db_update, ["--config", enabled, "--refined-report", report, "--audit-output", root / "audit.json"])
            approval = root / "approval.json"
            approval.write_text(
                json.dumps(
                    {
                        "run_id": "AAPT_20260709_153012_A1B2C",
                        "report_id": hashlib.sha256(report.read_bytes()).hexdigest(),
                        "approved_row_ids": ["line-1"],
                        "approved_by": "reviewer@nexon.com.au",
                        "approved_at": "2026-07-09T15:35:00+10:00",
                        "eligibility_policy_version": "accepted-resolution-v1",
                        "dry_run_hash": "dry-run-sha256",
                        "change_ticket": "CHG-1",
                        "batch_idempotency_key": "batch-1",
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "dry-run"):
                call_main(
                    optional_db_update,
                    [
                        "--config",
                        enabled,
                        "--refined-report",
                        report,
                        "--audit-output",
                        root / "audit.json",
                        "--approval-artifact",
                        approval,
                    ],
                )

    def test_preflight_config_shapes_and_local_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bad_shape = root / "bad-shape.yaml"
            write_config(bad_shape, provider_api_adapters="  - equinix\n")
            with self.assertRaisesRegex(RuntimeError, "must be a mapping"):
                call_main(preflight_check, ["--config", bad_shape])

            false_adapter = root / "false-adapter.yaml"
            write_config(false_adapter, provider_api_adapters="  equinix: false\n")
            with self.assertRaisesRegex(RuntimeError, "Remove disabled"):
                call_main(preflight_check, ["--config", false_adapter])

            valid_config = root / "valid.yaml"
            write_config(valid_config)
            upload_root = root / "upload"
            result_root = root / "result"
            for provider in common.PROVIDERS:
                (upload_root / provider).mkdir(parents=True, exist_ok=True)
                (result_root / provider).mkdir(parents=True, exist_ok=True)

            old_roots = preflight_check.sharepoint_roots
            try:
                preflight_check.sharepoint_roots = lambda config: (upload_root, result_root)
                stdout = io.StringIO()
                with contextlib.redirect_stdout(stdout):
                    self.assertEqual(0, call_main(preflight_check, ["--config", valid_config, "--local-check"]))
                self.assertIn("Local setup validation passed", stdout.getvalue())
            finally:
                preflight_check.sharepoint_roots = old_roots

    def test_provider_api_http_failures_token_shape_and_direct_unimplemented_branch(self) -> None:
        original_urlopen = provider_api_download.urlopen
        try:
            def raise_auth_error(*_args: object, **_kwargs: object) -> object:
                raise HTTPError("https://example.test/token", 401, "no", {}, io.BytesIO(b"bad auth"))

            provider_api_download.urlopen = raise_auth_error
            with self.assertRaisesRegex(RuntimeError, "auth failed"):
                provider_api_download._http_json("https://example.test/token", {})

            def raise_download_error(*_args: object, **_kwargs: object) -> object:
                raise HTTPError("https://example.test/invoice", 500, "no", {}, io.BytesIO(b"bad download"))

            provider_api_download.urlopen = raise_download_error
            with self.assertRaisesRegex(RuntimeError, "download failed"):
                provider_api_download._http_download("https://example.test/invoice", "token", Path("x"))
        finally:
            provider_api_download.urlopen = original_urlopen

        old_env = os.environ.copy()
        original_http_json = provider_api_download._http_json
        try:
            os.environ["NEXON_RECON_PROVIDER_API_CLIENT_ID_EQUINIX"] = "client"
            os.environ["NEXON_RECON_PROVIDER_API_CLIENT_SECRET_EQUINIX"] = "secret"
            provider_api_download._http_json = lambda *_args, **_kwargs: {}
            with self.assertRaisesRegex(RuntimeError, "access_token"):
                provider_api_download._equinix_download(
                    SimpleNamespace(account_id="acct", invoice_id="inv", document_id=None, output_dir=None, output_name=None, manifest=None),
                    {"provider": "Equinix"},
                )
        finally:
            provider_api_download._http_json = original_http_json
            os.environ.clear()
            os.environ.update(old_env)

        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.yaml"
            write_config(config, provider_api_adapters="  equinix: true\n  aapt: true\n")
            with self.assertRaisesRegex(NotImplementedError, "integration_unavailable"):
                call_main(provider_api_download, ["--config", config, "--provider", "AAPT"])

            original_equinix_download = provider_api_download._equinix_download
            try:
                provider_api_download._equinix_download = lambda args, provider_config: Path("downloaded.pdf")
                stdout = io.StringIO()
                with contextlib.redirect_stdout(stdout):
                    self.assertEqual(0, call_main(provider_api_download, ["--config", config, "--provider", "Equinix"]))
                self.assertEqual("downloaded.pdf", stdout.getvalue().strip())
            finally:
                provider_api_download._equinix_download = original_equinix_download

        original_load_config = provider_api_download.load_config
        try:
            provider_api_download.load_config = lambda _path: {
                "features": {"provider_api_enabled": True},
                "provider_api_adapters": {"aapt": True},
            }
            with self.assertRaisesRegex(NotImplementedError, "integration_unavailable"):
                call_main(provider_api_download, ["--config", "unused.yaml", "--provider", "AAPT"])
        finally:
            provider_api_download.load_config = original_load_config

    def test_record_failure_default_output_and_safe_unpack_success_and_read_failures(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "config.yaml"
            write_config(config)

            old_cwd = Path.cwd()
            try:
                os.chdir(root)
                stdout = io.StringIO()
                with contextlib.redirect_stdout(stdout):
                    self.assertEqual(0, call_main(record_failure, ["--config", config, "--provider", "AAPT", "--stage", "parse", "--reason", "bad"]))
                self.assertEqual("failure_manifest.json", stdout.getvalue().strip())
                self.assertTrue((root / "failure_manifest.json").is_file())
            finally:
                os.chdir(old_cwd)

            archive = root / "ok.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("folder/", "")
                zf.writestr("folder/invoice.csv", "invoice")
            manifest = root / "manifest.json"
            self.assertEqual(
                0,
                call_main(safe_unpack, ["--zip", archive, "--output-dir", root / "out", "--manifest", manifest]),
            )
            self.assertEqual(1, len(read_json(manifest)["members"]))

            bad_zip = root / "bad.zip"
            bad_zip.write_text("not zip", encoding="utf-8")
            self.assertTrue(safe_unpack.extract_zip(bad_zip, root / "bad-out")["blocked"])

            original_zipfile = safe_unpack.zipfile.ZipFile
            try:
                safe_unpack.zipfile.ZipFile = lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk read failed"))
                blocked = safe_unpack.extract_zip(archive, root / "err-out")["blocked"]
            finally:
                safe_unpack.zipfile.ZipFile = original_zipfile
            self.assertIn("Archive read failed", blocked[0]["reason"])

    def test_sharepoint_graph_and_local_edge_paths_without_network(self) -> None:
        old_env = os.environ.copy()
        os.environ["NEXON_RECON_GRAPH_ACCESS_TOKEN"] = "token"
        os.environ["NEXON_RECON_SHAREPOINT_DRIVE_ID"] = "drive"
        original_urlopen = sharepoint_connector.urlopen
        original_graph_json = sharepoint_connector._graph_json
        original_get_item = sharepoint_connector._get_item
        original_ensure_folder = sharepoint_connector._ensure_folder
        original_sharepoint_roots = sharepoint_connector.sharepoint_roots
        try:
            def raise_graph_error(*_args: object, **_kwargs: object) -> object:
                raise HTTPError("https://graph.test", 403, "no", {}, io.BytesIO(b"denied"))

            sharepoint_connector.urlopen = raise_graph_error
            with self.assertRaisesRegex(RuntimeError, "SharePoint Graph request failed"):
                sharepoint_connector._graph_request("GET", "/me")

            class FakeResponse:
                def __enter__(self) -> "FakeResponse":
                    return self

                def __exit__(self, *_args: object) -> None:
                    return None

                def read(self) -> bytes:
                    return b"{}"

            sharepoint_connector.urlopen = lambda *_args, **_kwargs: FakeResponse()
            self.assertEqual(b"{}", sharepoint_connector._graph_request("PUT", "/upload", b"payload", "application/octet-stream"))

            sharepoint_connector._graph_json = lambda method, path, body=None: {"value": [{"name": "a.csv", "file": {}, "id": "1", "size": 1}]}
            self.assertEqual({"value": [{"name": "a.csv", "file": {}, "id": "1", "size": 1}]}, sharepoint_connector._get_item("/x"))
            self.assertEqual(1, len(sharepoint_connector._children("/x")))

            def fake_get_item(path: str) -> dict:
                if path == "new":
                    raise RuntimeError("missing")
                return {"id": f"id-{path}"}

            created: list[dict] = []
            sharepoint_connector._get_item = fake_get_item
            sharepoint_connector._graph_json = lambda method, path, body=None: created.append({"method": method, "path": path, "body": body}) or {"id": "created"}
            self.assertEqual({"id": "id-new/child"}, sharepoint_connector._ensure_folder("new/child"))
            self.assertEqual("POST", created[0]["method"])

            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                output = root / "spaces.json"
                config = {"provider_api_adapters": {"aapt": True}}
                sharepoint_connector.sharepoint_roots = lambda config: (
                    root / "upload",
                    root / "result",
                )
                args = SimpleNamespace(provider="AAPT", mode="local", output=output)
                self.assertEqual(2, sharepoint_connector.check_spaces(args, config))
                self.assertEqual("setup_incomplete", read_json(output)["status"])

                upload_root = root / "upload"
                result_root = root / "result"
                (upload_root / "AAPT").mkdir(parents=True, exist_ok=True)
                (result_root / "AAPT").mkdir(parents=True, exist_ok=True)
                source = upload_root / "AAPT" / "invoice.csv"
                source.write_text("invoice", encoding="utf-8")
                old_roots = sharepoint_connector.sharepoint_roots
                sharepoint_connector.sharepoint_roots = lambda config: (upload_root, result_root)
                try:
                    output = root / "move.json"
                    args = SimpleNamespace(provider="AAPT", mode="local", source_name="invoice.csv", run_root=str(root / "run"), copy=True, output=output)
                    self.assertEqual(0, sharepoint_connector.move_upload_to_run_source(args, config))
                    self.assertEqual("copied", read_json(output)["status"])
                finally:
                    sharepoint_connector.sharepoint_roots = old_roots

            sharepoint_connector._get_item = lambda path: {"id": "source-id"}
            sharepoint_connector._ensure_folder = lambda path: {"id": "parent-id"}
            sharepoint_connector._graph_json = lambda method, path, body=None: {"async": True, "method": method, "path": path, "body": body}
            with tempfile.TemporaryDirectory() as tmp:
                output = Path(tmp) / "graph-move.json"
                args = SimpleNamespace(provider="AAPT", mode="graph", source_name="invoice.csv", run_root="/result/AAPT/2026/07/run", copy=True, output=output)
                self.assertEqual(0, sharepoint_connector.move_upload_to_run_source(args, {"provider_api_adapters": {"aapt": True}}))
                self.assertEqual("copy_started", read_json(output)["status"])
        finally:
            sharepoint_connector.urlopen = original_urlopen
            sharepoint_connector._graph_json = original_graph_json
            sharepoint_connector._get_item = original_get_item
            sharepoint_connector._ensure_folder = original_ensure_folder
            sharepoint_connector.sharepoint_roots = original_sharepoint_roots
            os.environ.clear()
            os.environ.update(old_env)

    def test_validate_run_remaining_failure_edges(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "config.yaml"
            write_config(config)

            with self.assertRaisesRegex(RuntimeError, "Invalid run_id"):
                call_main(validate_run, ["--config", config, "--run-root", root / "bad"])

            run_root = make_valid_run(root / "missing-subdir")
            (run_root / "source").rmdir()
            with self.assertRaisesRegex(RuntimeError, "Missing run subdir"):
                call_main(validate_run, ["--config", config, "--run-root", run_root])

            run_root = make_valid_run(root / "missing-run-manifest")
            (run_root / "manifest" / "run_manifest.json").unlink()
            with self.assertRaisesRegex(RuntimeError, "Missing run manifest"):
                call_main(validate_run, ["--config", config, "--run-root", run_root])

            run_root = make_valid_run(root / "missing-report-manifest")
            (run_root / "manifest" / "report_manifest.json").unlink()
            with self.assertRaisesRegex(RuntimeError, "Missing report manifest"):
                call_main(validate_run, ["--config", config, "--run-root", run_root])

            run_root = make_valid_run(root / "audit-attempt")
            write_json(
                run_root / "manifest" / "audit_manifest.json",
                {
                    "run_id": run_root.name,
                    "accepted_resolution_update_attempted": True,
                },
            )
            with self.assertRaisesRegex(RuntimeError, "accepted-resolution update was attempted"):
                call_main(validate_run, ["--config", config, "--run-root", run_root])

            run_root = make_valid_run(root / "missing-raw")
            next((run_root / "raw-recon-report").glob("*.xlsx")).unlink()
            with self.assertRaisesRegex(RuntimeError, "Raw workbook is missing"):
                call_main(validate_run, ["--config", config, "--run-root", run_root])

            run_root = make_valid_run(root / "missing-refined")
            next((run_root / "refined-recon-report").glob("*.xlsx")).unlink()
            with self.assertRaisesRegex(RuntimeError, "Refined workbook is missing"):
                call_main(validate_run, ["--config", config, "--run-root", run_root])

            run_root = make_valid_run(root / "row-mismatch", raw_count=2)
            with self.assertRaisesRegex(RuntimeError, "Raw/refined workbook row counts differ"):
                call_main(validate_run, ["--config", config, "--run-root", run_root])

            run_root = make_valid_run(root / "missing-column")
            refined = next((run_root / "refined-recon-report").glob("*.xlsx"))
            _write_workbook(
                refined,
                [{"line_id": "1", "agent_match_status": "no_match", "human_verified_status": "not_reviewed", "agent_evidence_summary": "evidence"}],
                ["line_id", "agent_match_status", "human_verified_status", "agent_evidence_summary"],
                run_path=common.logical_sharepoint_run_path("AAPT", run_root),
                period="2026-07",
            )
            with self.assertRaisesRegex(RuntimeError, "Refined Result columns"):
                call_main(validate_run, ["--config", config, "--run-root", run_root])

            run_root = make_valid_run(root / "forbidden-column")
            refined = next((run_root / "refined-recon-report").glob("*.xlsx"))
            header = BASE_COLUMNS + APPROVED_REFINED_COLUMNS + [next(iter(EXCLUDED_PHASE1_COLUMNS))]
            row = {column: "" for column in header}
            row["agent_match_status"] = "no_match"
            row["agent_evidence_summary"] = "No billing candidate was found."
            row["human_verified_status"] = "not_reviewed"
            _write_workbook(
                refined,
                [row],
                header,
                run_path=common.logical_sharepoint_run_path("AAPT", run_root),
                period="2026-07",
            )
            with self.assertRaisesRegex(RuntimeError, "Refined Result columns"):
                call_main(validate_run, ["--config", config, "--run-root", run_root])

            run_root = make_valid_run(root / "unexpected-column")
            refined = next((run_root / "refined-recon-report").glob("*.xlsx"))
            header = BASE_COLUMNS + APPROVED_REFINED_COLUMNS + ["agent_surprise"]
            row = {column: "" for column in header}
            row["agent_match_status"] = "no_match"
            row["agent_evidence_summary"] = "No billing candidate was found."
            row["human_verified_status"] = "not_reviewed"
            _write_workbook(
                refined,
                [row],
                header,
                run_path=common.logical_sharepoint_run_path("AAPT", run_root),
                period="2026-07",
            )
            with self.assertRaisesRegex(RuntimeError, "Refined Result columns"):
                call_main(validate_run, ["--config", config, "--run-root", run_root])

            bad_agent_row = {column: "" for column in BASE_COLUMNS + APPROVED_REFINED_COLUMNS}
            bad_agent_row["agent_match_status"] = "bad"
            bad_agent_row["agent_evidence_summary"] = "Bad status."
            bad_agent_row["human_verified_status"] = "not_reviewed"
            run_root = make_valid_run(root / "bad-agent", rows=[bad_agent_row])
            with self.assertRaisesRegex(RuntimeError, "Invalid agent_match_status"):
                call_main(validate_run, ["--config", config, "--run-root", run_root])

            bad_human_row = {column: "" for column in BASE_COLUMNS + APPROVED_REFINED_COLUMNS}
            bad_human_row["agent_match_status"] = "no_match"
            bad_human_row["agent_evidence_summary"] = "No billing candidate was found."
            bad_human_row["human_verified_status"] = "bad"
            run_root = make_valid_run(root / "bad-human", rows=[bad_human_row])
            with self.assertRaisesRegex(RuntimeError, "Invalid human_verified_status"):
                call_main(validate_run, ["--config", config, "--run-root", run_root])

            secret_root = make_valid_run(root / "secret-scan")
            (secret_root / "logs" / "secret.txt").write_text("password: nope", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "Secret-like marker"):
                validate_run.assert_no_secret_markers(secret_root)

            read_error_root = make_valid_run(root / "read-error")
            original_read_text = Path.read_text
            try:
                Path.read_text = lambda self, *args, **kwargs: (_ for _ in ()).throw(OSError("locked"))
                with self.assertRaisesRegex(RuntimeError, "Unable to scan output"):
                    validate_run.assert_no_secret_markers(read_error_root)
            finally:
                Path.read_text = original_read_text


if __name__ == "__main__":
    unittest.main()
