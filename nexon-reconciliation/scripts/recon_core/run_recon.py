from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .common import (
    DEFAULT_CONFIG_PATH,
    INVESTIGATOR_MATCH_STATUS_VALUES,
    ensure_db_update_disabled,
    load_config,
    logical_sharepoint_run_path,
    read_json,
    require_audit,
    sha256_file,
    write_json,
)
from .core_persistence import persist_shadow_run
from .db_mcp_handoff import (
    consume_billing_query_receipts,
    consume_persistence_receipt,
    prepare_billing_query_plan,
    prepare_persistence_request,
    validate_database_mcp,
    write_billing_outputs,
)
from .fetch_intake_artifact import verify_receipt_attestation
from .intake_run import create_run
from .match_recon import classify_line
from .preflight_check import capability_manifest, execution_policy
from .run_state import create_state, finalize_state, update_stage
from .safe_unpack import extract_zip
from .sqlserver_persistence import persist_sqlserver_run
from .validate_run import validate_run
from .write_reports import write_reports


DOWNLOAD_RECEIPT_FIELDS = {
    "contract_version",
    "status",
    "environment",
    "provider",
    "space",
    "source_name",
    "local_path",
    "byte_count",
    "sha256",
    "index",
    "preparation_receipt_sha256",
    "downloaded_at",
    "attestation",
}
DOWNLOAD_INDEX_FIELDS = {"index_id", "index_sha256", "relative_path"}
PUBLICATION_RECEIPT_FIELDS = {
    "contract_version",
    "status",
    "run_id",
    "uploaded_artifacts",
    "source_move_receipt",
}
PUBLISHED_ARTIFACT_FIELDS = {"status", "local_path", "relative_path", "sha256"}
SOURCE_MOVE_FIELDS = {"status", "source_name", "relative_path", "sha256"}


def _run_command(command: list[str]) -> None:
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"tool_failed[{Path(command[1]).name}]: {detail}")


def _validate_provider_api_provenance(args: argparse.Namespace) -> None:
    if args.provider_api_manifest is None:
        raise RuntimeError("provider_api_provenance_missing: --provider-api-manifest is required.")
    manifest = read_json(args.provider_api_manifest)
    if manifest.get("provider") != args.provider:
        raise RuntimeError("provider_api_provenance_invalid: manifest provider does not match.")
    if not args.provider_api_account_id or manifest.get("account_id") != args.provider_api_account_id:
        raise RuntimeError("provider_api_provenance_invalid: provider account does not match approved input.")
    manifest_document = str(manifest.get("document_id") or "")
    if manifest_document and manifest_document != str(args.provider_api_document_id or ""):
        raise RuntimeError("provider_api_provenance_invalid: provider document does not match approved input.")
    output_file = Path(str(manifest.get("output_file") or "")).resolve()
    if args.source_file is None or output_file != args.source_file.resolve():
        raise RuntimeError("provider_api_provenance_invalid: manifest output file does not match source.")
    if manifest.get("output_sha256") != sha256_file(args.source_file):
        raise RuntimeError("provider_api_provenance_invalid: source checksum does not match manifest.")
    provider_identity = str(
        manifest.get("invoice_id") or manifest.get("document_id") or ""
    ).strip()
    if not provider_identity or args.source_identity != provider_identity:
        raise RuntimeError(
            "provider_api_provenance_invalid: --source-identity must match the provider invoice identity."
        )


def _paths() -> tuple[Path, Path]:
    skills_root = Path(__file__).resolve().parents[3]
    parser_cli = skills_root / "nexon-telco-parsers" / "scripts" / "parse_provider_invoice.py"
    billing_cli = Path(__file__).resolve().parents[1] / "billing_query.py"
    return parser_cli, billing_cli


def _audit(run_root: Path, *, run_id: str, source_checksum: str) -> Path:
    path = run_root / "manifest" / "audit_manifest.json"
    write_json(
        path,
        {
            "contract_version": 1,
            "run_id": run_id,
            "source_checksum_sha256": source_checksum,
            "audit_required": True,
            "accepted_resolution_update_attempted": False,
            "query_logs": [],
            "approval_artifact": None,
        },
    )
    return path


def _parser_command(
    *,
    parser_cli: Path,
    config_path: Path,
    provider: str,
    run_root: Path,
    input_dir: Path,
) -> list[str]:
    return [
        sys.executable,
        str(parser_cli),
        "--config",
        str(config_path),
        "--provider",
        provider,
        "--input-dir",
        str(input_dir),
        "--output",
        str(run_root / "normalized" / "provider_lines.json"),
        "--warnings",
        str(run_root / "logs" / "parser_warnings.json"),
        "--manifest",
        str(run_root / "manifest" / "parser_manifest.json"),
        "--run-id",
        run_root.name,
    ]


def _billing_command(
    *,
    billing_cli: Path,
    config_path: Path,
    run_root: Path,
    sql_file: Path,
) -> list[str]:
    return [
        sys.executable,
        str(billing_cli),
        "--config",
        str(config_path),
        "--normalized",
        str(run_root / "normalized" / "provider_lines.json"),
        "--sql-file",
        str(sql_file),
        "--output",
        str(run_root / "evidence" / "billing_candidates.json"),
        "--query-log",
        str(run_root / "logs" / "billing_query_log.json"),
    ]


def _logical_run_path(provider: str, run_root: Path) -> str:
    return logical_sharepoint_run_path(provider, run_root)


