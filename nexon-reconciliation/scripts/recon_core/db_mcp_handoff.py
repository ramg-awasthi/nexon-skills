from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from sqlglot import exp, parse_one

from .billing_query import (
    _assert_billing_config,
    _assert_read_only_sql,
    _candidate_from_row,
    _chunk_key,
    _load_sql,
    _param_hashes,
    _strip_sql_comments,
)
from .common import positive_limit, read_json, write_json


READ_RESPONSE_FIELDS = {
    "environment",
    "run_id",
    "query_sha256",
    "parameter_sha256",
    "tables",
    "row_count",
    "rows",
}
PERSIST_RESPONSE_FIELDS = {
    "environment",
    "run_id",
    "payload_sha256",
    "persisted",
    "manifest",
}


def canonical_hash(value: Any) -> str:
    payload = json.dumps(value, default=str, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _result(payload: Any, *, name: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise RuntimeError(f"{name}_invalid: expected an object.")
    result = payload.get("result", payload)
    if not isinstance(result, dict):
        raise RuntimeError(f"{name}_invalid: result must be an object.")
    return result


def validate_database_mcp(
    capabilities_payload: dict[str, Any],
    probe_payload: dict[str, Any],
    *,
    environment: str,
    require_persistence: bool,
    row_limit: int,
) -> dict[str, Any]:
    capabilities = _result(capabilities_payload, name="database_mcp_capabilities")
    probe = _result(probe_payload, name="database_mcp_probe")
    flags = capabilities.get("capabilities")
    policy = capabilities.get("query_policy")
    if (
        capabilities.get("service") != "nexon-recon-db-mcp"
        or capabilities.get("environment") != environment
        or not isinstance(flags, dict)
        or flags.get("read_queries") is not True
        or not isinstance(policy, dict)
        or policy.get("read_only") is not True
        or policy.get("schema_qualified_allowlist") is not True
        or policy.get("comments_allowed") is not False
        or policy.get("wildcard_projection_allowed") is not False
        or policy.get("audit_required") is not True
        or int(policy.get("row_limit") or 0) < row_limit
    ):
        raise RuntimeError(
            "database_mcp_capability_invalid: read-query policy is incomplete."
        )
    if require_persistence and flags.get("core_persistence") is not True:
        raise RuntimeError(
            "database_mcp_capability_invalid: core persistence is not enabled."
        )
    if (
        probe.get("environment") != environment
        or probe.get("reachable") is not True
        or not str(probe.get("database_name") or "").strip()
    ):
        raise RuntimeError(
            "database_mcp_probe_invalid: database identity is not reachable."
        )
    return {
        "environment": environment,
        "read_queries": True,
        "core_persistence": flags.get("core_persistence") is True,
        "database_name": str(probe["database_name"]),
    }


def _projection_names(sql: str) -> list[str]:
    expression = parse_one(sql, read="tsql")
    select = expression if isinstance(expression, exp.Select) else expression.find(exp.Select)
    if select is None:
        raise RuntimeError("billing_query_sql_invalid: SQL has no SELECT projection.")
    names = [str(item.alias_or_name or "").strip() for item in select.expressions]
    lowered = [name.lower() for name in names]
    if (
        not names
        or any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) for name in names)
        or len(lowered) != len(set(lowered))
    ):
        raise RuntimeError(
            "billing_query_scope_invalid: every projected candidate column needs a unique safe name."
        )
    return names


def _table_names(sql: str) -> list[str]:
    expression = parse_one(sql, read="tsql")
    names = sorted(
        {
            f"{str(table.db or '').lower()}.{str(table.name or '').lower()}"
            for table in expression.find_all(exp.Table)
        }
    )
    return names


def _mcp_scoped_sql(sql: str, service_parameter_names: list[str]) -> str:
    cleaned = _strip_sql_comments(sql).strip().rstrip(";")
    projection = ", ".join(
        f"candidate_scope.[{name.replace(']', ']]')}]"
        for name in _projection_names(cleaned)
    )
    service_scope = ", ".join(f":{name}" for name in service_parameter_names)
    return (
        f"SELECT {projection} FROM ({cleaned}) AS candidate_scope "
        "WHERE LOWER(candidate_scope.provider) = LOWER(:provider) "
        "AND candidate_scope.provider_account = :provider_account "
        "AND candidate_scope.transaction_date >= :billing_period_start "
        "AND candidate_scope.transaction_date <= :billing_period_end "
        f"AND candidate_scope.service_id IN ({service_scope})"
    )


