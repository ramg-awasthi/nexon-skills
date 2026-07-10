from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path

from .common import (
    AGENT_MATCH_STATUS_VALUES,
    APPROVED_REFINED_COLUMNS,
    EXCLUDED_PHASE1_COLUMNS,
    HUMAN_VERIFIED_STATUS_VALUES,
    RUN_SUBDIRS,
    DEFAULT_CONFIG_PATH,
    ensure_db_update_disabled,
    evidence_summary_policy,
    load_config,
    read_json,
    validate_run_id,
)


SECRET_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"code=",
        r"sig=",
        r"client_secret",
        r"accountkey=",
        r"sharedaccesssignature",
        r"password\s*[:=]",
    )
]


def read_header(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        return next(reader)


def count_rows(path: Path) -> int:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return sum(1 for _ in reader)


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def assert_no_secret_markers(run_root: Path) -> None:
    scan_roots = [run_root / "manifest", run_root / "logs", run_root / "raw-recon-report", run_root / "refined-recon-report"]
    for root in scan_roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError as exc:
                raise RuntimeError(f"Unable to scan output for secrets: {path}") from exc
            for pattern in SECRET_PATTERNS:
                if pattern.search(text):
                    raise RuntimeError(f"Secret-like marker found in output artifact: {path}")


def assert_result_path_shape(run_root: Path, run_id: str) -> None:
    provider, ymd, _time, _hash5 = run_id.split("_")
    expected_year = ymd[:4]
    expected_month = ymd[4:6]
    actual_month = run_root.parent.name
    actual_year = run_root.parent.parent.name
    actual_provider = run_root.parent.parent.parent.name
    if actual_provider != provider or actual_year != expected_year or actual_month != expected_month:
        raise RuntimeError(
            "Run path must end with <provider>/<year>/<month>/<run_id>: "
            f"expected {provider}/{expected_year}/{expected_month}/{run_id}, got {run_root}"
        )


def assert_parser_warnings_resolved(run_root: Path, refined_rows: list[dict[str, str]]) -> None:
    warnings_path = run_root / "logs" / "parser_warnings.json"
    if not warnings_path.is_file():
        raise RuntimeError(f"Missing parser warnings artifact: {warnings_path}")
    warnings = read_json(warnings_path)
    if not isinstance(warnings, list):
        raise RuntimeError("Parser warnings artifact must be a list.")

    error_warnings = [warning for warning in warnings if isinstance(warning, dict) and warning.get("severity") == "error"]
    if error_warnings:
        raise RuntimeError("Parser error warnings block run validation.")


def assert_evidence_summary(row: dict[str, str], *, auto_matched_mode: str, max_chars: int) -> None:
    status = row.get("agent_match_status", "")
    summary = row.get("agent_evidence_summary", "").strip()
    required = status != "excluded" and not (status == "auto_matched" and auto_matched_mode == "blank")
    if required and not summary:
        raise RuntimeError("agent_evidence_summary is required for refined report rows.")
    if summary:
        if "\n" in summary or "\r" in summary:
            raise RuntimeError("agent_evidence_summary must be a single line.")
        if len(summary) > max_chars:
            raise RuntimeError(f"agent_evidence_summary exceeds max_chars={max_chars}.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a run package.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--run-root", type=Path, required=True)
    args = parser.parse_args()

    config = load_config(args.config)
    ensure_db_update_disabled(config)
    evidence_policy = evidence_summary_policy(config)

    run_id = args.run_root.name
    if not validate_run_id(run_id):
        raise RuntimeError(f"Invalid run_id: {run_id}")
    assert_result_path_shape(args.run_root, run_id)

    for subdir in RUN_SUBDIRS:
        path = args.run_root / subdir
        if not path.exists():
            raise RuntimeError(f"Missing run subdir: {path}")

    run_manifest_path = args.run_root / "manifest" / "run_manifest.json"
    report_manifest_path = args.run_root / "manifest" / "report_manifest.json"
    if not run_manifest_path.is_file():
        raise RuntimeError(f"Missing run manifest: {run_manifest_path}")
    if not report_manifest_path.is_file():
        raise RuntimeError(f"Missing report manifest: {report_manifest_path}")

    run_manifest = read_json(run_manifest_path)
    if run_manifest.get("run_id") != run_id:
        raise RuntimeError("Run manifest run_id does not match run folder name.")
    if run_manifest.get("db_update_enabled") is not False:
        raise RuntimeError("Run manifest does not prove report-only db_update_enabled=false mode.")

    audit_manifest_path = args.run_root / "manifest" / "audit_manifest.json"
    if audit_manifest_path.exists() and read_json(audit_manifest_path).get("db_update_attempted") is not False:
        raise RuntimeError("Audit manifest indicates DB update was attempted.")

    raw_reports = list((args.run_root / "raw-recon-report").glob("*.csv"))
    refined_reports = list((args.run_root / "refined-recon-report").glob("*.csv"))
    if not raw_reports:
        raise RuntimeError("Missing raw CSV report.")
    if not refined_reports:
        raise RuntimeError("Missing refined CSV report.")

    raw_count = count_rows(raw_reports[0])
    refined_rows = read_rows(refined_reports[0])
    if raw_count != len(refined_rows):
        raise RuntimeError(f"Raw/refined row count mismatch: raw={raw_count}, refined={len(refined_rows)}")
    report_manifest = read_json(report_manifest_path)
    if report_manifest.get("row_count") != raw_count:
        raise RuntimeError(
            f"Report manifest row_count does not match report rows: manifest={report_manifest.get('row_count')}, actual={raw_count}"
        )

    header = read_header(refined_reports[0])
    missing = [column for column in APPROVED_REFINED_COLUMNS if column not in header]
    forbidden = [column for column in EXCLUDED_PHASE1_COLUMNS if column in header]
    unexpected_review_columns = [
        column
        for column in header
        if (column.startswith("agent_") or column.startswith("human_")) and column not in APPROVED_REFINED_COLUMNS
    ]
    if missing:
        raise RuntimeError(f"Missing refined columns: {missing}")
    if forbidden:
        raise RuntimeError(f"Forbidden refined columns present: {forbidden}")
    if unexpected_review_columns:
        raise RuntimeError(f"Unexpected agent/human refined columns present: {unexpected_review_columns}")

    for row in refined_rows:
        status = row.get("agent_match_status", "")
        human_status = row.get("human_verified_status", "")
        if status not in AGENT_MATCH_STATUS_VALUES:
            raise RuntimeError(f"Invalid agent_match_status: {status}")
        if human_status not in HUMAN_VERIFIED_STATUS_VALUES:
            raise RuntimeError(f"Invalid human_verified_status: {human_status}")
        if human_status == "verified" and not row.get("human_verified_invoice_number", "").strip():
            raise RuntimeError("human_verified_invoice_number is required when human_verified_status=verified.")
        assert_evidence_summary(
            row,
            auto_matched_mode=evidence_policy["auto_matched"],
            max_chars=evidence_policy["max_chars"],
        )

    assert_parser_warnings_resolved(args.run_root, refined_rows)
    assert_no_secret_markers(args.run_root)

    print("Run validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