def _verify_download_receipt(
    *,
    receipt_path: Path,
    provider: str,
    allowed_spaces: tuple[str, ...],
    expected_file: Path | None = None,
    expected_relative_path: str | None = None,
    expected_environment: str | None = None,
    expected_attestation_public_key: str | None = None,
    forbidden_root: Path | None = None,
) -> dict[str, Any]:
    receipt = read_json(receipt_path)
    if not isinstance(receipt, dict) or set(receipt) != DOWNLOAD_RECEIPT_FIELDS:
        raise RuntimeError(
            "download_receipt_invalid: receipt fields do not match the contract."
        )
    if (
        receipt.get("contract_version") != 1
        or receipt.get("status") != "downloaded"
        or (
            expected_environment is not None
            and receipt.get("environment") != expected_environment
        )
        or receipt.get("provider") != provider
        or receipt.get("space") not in allowed_spaces
    ):
        raise RuntimeError(
            "download_receipt_invalid: version, status, provider, or space does not match."
        )
    local_path = Path(str(receipt.get("local_path") or "")).resolve()
    source_name = str(receipt.get("source_name") or "")
    if (
        not local_path.is_file()
        or local_path.name != source_name
        or (expected_file is not None and local_path != expected_file.resolve())
    ):
        raise RuntimeError(
            "download_receipt_invalid: local artifact identity does not match."
        )
    if forbidden_root is not None and local_path.is_relative_to(
        forbidden_root.resolve()
    ):
        raise RuntimeError(
            "download_receipt_invalid: verification download must be outside the run folder."
        )
    checksum = sha256_file(local_path)
    if (
        receipt.get("byte_count") != local_path.stat().st_size
        or receipt.get("sha256") != checksum
    ):
        raise RuntimeError(
            "download_receipt_invalid: local size or checksum does not match."
        )
    index = receipt.get("index")
    if (
        not isinstance(index, dict)
        or set(index) != DOWNLOAD_INDEX_FIELDS
        or not str(index.get("index_id") or "").strip()
        or len(str(index.get("index_sha256") or "")) != 64
        or not str(index.get("relative_path") or "").strip()
    ):
        raise RuntimeError(
            "download_receipt_invalid: sanitized index identity is incomplete."
        )
    if (
        expected_relative_path is not None
        and index.get("relative_path") != expected_relative_path
    ):
        raise RuntimeError(
            "download_receipt_invalid: indexed relative path does not match."
        )
    if (
        len(str(receipt.get("preparation_receipt_sha256") or "")) != 64
        or not str(receipt.get("downloaded_at") or "").strip()
    ):
        raise RuntimeError(
            "download_receipt_invalid: preparation identity or timestamp is missing."
        )
    verify_receipt_attestation(
        receipt,
        expected_public_key=expected_attestation_public_key,
    )
    return receipt


def _sharepoint_capability_identity(
    path: Path,
    config: dict[str, Any],
) -> tuple[str, str]:
    envelope = read_json(path)
    result = envelope.get("result") if isinstance(envelope, dict) else None
    attestation = result.get("attestation") if isinstance(result, dict) else None
    intake = config.get("sharepoint_intake", {})
    expected_environment = (
        str(intake.get("environment") or "").strip().lower()
        if isinstance(intake, dict)
        else ""
    )
    if (
        not isinstance(attestation, dict)
        or result.get("environment") != expected_environment
        or attestation.get("algorithm") != "Ed25519"
        or not str(attestation.get("public_key") or "")
    ):
        raise RuntimeError(
            "sharepoint_mcp_capability_invalid: environment or attestation key is invalid."
        )
    return expected_environment, str(attestation["public_key"])


def _freeze_download_receipt(
    run_root: Path, receipt: dict[str, Any]
) -> str:
    frozen_path = run_root / "manifest" / "source_download_receipt.json"
    write_json(frozen_path, receipt)
    return sha256_file(frozen_path)


def _unresolved_payload(
    rows: list[dict[str, Any]],
    candidates: dict[str, Any],
    *,
    run_id: str,
    parser_warnings: list[dict[str, Any]],
    query_log_identity: dict[str, Any],
    remaining_query_rounds: int,
) -> dict[str, Any]:
    unresolved = [row for row in rows if row.get("ReconMatchStatus") != "Matched"]
    by_line = candidates.get("candidates_by_line", {})
    return {
        "run_id": run_id,
        "parser_warnings": parser_warnings,
        "query_log_identity": query_log_identity,
        "remaining_query_rounds": remaining_query_rounds,
        "rows": unresolved,
        "candidates_by_line": {
            str(row.get("line_id")): by_line.get(str(row.get("line_id")), [])
            for row in unresolved
        },
    }


def _apply_investigation(
    match_rows: list[dict[str, Any]], investigation: dict[str, Any], *, expected_run_id: str
) -> list[dict[str, Any]]:
    if investigation.get("run_id") != expected_run_id:
        raise RuntimeError("investigation_invalid: run_id does not match the run being resumed.")
    updates = investigation.get("rows", investigation.get("investigations", []))
    if not isinstance(updates, list):
        raise RuntimeError("investigation_invalid: expected rows list.")
    update_by_line: dict[str, dict[str, Any]] = {}
    for update in updates:
        line_id = str(update.get("line_id") or "")
        if not line_id or line_id in update_by_line:
            raise RuntimeError("investigation_invalid: line_id is missing or duplicated.")
        update_by_line[line_id] = update
    output: list[dict[str, Any]] = []
    expected = {str(row.get("line_id")) for row in match_rows if row.get("ReconMatchStatus") != "Matched"}
    if set(update_by_line) != expected:
        raise RuntimeError("investigation_invalid: updates must cover exactly the unresolved line identities.")
    allowed = {
        "agent_match_status",
        "agent_match_rule",
        "agent_suggested_customer_account",
        "agent_suggested_subscription_id",
        "agent_suggested_invoice_number",
        "agent_suggested_service_id",
        "agent_evidence_summary",
        "agent_review_required",
    }
    for row in match_rows:
        merged = dict(row)
        update = update_by_line.get(str(row.get("line_id")))
        if update:
            extra = set(update) - allowed - {"line_id", "run_id"}
            if extra:
                raise RuntimeError(f"investigation_invalid: disallowed fields {sorted(extra)}")
            if not str(update.get("agent_evidence_summary") or "").strip():
                raise RuntimeError("investigation_invalid: evidence summary is required.")
            if update.get("agent_match_status") not in INVESTIGATOR_MATCH_STATUS_VALUES:
                raise RuntimeError("investigation_invalid: unsupported agent_match_status.")
            merged.update({key: value for key, value in update.items() if key in allowed})
            merged["agent_review_required"] = True
        output.append(merged)
    return output