def prepare_billing_query_plan(
    *,
    normalized: dict[str, Any],
    sql_file: Path,
    config: dict[str, Any],
    environment: str,
) -> dict[str, Any]:
    _assert_billing_config(config)
    sql, sql_source = _load_sql(None, sql_file)
    _assert_read_only_sql(sql)
    lines = normalized.get("lines", [])
    if not isinstance(lines, list) or not lines:
        raise RuntimeError(
            "billing_query_scope_invalid: normalized lines must be a non-empty list."
        )
    run_ids = {str(line.get("run_id") or "") for line in lines}
    providers = {str(line.get("provider") or "") for line in lines}
    if len(run_ids) != 1 or "" in run_ids or len(providers) != 1 or "" in providers:
        raise RuntimeError(
            "billing_query_scope_invalid: normalized rows must belong to one run/provider."
        )
    run_id = next(iter(run_ids))
    provider = next(iter(providers))
    batch_size = positive_limit(config, "investigation_rows_per_batch", 100)
    row_limit = positive_limit(config, "billing_query_row_limit", 5000)
    grouped: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for line in lines:
        grouped[_chunk_key(line)].append(line)

    requests: list[dict[str, Any]] = []
    for group_lines in grouped.values():
        for start in range(0, len(group_lines), batch_size):
            chunk = group_lines[start : start + batch_size]
            service_ids = sorted(
                {
                    str(
                        line.get("service_id_normalized")
                        or line.get("service_id_raw")
                        or ""
                    )
                    for line in chunk
                    if line.get("service_id_normalized") or line.get("service_id_raw")
                }
            )
            if not service_ids:
                raise RuntimeError(
                    "billing_query_scope_invalid: a query chunk has no service identifiers."
                )
            service_parameters = {
                f"service_id_{index}": value
                for index, value in enumerate(service_ids)
            }
            first = chunk[0]
            parameters = {
                "provider": first.get("provider"),
                "provider_account": first.get("provider_account"),
                "billing_period_start": first.get("billing_period_start"),
                "billing_period_end": first.get("billing_period_end"),
                **service_parameters,
            }
            scoped_sql = _mcp_scoped_sql(sql, list(service_parameters))
            requests.append(
                {
                    "chunk_id": len(requests) + 1,
                    "environment": environment,
                    "run_id": run_id,
                    "purpose": "initial_reconciliation_candidate_lookup",
                    "sql": scoped_sql,
                    "parameters": parameters,
                    "row_limit": row_limit,
                    "query_sha256": hashlib.sha256(
                        scoped_sql.encode("utf-8")
                    ).hexdigest(),
                    "parameter_sha256": canonical_hash(parameters),
                    "tables": _table_names(sql),
                    "line_ids": [str(line.get("line_id") or "") for line in chunk],
                }
            )
    return {
        "contract_version": 1,
        "environment": environment,
        "run_id": run_id,
        "provider": provider,
        "sql_source": sql_source,
        "base_sql_sha256": hashlib.sha256(sql.encode("utf-8")).hexdigest(),
        "requests": requests,
    }