def _complete_validation(run_root: Path, config: dict[str, Any], state_path: Path) -> dict[str, Any]:
    update_stage(state_path, "notification", "skipped")
    update_stage(state_path, "validation", "running")
    result = validate_run(run_root, config, "reconciliation")
    update_stage(state_path, "validation", "completed")
    finalize_state(state_path, "completed")
    result["run_status"] = "completed"
    result["run_root"] = str(run_root)
    return result


def _publishable_artifacts(run_root: Path) -> list[Path]:
    mutable = {
        run_root / "manifest" / "run_state.json",
        run_root / "manifest" / "audit_manifest.json",
        run_root / "manifest" / "publication_receipt.json",
        run_root / "manifest" / "publication_verification_receipts.json",
        run_root / "manifest" / "publication_set.json",
        run_root / "manifest" / "notification_receipt.json",
        run_root / "manifest" / "failure_manifest.json",
    }
    run_manifest = read_json(run_root / "manifest" / "run_manifest.json")
    manual_source = (
        run_root / "source"
        if run_manifest.get("intake_mode") == "manual_upload"
        else None
    )
    return sorted(
        path
        for path in run_root.rglob("*")
        if path.is_file()
        and path not in mutable
        and (run_root / "extracted") not in path.parents
        and (manual_source is None or manual_source not in path.parents)
    )


def _await_publication(run_root: Path, state_path: Path) -> dict[str, Any]:
    artifacts = _publishable_artifacts(run_root)
    frozen = [
        {
            "local_path": str(path.resolve()),
            "relative_path": path.relative_to(run_root).as_posix(),
            "sha256": sha256_file(path),
        }
        for path in artifacts
    ]
    publication_set = run_root / "manifest" / "publication_set.json"
    write_json(publication_set, {"run_id": run_root.name, "artifacts": frozen})
    update_stage(
        state_path,
        "publication",
        "running",
        artifacts=[str(publication_set)],
    )
    return {
        "run_id": run_root.name,
        "run_root": str(run_root),
        "status": "awaiting_publication",
        "artifacts_to_publish": frozen,
    }


def _record_billing_outputs(
    *,
    run_root: Path,
    state_path: Path,
    candidates: dict[str, Any],
    query_log: list[dict[str, Any]],
) -> tuple[Path, Path]:
    candidates_path, query_log_path = write_billing_outputs(
        run_root=run_root,
        candidates=candidates,
        query_log=query_log,
    )
    audit_path = run_root / "manifest" / "audit_manifest.json"
    audit = read_json(audit_path)
    audit["query_logs"] = [
        {
            "path": str(query_log_path),
            "sha256": sha256_file(query_log_path),
            "chunk_count": len(query_log),
        }
    ]
    write_json(audit_path, audit)
    update_stage(
        state_path,
        "billing_preparation",
        "completed",
        counts={"query_chunks": len(query_log)},
        artifacts=[str(candidates_path), str(query_log_path)],
    )
    return candidates_path, query_log_path


def _record_database_handoff(
    run_root: Path,
    *,
    name: str,
    request_path: Path,
    receipt_paths: list[Path],
) -> None:
    audit_path = run_root / "manifest" / "audit_manifest.json"
    audit = read_json(audit_path)
    handoffs = audit.setdefault("database_handoffs", {})
    handoffs[name] = {
        "request_sha256": sha256_file(request_path),
        "receipt_sha256": [sha256_file(path) for path in receipt_paths],
    }
    write_json(audit_path, audit)


def _perform_matching(
    *,
    run_root: Path,
    state_path: Path,
    config: dict[str, Any],
    normalized: dict[str, Any],
    candidates: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    update_stage(state_path, "deterministic_comparison", "running")
    candidate_map = candidates.get("candidates_by_line", {})
    match_rows = [
        classify_line(line, candidate_map.get(str(line.get("line_id")), []))
        for line in normalized.get("lines", [])
    ]
    matches_path = run_root / "normalized" / "match_results.json"
    write_json(matches_path, {"rows": match_rows})
    query_log_path = run_root / "logs" / "billing_query_log.json"
    query_log = read_json(query_log_path)
    unresolved = _unresolved_payload(
        match_rows,
        candidates,
        run_id=run_root.name,
        parser_warnings=read_json(run_root / "logs" / "parser_warnings.json"),
        query_log_identity={
            "path": str(query_log_path),
            "sha256": sha256_file(query_log_path),
            "chunk_count": len(query_log),
        },
        remaining_query_rounds=int(
            config.get("limits", {}).get("investigation_query_rounds", 2)
        ),
    )
    unresolved_path = run_root / "evidence" / "exception_input.json"
    write_json(unresolved_path, unresolved)
    update_stage(
        state_path,
        "deterministic_comparison",
        "completed",
        counts={
            "matched_rows": len(match_rows) - len(unresolved["rows"]),
            "unresolved_rows": len(unresolved["rows"]),
        },
        artifacts=[str(matches_path), str(unresolved_path)],
    )
    return match_rows, unresolved


def _record_persistence(
    *,
    run_root: Path,
    state_path: Path,
    persisted: dict[str, Any],
    persistence_manifest: dict[str, Any],
) -> None:
    persisted_path = run_root / "normalized" / "persisted_match_results.json"
    persistence_manifest_path = run_root / "manifest" / "persistence_manifest.json"
    write_json(persisted_path, persisted)
    write_json(persistence_manifest_path, persistence_manifest)
    update_stage(
        state_path,
        "supplier_persistence",
        "completed",
        counts={
            "requests": persistence_manifest["request_count"],
            "invoices": persistence_manifest["invoice_count"],
            "supplier_lines": persistence_manifest["supplier_line_count"],
        },
        artifacts=[str(persistence_manifest_path)],
    )
    update_stage(
        state_path,
        "result_persistence",
        "completed",
        counts={"results": persistence_manifest["result_count"]},
        artifacts=[str(persisted_path)],
    )


def _continue_after_matching(
    *,
    args: argparse.Namespace,
    run_root: Path,
    state_path: Path,
    config: dict[str, Any],
    report_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    unresolved_path = run_root / "evidence" / "exception_input.json"
    unresolved = read_json(unresolved_path)
    update_stage(state_path, "raw_workbook", "running")
    final_rows = report_rows
    refined_output: Path | None = None
    if unresolved["rows"]:
        if args.investigation is None:
            update_stage(
                state_path,
                "exception_investigation",
                "running",
                artifacts=[str(unresolved_path)],
            )
        else:
            update_stage(state_path, "exception_investigation", "running")
            final_rows = _apply_investigation(
                final_rows,
                read_json(args.investigation),
                expected_run_id=run_root.name,
            )
            update_stage(
                state_path,
                "exception_investigation",
                "completed",
                counts={"investigated_rows": len(unresolved["rows"])},
                artifacts=[str(args.investigation)],
            )
            refined_output = (
                run_root / "refined-recon-report" / "refined-reconciliation.xlsx"
            )
    else:
        update_stage(state_path, "exception_investigation", "skipped")
        update_stage(state_path, "refined_workbook", "skipped")

    run_manifest = read_json(run_root / "manifest" / "run_manifest.json")
    report_manifest = run_root / "manifest" / "report_manifest.json"
    raw_output = run_root / "raw-recon-report" / "raw-reconciliation.xlsx"
    provider = str(read_json(state_path).get("provider") or "")
    write_reports(
        raw_rows=report_rows,
        refined_input_rows=final_rows,
        raw_output=raw_output,
        refined_output=refined_output,
        manifest=report_manifest,
        config=config,
        run_path=_logical_run_path(provider, run_root),
        period=str(run_manifest.get("billing_period") or ""),
    )
    update_stage(
        state_path,
        "raw_workbook",
        "completed",
        counts={"reported_rows": len(final_rows)},
        artifacts=[str(raw_output)],
    )
    if refined_output:
        update_stage(
            state_path,
            "refined_workbook",
            "completed",
            counts={"reported_rows": len(final_rows)},
            artifacts=[str(refined_output)],
        )
    if unresolved["rows"] and args.investigation is None:
        return {
            "run_id": run_root.name,
            "run_root": str(run_root),
            "status": "awaiting_exception_investigation",
            "unresolved_rows": len(unresolved["rows"]),
            "exception_input": str(unresolved_path),
            "raw_workbook": str(raw_output),
        }
    if args.local_only:
        update_stage(state_path, "publication", "skipped")
        return _complete_validation(run_root, config, state_path)
    return _await_publication(run_root, state_path)


def resume_run(args: argparse.Namespace) -> dict[str, Any]:
    run_root = args.resume_run_root.resolve()
    config = load_config(args.config.resolve())
    state_path = run_root / "manifest" / "run_state.json"
    state = read_json(state_path)
    if state.get("run_status") != "running":
        raise RuntimeError("resume_invalid: run must be in running state.")
    billing_stage = state.get("stages", {}).get("billing_preparation", {})
    persistence_stage = state.get("stages", {}).get("supplier_persistence", {})
    stage = state.get("stages", {}).get("exception_investigation", {})
    publication_stage = state.get("stages", {}).get("publication", {})
    if billing_stage.get("status") == "running":
        receipt_paths = getattr(args, "billing_mcp_receipt", None)
        if not isinstance(receipt_paths, list) or not receipt_paths:
            raise RuntimeError(
                "resume_invalid: --billing-mcp-receipt is required for every query chunk."
            )
        plan_path = run_root / "manifest" / "billing_mcp_plan.json"
        plan = read_json(plan_path)
        normalized = read_json(run_root / "normalized" / "provider_lines.json")
        candidates, query_log = consume_billing_query_receipts(
            plan=plan,
            receipt_paths=[path.resolve() for path in receipt_paths],
            normalized=normalized,
            config=config,
        )
        _record_billing_outputs(
            run_root=run_root,
            state_path=state_path,
            candidates=candidates,
            query_log=query_log,
        )
        _record_database_handoff(
            run_root,
            name="billing_query",
            request_path=plan_path,
            receipt_paths=[path.resolve() for path in receipt_paths],
        )
        plan_path.unlink()
        match_rows, _ = _perform_matching(
            run_root=run_root,
            state_path=state_path,
            config=config,
            normalized=normalized,
            candidates=candidates,
        )
        provider_account_id = int(
            read_json(run_root / "manifest" / "run_manifest.json")[
                "provider_account_id"
            ]
        )
        if config.get("features", {}).get("core_persistence_enabled") is True:
            persistence_request = prepare_persistence_request(
                environment=str(plan["environment"]),
                run_id=run_root.name,
                normalized=normalized,
                candidates=candidates,
                matches={"rows": match_rows},
                provider_account_id=provider_account_id,
                run_path=_logical_run_path(str(state.get("provider") or ""), run_root),
            )
            request_path = run_root / "manifest" / "database_persistence_request.json"
            write_json(request_path, persistence_request)
            update_stage(
                state_path,
                "supplier_persistence",
                "running",
                artifacts=[str(request_path)],
            )
            update_stage(state_path, "result_persistence", "running")
            return {
                "run_id": run_root.name,
                "run_root": str(run_root),
                "status": "awaiting_core_persistence",
                "database_persistence_request": str(request_path),
            }
        update_stage(state_path, "supplier_persistence", "skipped")
        update_stage(state_path, "result_persistence", "skipped")
        return _continue_after_matching(
            args=args,
            run_root=run_root,
            state_path=state_path,
            config=config,
            report_rows=match_rows,
        )
    if persistence_stage.get("status") == "running":
        receipt_path = getattr(args, "database_persistence_receipt", None)
        if receipt_path is None:
            raise RuntimeError(
                "resume_invalid: --database-persistence-receipt is required."
            )
        request_path = (
            run_root / "manifest" / "database_persistence_request.json"
        )
        request = read_json(request_path)
        persisted, persistence_manifest = consume_persistence_receipt(
            request, receipt_path.resolve()
        )
        _record_database_handoff(
            run_root,
            name="core_persistence",
            request_path=request_path,
            receipt_paths=[receipt_path.resolve()],
        )
        _record_persistence(
            run_root=run_root,
            state_path=state_path,
            persisted=persisted,
            persistence_manifest=persistence_manifest,
        )
        request_path.unlink()
        return _continue_after_matching(
            args=args,
            run_root=run_root,
            state_path=state_path,
            config=config,
            report_rows=persisted["rows"],
        )
    if stage.get("status") == "running":
        if args.investigation is None:
            raise RuntimeError("resume_invalid: --investigation is required.")
        run_manifest = read_json(run_root / "manifest" / "run_manifest.json")
        persistence_setting = run_manifest.get("core_persistence_enabled")
        result_name = (
            "persisted_match_results.json"
            if persistence_setting is True
            or (
                persistence_setting is None
                and (run_root / "normalized" / "persisted_match_results.json").is_file()
            )
            else "match_results.json"
        )
        persisted = read_json(run_root / "normalized" / result_name)
        final_rows = _apply_investigation(
            persisted.get("rows", []),
            read_json(args.investigation),
            expected_run_id=run_root.name,
        )
        final_path = run_root / "normalized" / "final_match_results.json"
        write_json(final_path, {"rows": final_rows})
        update_stage(
            state_path,
            "exception_investigation",
            "completed",
            counts={"investigated_rows": len(read_json(run_root / "evidence" / "exception_input.json").get("rows", []))},
            artifacts=[str(args.investigation), str(final_path)],
        )
        raw_output = run_root / "raw-recon-report" / "raw-reconciliation.xlsx"
        refined_output = run_root / "refined-recon-report" / "refined-reconciliation.xlsx"
        report_manifest_path = run_root / "manifest" / "report_manifest.json"
        update_stage(state_path, "refined_workbook", "running")
        write_reports(
            raw_rows=persisted.get("rows", []),
            refined_input_rows=final_rows,
            raw_output=raw_output,
            refined_output=refined_output,
            manifest=report_manifest_path,
            config=config,
            run_path=_logical_run_path(str(state.get("provider") or ""), run_root),
            period=str(
                read_json(run_root / "manifest" / "run_manifest.json").get(
                    "billing_period", ""
                )
            ),
        )
        update_stage(
            state_path,
            "refined_workbook",
            "completed",
            counts={"reported_rows": len(final_rows)},
            artifacts=[str(refined_output)],
        )
        if args.local_only:
            update_stage(state_path, "publication", "skipped")
            return _complete_validation(run_root, config, state_path)
        return _await_publication(run_root, state_path)
    if publication_stage.get("status") == "running":
        if args.publication_receipt is None:
            raise RuntimeError("resume_invalid: --publication-receipt is required.")
        receipt = read_json(args.publication_receipt)
        if not isinstance(receipt, dict) or set(receipt) != PUBLICATION_RECEIPT_FIELDS:
            raise RuntimeError(
                "publication_invalid: native receipt fields do not match the contract."
            )
        uploaded = receipt.get("uploaded_artifacts", [])
        if (
            receipt.get("contract_version") != 1
            or receipt.get("status") != "published"
            or receipt.get("run_id") != run_root.name
        ):
            raise RuntimeError(
                "publication_invalid: native receipt identity does not match."
            )
        if (
            not isinstance(uploaded, list)
            or not uploaded
            or not all(
                isinstance(item, dict)
                and set(item) == PUBLISHED_ARTIFACT_FIELDS
                and item.get("status") == "uploaded"
                for item in uploaded
            )
        ):
            raise RuntimeError("publication_invalid: receipt requires uploaded_artifacts.")
        publication_set = read_json(run_root / "manifest" / "publication_set.json")
        if publication_set.get("run_id") != run_root.name:
            raise RuntimeError("publication_invalid: frozen publication set has wrong run identity.")
        frozen_artifacts = publication_set.get("artifacts", [])
        if not isinstance(frozen_artifacts, list) or not frozen_artifacts:
            raise RuntimeError("publication_invalid: frozen publication set is empty.")
        provider = run_root.parent.parent.parent.name
        year = run_root.parent.parent.name
        month = run_root.parent.name
        expected = {
            str(item["relative_path"]): {
                "local_path": str(item["local_path"]),
                "sha256": str(item["sha256"]),
            }
            for item in frozen_artifacts
        }
        current = {
            relative: sha256_file(Path(details["local_path"]))
            for relative, details in expected.items()
        }
        if current != {
            relative: details["sha256"]
            for relative, details in expected.items()
        }:
            raise RuntimeError("publication_invalid: a frozen artifact changed after the publication pause.")
        received = {
            str(item.get("relative_path", "")): {
                "local_path": str(item.get("local_path", "")),
                "sha256": str(item.get("sha256", "")),
            }
            for item in uploaded
        }
        if len(uploaded) != len(received) or received != expected:
            raise RuntimeError("publication_invalid: receipt paths/checksums do not match the run artifacts.")
        run_manifest = read_json(run_root / "manifest" / "run_manifest.json")
        verification_expected = {
            f"{provider}/{year}/{month}/{run_root.name}/{relative}": details[
                "sha256"
            ]
            for relative, details in expected.items()
        }
        if run_manifest.get("intake_mode") == "manual_upload":
            source_move = receipt.get("source_move_receipt")
            source_name = Path(str(run_manifest.get("source_file") or "")).name
            source_relative = f"source/{source_name}"
            if (
                not isinstance(source_move, dict)
                or set(source_move) != SOURCE_MOVE_FIELDS
                or
                source_move.get("status") != "moved"
                or source_move.get("source_name") != source_name
                or source_move.get("relative_path") != source_relative
                or source_move.get("sha256") != run_manifest.get("source_checksum_sha256")
            ):
                raise RuntimeError("publication_invalid: manual upload requires a run-scoped source move receipt.")
            verification_expected[
                f"{provider}/{year}/{month}/{run_root.name}/{source_relative}"
            ] = str(run_manifest.get("source_checksum_sha256") or "")
        elif receipt.get("source_move_receipt") is not None:
            raise RuntimeError(
                "publication_invalid: provider API intake cannot include a source move."
            )
        verification_arguments = getattr(
            args, "publication_verification_receipt", None
        )
        if not isinstance(verification_arguments, list):
            raise RuntimeError(
                "publication_invalid: result-space verification receipts are required."
            )
        verified: dict[str, str] = {}
        capability_path = (
            run_root / "manifest" / "sharepoint_mcp_capabilities.json"
        )
        if not capability_path.is_file():
            raise RuntimeError(
                "publication_invalid: frozen SharePoint MCP capability is missing."
            )
        expected_environment, expected_attestation_public_key = (
            _sharepoint_capability_identity(
                capability_path,
                config,
            )
        )
        for verification_path in verification_arguments:
            verification = _verify_download_receipt(
                receipt_path=verification_path.resolve(),
                provider=provider,
                allowed_spaces=("result",),
                expected_environment=expected_environment,
                expected_attestation_public_key=expected_attestation_public_key,
                forbidden_root=run_root,
            )
            relative_path = str(verification["index"]["relative_path"])
            if relative_path in verified:
                raise RuntimeError(
                    "publication_invalid: duplicate result-space verification receipt."
                )
            verified[relative_path] = str(verification["sha256"])
        if verified != verification_expected:
            raise RuntimeError(
                "publication_invalid: MCP result-space re-downloads do not match publication."
            )
        receipt_path = run_root / "manifest" / "publication_receipt.json"
        write_json(receipt_path, receipt)
        verification_manifest = (
            run_root / "manifest" / "publication_verification_receipts.json"
        )
        write_json(
            verification_manifest,
            {
                "contract_version": 1,
                "run_id": run_root.name,
                "verified_artifacts": verified,
            },
        )
        update_stage(
            state_path,
            "publication",
            "completed",
            counts={"uploaded_artifacts": len(uploaded)},
            artifacts=[str(receipt_path), str(verification_manifest)],
        )
        return _complete_validation(run_root, config, state_path)
    raise RuntimeError(
        "resume_invalid: run is not awaiting billing, persistence, investigation, or publication."
    )


def run(args: argparse.Namespace) -> dict[str, Any]:
    if args.resume_run_root:
        return resume_run(args)
    config_path = args.config.resolve()
    config = load_config(config_path)
    ensure_db_update_disabled(config)
    require_audit(config)
    if args.run_mode == "parser_validation" and not args.copy:
        raise RuntimeError("invalid_run_mode_option: parser_validation requires --copy.")
    if args.run_mode == "parser_validation" and args.intake_mode != "manual_upload":
        raise RuntimeError(
            "invalid_run_mode_option: parser_validation supports manual_upload intake only."
        )
    if args.run_mode == "reconciliation" and args.copy:
        raise RuntimeError("invalid_run_mode_option: reconciliation must move the staged local source.")
    if args.intake_mode == "provider_api":
        features = config.get("features", {})
        adapters = config.get("provider_api_adapters", {})
        provider_key = str(args.provider or "").strip().lower()
        if features.get("provider_api_enabled") is not True or adapters.get(provider_key) is not True:
            raise RuntimeError("provider_api_not_available: provider API intake is not enabled for this provider.")
        _validate_provider_api_provenance(args)
    if (
        args.source_file is None
        or args.result_root is None
        or args.provider is None
        or args.run_mode is None
    ):
        raise RuntimeError(
            "run_input_missing: --provider, --source-file, --result-root, and --run-mode are required for a new run."
        )
    database_mcp_identity: dict[str, Any] | None = None
    if args.run_mode == "reconciliation" and not args.local_only:
        database_capabilities_argument = getattr(
            args, "database_mcp_capabilities", None
        )
        database_probe_argument = getattr(args, "database_mcp_probe", None)
        if (
            database_capabilities_argument is None
            or database_probe_argument is None
        ):
            raise RuntimeError(
                "database_mcp_receipts_required: Fleet reconciliation requires capability and probe receipts."
            )
        database_config = config.get("database_mcp", {})
        environment = (
            str(database_config.get("environment") or "").strip().lower()
            if isinstance(database_config, dict)
            else ""
        )
        if not environment:
            raise RuntimeError(
                "database_mcp_config_invalid: database_mcp.environment is required."
            )
        database_mcp_identity = validate_database_mcp(
            read_json(database_capabilities_argument.resolve()),
            read_json(database_probe_argument.resolve()),
            environment=environment,
            require_persistence=(
                config.get("features", {}).get("core_persistence_enabled") is True
            ),
            row_limit=int(
                config.get("limits", {}).get("billing_query_row_limit", 5000)
            ),
        )
    capabilities = capability_manifest(
        config,
        local_check=args.local_only,
        database_mcp_validated=database_mcp_identity is not None,
        database_mcp_persistence=bool(
            database_mcp_identity
            and database_mcp_identity.get("core_persistence") is True
        ),
    )
    policy = execution_policy(
        config,
        capabilities,
        run_mode=args.run_mode,
        intake_mode=args.intake_mode,
        provider=args.provider,
        local_only=args.local_only,
    )
    required_runtime = ["provider_parsing", "archive_validation"]
    missing_runtime = [
        name
        for name in required_runtime
        if capabilities["capabilities"].get(name) is not True
    ]
    blockers = [*missing_runtime, *policy["blockers"]]
    if blockers:
        raise RuntimeError(
            f"core_reconciliation_not_available: missing capabilities or enabled features {blockers}"
        )

    verified_download_receipt: dict[str, Any] | None = None
    sharepoint_capability_argument = getattr(
        args, "sharepoint_mcp_capabilities", None
    )
    expected_environment = ""
    expected_attestation_public_key = ""
    if not args.local_only:
        if sharepoint_capability_argument is None:
            raise RuntimeError(
                "sharepoint_mcp_capability_required: Fleet runs require the validated capability envelope."
            )
        expected_environment, expected_attestation_public_key = (
            _sharepoint_capability_identity(
                sharepoint_capability_argument.resolve(),
                config,
            )
        )
    if not args.local_only and args.intake_mode == "manual_upload":
        receipt_argument = getattr(args, "source_download_receipt", None)
        if receipt_argument is None:
            raise RuntimeError(
                "source_download_receipt_required: Fleet intake requires capability and binary download receipts."
            )
        verified_download_receipt = _verify_download_receipt(
            receipt_path=receipt_argument.resolve(),
            provider=args.provider,
            allowed_spaces=(
                ("upload", "reference")
                if args.run_mode == "parser_validation"
                else ("upload",)
            ),
            expected_file=args.source_file.resolve(),
            expected_relative_path=f"{args.provider}/{args.source_file.name}",
            expected_environment=expected_environment,
            expected_attestation_public_key=expected_attestation_public_key,
        )
    (args.result_root / args.provider).mkdir(parents=True, exist_ok=True)
    source_checksum = sha256_file(args.source_file)
    run_root = create_run(
        config=config,
        provider=args.provider,
        source_file=args.source_file,
        result_root=args.result_root,
        intake_mode=args.intake_mode,
        source_identity=args.source_identity,
        copy_source=args.copy,
    )
    run_id = run_root.name
    run_manifest_path = run_root / "manifest" / "run_manifest.json"
    run_manifest = read_json(run_manifest_path)
    run_manifest["run_mode"] = args.run_mode
    run_manifest["billing_period"] = args.billing_period or ""
    run_manifest["core_persistence_enabled"] = (
        config.get("features", {}).get("core_persistence_enabled") is True
    )
    policy_path = run_root / "manifest" / "execution_policy.json"
    write_json(policy_path, policy)
    run_manifest["execution_policy_sha256"] = sha256_file(policy_path)
    if args.provider_account_id is not None:
        run_manifest["provider_account_id"] = args.provider_account_id
    if database_mcp_identity is not None:
        capability_snapshot = (
            run_root / "manifest" / "database_mcp_capabilities.json"
        )
        probe_snapshot = run_root / "manifest" / "database_mcp_probe.json"
        write_json(
            capability_snapshot,
            read_json(database_capabilities_argument.resolve()),
        )
        write_json(probe_snapshot, read_json(database_probe_argument.resolve()))
        run_manifest["database_mcp_capability_sha256"] = sha256_file(
            capability_snapshot
        )
        run_manifest["database_mcp_probe_sha256"] = sha256_file(probe_snapshot)
    if sharepoint_capability_argument is not None:
        capability_snapshot = (
            run_root / "manifest" / "sharepoint_mcp_capabilities.json"
        )
        write_json(
            capability_snapshot,
            read_json(sharepoint_capability_argument.resolve()),
        )
        run_manifest["sharepoint_mcp_capability_sha256"] = sha256_file(
            capability_snapshot
        )
    if verified_download_receipt is not None:
        run_manifest["sharepoint_source_space"] = str(
            verified_download_receipt.get("space") or "upload"
        )
        run_manifest["sharepoint_source_index"] = verified_download_receipt[
            "index"
        ]
        run_manifest["source_download_receipt_sha256"] = (
            _freeze_download_receipt(
                run_root, verified_download_receipt
            )
        )
    write_json(run_manifest_path, run_manifest)
    state_path = run_root / "manifest" / "run_state.json"
    create_state(
        state_path,
        run_id=run_id,
        provider=args.provider,
        run_mode=args.run_mode,
        source_identity=args.source_identity or source_checksum,
    )
    _audit(run_root, run_id=run_id, source_checksum=source_checksum)
    current_stage = "run_creation"
    try:
        update_stage(state_path, "source_staging", "completed", artifacts=[str(run_root / "source")])
        update_stage(state_path, "run_creation", "completed", artifacts=[str(run_root)])

        current_stage = "archive_validation"
        update_stage(state_path, current_stage, "running")
        source_files = [path for path in (run_root / "source").iterdir() if path.is_file()]
        if len(source_files) != 1:
            raise RuntimeError("source_ambiguous: run source folder must contain exactly one package.")
        source = source_files[0]
        unpack_manifest = run_root / "manifest" / "unpack_manifest.json"
        if source.suffix.lower() == ".zip":
            limits = config.get("limits", {})
            inventory = extract_zip(
                source,
                run_root / "extracted",
                max_members=int(limits.get("max_zip_members", 2000)),
                max_single_file_bytes=int(limits.get("max_single_file_mb", 250)) * 1024 * 1024,
                max_total_expanded_bytes=int(limits.get("max_total_expanded_mb", 1000)) * 1024 * 1024,
                max_compression_ratio=int(limits.get("max_compression_ratio", 100)),
            )
            write_json(unpack_manifest, inventory)
            if inventory["blocked"]:
                raise RuntimeError("unsafe_archive: archive validation failed.")
            archive_count = len(inventory["members"])
        else:
            write_json(
                unpack_manifest,
                {"archive": None, "members": [], "blocked": [], "status": "not_archive"},
            )
            archive_count = 0
        update_stage(
            state_path,
            current_stage,
            "completed",
            counts={"archive_members": archive_count},
            artifacts=[str(unpack_manifest)],
        )

        current_stage = "provider_parsing"
        update_stage(state_path, current_stage, "running")
        parser_cli, billing_cli = _paths()
        _run_command(
            _parser_command(
                parser_cli=parser_cli,
                config_path=config_path,
                provider=args.provider,
                run_root=run_root,
                input_dir=(
                    run_root / "extracted"
                    if source.suffix.lower() == ".zip"
                    and args.provider in {"Telstra", "Vocus", "Megaport", "Equinix"}
                    else run_root / "source"
                ),
            )
        )
        normalized_path = run_root / "normalized" / "provider_lines.json"
        normalized = read_json(normalized_path)
        parsed_count = len(normalized.get("lines", []))
        update_stage(
            state_path,
            current_stage,
            "completed",
            counts={"parsed_rows": parsed_count, "invoice_headers": len(normalized.get("invoice_headers", []))},
            artifacts=[str(normalized_path)],
        )

        if args.run_mode == "parser_validation":
            for stage in (
                "supplier_persistence",
                "billing_preparation",
                "deterministic_comparison",
                "result_persistence",
                "raw_workbook",
                "exception_investigation",
                "refined_workbook",
                "publication",
                "notification",
            ):
                update_stage(state_path, stage, "skipped")
            current_stage = "validation"
            update_stage(state_path, current_stage, "running")
            result = validate_run(run_root, config, "parser_validation")
            update_stage(state_path, current_stage, "completed")
            finalize_state(state_path, "completed")
            result["run_root"] = str(run_root)
            return result

        if args.billing_sql_file is None or args.provider_account_id is None:
            raise RuntimeError("reconciliation_input_missing: --billing-sql-file and --provider-account-id are required.")
        current_stage = "billing_preparation"
        update_stage(state_path, current_stage, "running")
        if not args.local_only:
            plan = prepare_billing_query_plan(
                normalized=normalized,
                sql_file=args.billing_sql_file.resolve(),
                config=config,
                environment=str(database_mcp_identity["environment"]),
            )
            plan_path = run_root / "manifest" / "billing_mcp_plan.json"
            write_json(plan_path, plan)
            update_stage(
                state_path,
                current_stage,
                "running",
                counts={"query_chunks": len(plan["requests"])},
                artifacts=[str(plan_path)],
            )
            return {
                "run_id": run_id,
                "run_root": str(run_root),
                "status": "awaiting_billing_query",
                "billing_mcp_plan": str(plan_path),
                "query_chunks": len(plan["requests"]),
            }
        _run_command(
            _billing_command(
                billing_cli=billing_cli,
                config_path=config_path,
                run_root=run_root,
                sql_file=args.billing_sql_file.resolve(),
            )
        )
        candidates_path = run_root / "evidence" / "billing_candidates.json"
        candidates = read_json(candidates_path)
        query_log_path = run_root / "logs" / "billing_query_log.json"
        query_log = read_json(query_log_path)
        _record_billing_outputs(
            run_root=run_root,
            state_path=state_path,
            candidates=candidates,
            query_log=query_log,
        )

        current_stage = "deterministic_comparison"
        match_rows, _ = _perform_matching(
            run_root=run_root,
            state_path=state_path,
            config=config,
            normalized=normalized,
            candidates=candidates,
        )

        if config.get("features", {}).get("core_persistence_enabled") is True:
            current_stage = "supplier_persistence"
            update_stage(state_path, current_stage, "running")
            update_stage(state_path, "result_persistence", "running")
            core_mode = os.environ.get("NEXON_RECON_CORE_MODE", "").strip().lower()
            persistence_args = {
                "dsn": os.environ["NEXON_RECON_CORE_DSN"],
                "normalized": normalized,
                "candidates": candidates,
                "matches": {"rows": match_rows},
                "provider_account_id": args.provider_account_id,
                "run_path": _logical_run_path(args.provider, run_root),
            }
            if core_mode == "sqlite_shadow":
                persisted, persistence_manifest = persist_shadow_run(**persistence_args)
            elif core_mode in {"sqlserver", "azure_sql"}:
                persisted, persistence_manifest = persist_sqlserver_run(
                    **persistence_args
                )
            else:
                raise RuntimeError(
                    "core_persistence_not_available: NEXON_RECON_CORE_MODE must be "
                    "sqlite_shadow, sqlserver, or azure_sql."
                )
            _record_persistence(
                run_root=run_root,
                state_path=state_path,
                persisted=persisted,
                persistence_manifest=persistence_manifest,
            )
            report_rows = persisted["rows"]
        else:
            update_stage(state_path, "supplier_persistence", "skipped")
            update_stage(state_path, "result_persistence", "skipped")
            report_rows = match_rows
        current_stage = "raw_workbook"
        return _continue_after_matching(
            args=args,
            run_root=run_root,
            state_path=state_path,
            config=config,
            report_rows=report_rows,
        )
    except Exception as exc:
        update_stage(
            state_path,
            current_stage,
            "failed",
            failure_code=str(exc).split(":", 1)[0],
            retryable=False,
        )
        finalize_state(state_path, "failed")
        write_json(
            run_root / "manifest" / "failure_manifest.json",
            {
                "status": "failed",
                "run_id": run_id,
                "correlation_id": run_id,
                "provider": args.provider,
                "failed_stage": current_stage,
                "failure_code": str(exc).split(":", 1)[0],
                "sanitized_detail": str(exc),
                "retryable": False,
                "recorded_at": datetime.now(timezone.utc).isoformat(),
                "accepted_resolution_update_attempted": False,
                "notification_required": False,
                "notification_sent": False,
            },
        )
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the deterministic Nexon reconciliation state machine.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--provider")
    parser.add_argument("--source-file", type=Path)
    parser.add_argument("--result-root", type=Path)
    parser.add_argument("--run-mode", choices=["parser_validation", "reconciliation"])
    parser.add_argument("--resume-run-root", type=Path)
    parser.add_argument("--intake-mode", choices=["manual_upload", "provider_api"], default="manual_upload")
    parser.add_argument("--source-identity")
    parser.add_argument("--provider-api-manifest", type=Path)
    parser.add_argument("--provider-api-account-id")
    parser.add_argument("--provider-api-document-id")
    parser.add_argument("--copy", action="store_true")
    parser.add_argument("--billing-sql-file", type=Path)
    parser.add_argument("--billing-period")
    parser.add_argument("--provider-account-id", type=int)
    parser.add_argument("--investigation", type=Path)
    parser.add_argument("--publication-receipt", type=Path)
    parser.add_argument(
        "--publication-verification-receipt",
        type=Path,
        action="append",
    )
    parser.add_argument("--source-download-receipt", type=Path)
    parser.add_argument("--sharepoint-mcp-capabilities", type=Path)
    parser.add_argument("--database-mcp-capabilities", type=Path)
    parser.add_argument("--database-mcp-probe", type=Path)
    parser.add_argument("--billing-mcp-receipt", type=Path, action="append")
    parser.add_argument("--database-persistence-receipt", type=Path)
    parser.add_argument("--local-only", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run(args)
    if args.output:
        write_json(args.output, result)
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