def consume_billing_query_receipts(
    *,
    plan: dict[str, Any],
    receipt_paths: list[Path],
    normalized: dict[str, Any],
    config: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    requests = plan.get("requests", [])
    if not isinstance(requests, list) or not requests:
        raise RuntimeError("billing_mcp_plan_invalid: requests are missing.")
    if len(receipt_paths) != len(requests):
        raise RuntimeError(
            "billing_mcp_receipt_invalid: provide exactly one receipt per query chunk."
        )
    lines = {
        str(line.get("line_id") or ""): line
        for line in normalized.get("lines", [])
        if isinstance(line, dict)
    }
    candidate_limit = positive_limit(
        config, "investigation_candidates_per_row", 20
    )
    candidates_by_line: dict[str, list[dict[str, Any]]] = {}
    query_log: list[dict[str, Any]] = []
    for request, receipt_path in zip(requests, receipt_paths, strict=True):
        receipt = _result(read_json(receipt_path), name="billing_mcp_receipt")
        if set(receipt) != READ_RESPONSE_FIELDS:
            raise RuntimeError(
                "billing_mcp_receipt_invalid: response fields do not match the contract."
            )
        rows = receipt.get("rows")
        if (
            receipt.get("environment") != request.get("environment")
            or receipt.get("run_id") != request.get("run_id")
            or receipt.get("query_sha256") != request.get("query_sha256")
            or receipt.get("parameter_sha256") != request.get("parameter_sha256")
            or receipt.get("tables") != request.get("tables")
            or not isinstance(rows, list)
            or receipt.get("row_count") != len(rows)
            or len(rows) > int(request.get("row_limit") or 0)
        ):
            raise RuntimeError(
                "billing_mcp_receipt_invalid: response identity or limits do not match."
            )
        by_service: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            if not isinstance(row, dict):
                raise RuntimeError(
                    "billing_mcp_receipt_invalid: every returned row must be an object."
                )
            service_id = (
                row.get("service_id")
                or row.get("carrier_service_id")
                or row.get("circuit_id")
                or row.get("line_number")
            )
            by_service[str(service_id or "").strip().lower()].append(row)
        for line_id in request.get("line_ids", []):
            line = lines.get(str(line_id))
            if line is None:
                raise RuntimeError(
                    "billing_mcp_plan_invalid: query references an unknown line."
                )
            service_keys = {
                str(line.get("service_id_raw") or "").strip().lower(),
                str(line.get("service_id_normalized") or "").strip().lower(),
            } - {""}
            selected: dict[str, dict[str, Any]] = {}
            for service_key in service_keys:
                for row in by_service.get(service_key, []):
                    selected[canonical_hash(row)] = row
            if len(selected) > candidate_limit:
                raise RuntimeError(
                    f"billing_query_candidate_limit_exceeded: line {line_id} has more than "
                    f"{candidate_limit} candidates."
                )
            candidates_by_line[str(line_id)] = [
                _candidate_from_row(line, row, index)
                for index, row in enumerate(selected.values())
            ]
        query_log.append(
            {
                "chunk_id": request["chunk_id"],
                "line_count": len(request.get("line_ids", [])),
                "billing_mode": "database_mcp",
                "sql_source": plan.get("sql_source"),
                "sql_hash": request["query_sha256"],
                "read_only_validation": "mcp_passed",
                "parameter_keys": sorted(request["parameters"]),
                "parameter_hashes": _param_hashes(request["parameters"]),
                "row_count": len(rows),
                "row_limit": request["row_limit"],
                "candidate_limit_per_line": candidate_limit,
                "query_round": None,
                "query_budget": None,
            }
        )
    return (
        {
            "run_id": plan["run_id"],
            "provider": plan["provider"],
            "billing_mode": "database_mcp",
            "sql_source": plan["sql_source"],
            "sql_hash": plan["base_sql_sha256"],
            "candidates_by_line": candidates_by_line,
        },
        query_log,
    )


def prepare_persistence_request(
    *,
    environment: str,
    run_id: str,
    normalized: dict[str, Any],
    candidates: dict[str, Any],
    matches: dict[str, Any],
    provider_account_id: int,
    run_path: str,
) -> dict[str, Any]:
    payload = {
        "run_id": run_id,
        "normalized": normalized,
        "candidates": candidates,
        "matches": matches,
        "provider_account_id": provider_account_id,
        "run_path": run_path,
    }
    return {
        "contract_version": 1,
        "environment": environment,
        **payload,
        "payload_sha256": canonical_hash(payload),
        "write_intent": "persist_current_reconciliation_lifecycle",
    }


def consume_persistence_receipt(
    request: dict[str, Any], receipt_path: Path
) -> tuple[dict[str, Any], dict[str, Any]]:
    receipt = _result(read_json(receipt_path), name="database_persistence_receipt")
    if set(receipt) != PERSIST_RESPONSE_FIELDS:
        raise RuntimeError(
            "database_persistence_receipt_invalid: response fields do not match."
        )
    persisted = receipt.get("persisted")
    manifest = receipt.get("manifest")
    if (
        receipt.get("environment") != request.get("environment")
        or receipt.get("run_id") != request.get("run_id")
        or receipt.get("payload_sha256") != request.get("payload_sha256")
        or not isinstance(persisted, dict)
        or not isinstance(persisted.get("rows"), list)
        or not isinstance(manifest, dict)
    ):
        raise RuntimeError(
            "database_persistence_receipt_invalid: response identity or payload is invalid."
        )
    return persisted, manifest


def write_billing_outputs(
    *,
    run_root: Path,
    candidates: dict[str, Any],
    query_log: list[dict[str, Any]],
) -> tuple[Path, Path]:
    candidates_path = run_root / "evidence" / "billing_candidates.json"
    query_log_path = run_root / "logs" / "billing_query_log.json"
    write_json(candidates_path, candidates)
    write_json(query_log_path, query_log)
    return candidates_path, query_log_path
