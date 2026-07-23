from __future__ import annotations

import argparse
import hashlib
import os
import re
import sqlite3
import time
import json
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

from .common import DEFAULT_CONFIG_PATH, load_config, positive_limit, read_json, require_audit, write_json

FORBIDDEN_SQL_TOKENS = {
    "alter",
    "call",
    "copy",
    "create",
    "delete",
    "drop",
    "execute",
    "grant",
    "insert",
    "into",
    "merge",
    "reindex",
    "replace",
    "revoke",
    "truncate",
    "update",
    "vacuum",
}

APPROVED_TABLES = {
    "dbo.inomialservicemetadata",
    "dbo.inomialtransactiondata",
    "finance.inomialservicemetadata",
    "finance.inomialtransactiondata",
    "finance.genericnexonbilling",
    "finance.billingSystem".lower(),
    "finance.serviceprovider",
    "finance.serviceprovideraccount",
}

FORBIDDEN_COLUMN_FRAGMENTS = {"password", "secret", "token", "credential", "connectionstring"}


def _load_sql(sql: str | None, sql_file: Path | None) -> tuple[str, str]:
    if sql_file:
        return sql_file.read_text(encoding="utf-8"), str(sql_file)
    if sql:
        return sql, "inline"
    raise RuntimeError("billing_query_sql_missing: Provide --sql-file or --sql.")


def _sql_hash(sql: str) -> str:
    return hashlib.sha256(sql.encode("utf-8")).hexdigest()


def _json_payload_hash(payload: Any) -> str:
    serialized = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _strip_sql_comments(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    return re.sub(r"--[^\n\r]*", " ", sql)


def _assert_read_only_sql(sql: str) -> None:
    cleaned = _strip_sql_comments(sql).strip()
    normalized = re.sub(r"\s+", " ", cleaned).lower()
    if not normalized:
        raise RuntimeError("billing_query_sql_invalid: SQL is empty.")
    if not (normalized.startswith("select ") or normalized.startswith("with ")):
        raise RuntimeError("billing_query_not_read_only: SQL must start with SELECT or WITH.")
    if ";" in normalized.rstrip(";"):
        raise RuntimeError("billing_query_not_read_only: SQL must contain only one read-only statement.")
    tokens = set(re.findall(r"[a-z_][a-z0-9_]*", normalized))
    forbidden = sorted(tokens.intersection(FORBIDDEN_SQL_TOKENS))
    if forbidden:
        raise RuntimeError(f"billing_query_not_read_only: forbidden SQL tokens present: {forbidden}")
    mode = os.environ.get("NEXON_RECON_BILLING_MODE", "").strip().lower()
    try:
        from sqlglot import exp, parse_one
    except ImportError as exc:
        raise RuntimeError("sqlglot is required for structural SQL validation.") from exc
    dialect = (
        "tsql"
        if mode in {"sqlserver", "azure_sql"}
        else "postgres"
        if mode in {"postgres", "postgresql", "inomial_postgres"}
        else "sqlite"
    )
    try:
        expression = parse_one(cleaned, read=dialect)
    except Exception as exc:
        raise RuntimeError("billing_query_sql_invalid: SQL could not be parsed.") from exc
    targets = []
    for table in expression.find_all(exp.Table):
        name = str(table.name or "").lower()
        database = str(table.db or "").lower()
        targets.append(f"{database}.{name}" if database else name)
    unapproved = sorted(
        target
        for target in targets
        if target not in APPROVED_TABLES
        and not (mode == "sqlite" and target == "billing_candidates")
    )
    if unapproved:
        raise RuntimeError(f"billing_query_scope_invalid: unapproved tables referenced: {unapproved}")
    select = expression if isinstance(expression, exp.Select) else expression.find(exp.Select)
    if select is None:
        raise RuntimeError("billing_query_sql_invalid: SQL has no SELECT projection.")
    canonical = {"provider", "provider_account", "transaction_date", "service_id"}
    projected = {
        str(item.alias_or_name or "").lower(): item
        for item in select.expressions
    }
    missing_projection = sorted(canonical - set(projected))
    if missing_projection:
        raise RuntimeError(
            f"billing_query_scope_invalid: query must project canonical candidate fields {missing_projection}."
        )
    unsafe_projection = sorted(
        name
        for name in canonical
        if not isinstance(
            projected[name].this if isinstance(projected[name], exp.Alias) else projected[name],
            exp.Column,
        )
    )
    if unsafe_projection:
        raise RuntimeError(
            f"billing_query_scope_invalid: canonical fields must come from source columns {unsafe_projection}."
        )
    approved_sources = {
        "provider": {
            "provider",
            "providername",
            "serviceprovider",
            "service_provider",
            "suppliername",
            "carrier_name",
        },
        "provider_account": {
            "provider_account",
            "provideraccount",
            "serviceprovideraccount",
            "supplieraccountnumber",
            "accountnumber",
        },
        "transaction_date": {
            "transaction_date",
            "billingdate",
            "ledgerdate",
            "creationtime",
            "charge_start",
        },
        "service_id": {
            "service_id",
            "servicenumber",
            "carrier_service_id",
            "circuit_id",
            "line_number",
        },
    }
    wrong_sources = []
    for canonical_name, allowed in approved_sources.items():
        item = projected[canonical_name]
        column = item.this if isinstance(item, exp.Alias) else item
        if str(column.name or "").lower() not in allowed:
            wrong_sources.append(canonical_name)
    if wrong_sources:
        raise RuntimeError(
            f"billing_query_scope_invalid: canonical aliases use unapproved source columns {sorted(wrong_sources)}."
        )
    identifiers = {token.lower() for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", cleaned)}
    unsafe_columns = sorted(
        token
        for token in identifiers
        if any(fragment in token for fragment in FORBIDDEN_COLUMN_FRAGMENTS)
    )
    if unsafe_columns:
        raise RuntimeError(f"billing_query_scope_invalid: forbidden column identifiers: {unsafe_columns}")


def _scoped_sql(sql: str, mode: str) -> str:
    cleaned = _strip_sql_comments(sql).strip().rstrip(";")
    if mode in {"postgres", "postgresql", "inomial_postgres"}:
        service_scope = (
            "SELECT value FROM jsonb_array_elements_text("
            "CAST(:service_ids_json AS jsonb)) AS scoped_ids(value)"
        )
    elif mode in {"sqlserver", "azure_sql"}:
        service_scope = "SELECT [value] FROM OPENJSON(:service_ids_json)"
    else:
        service_scope = "SELECT value FROM json_each(:service_ids_json)"
    return (
        "SELECT * FROM ("
        f"{cleaned}"
        ") AS candidate_scope "
        "WHERE LOWER(candidate_scope.provider) = LOWER(:provider) "
        "AND candidate_scope.provider_account = :provider_account "
        "AND candidate_scope.transaction_date >= :billing_period_start "
        "AND candidate_scope.transaction_date <= :billing_period_end "
        f"AND candidate_scope.service_id IN ({service_scope})"
    )


def _assert_billing_config(config: dict[str, Any]) -> None:
    if config.get("features", {}).get("billing_query_enabled") is not True:
        raise RuntimeError("billing_query_not_available: Enable approved read-only billing query mode before use.")
    billing = config.get("billing", {})
    if not isinstance(billing, dict):
        raise RuntimeError("billing_query_not_available: billing config must be a mapping.")
    if billing.get("mode") != "read_only_sql":
        raise RuntimeError("billing_query_not_available: billing.mode must be read_only_sql.")
    if billing.get("agent_sql_allowed") is not True:
        raise RuntimeError("billing_query_not_available: billing.agent_sql_allowed must be true.")
    require_audit(config)


def _line_params(line: dict[str, Any]) -> dict[str, Any]:
    return {
        "line_id": line.get("line_id"),
        "provider": line.get("provider"),
        "service_id": line.get("service_id_normalized") or line.get("service_id_raw"),
        "service_id_raw": line.get("service_id_raw"),
        "service_id_normalized": line.get("service_id_normalized"),
        "provider_account": line.get("provider_account"),
        "invoice_number": line.get("invoice_number"),
        "billing_period_start": line.get("billing_period_start"),
        "billing_period_end": line.get("billing_period_end"),
    }


def _chunk_key(line: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(line.get("provider") or ""),
        str(line.get("provider_account") or ""),
        str(line.get("billing_period_start") or ""),
        str(line.get("billing_period_end") or ""),
    )


def _chunk_params(lines: list[dict[str, Any]]) -> dict[str, Any]:
    if not lines:
        raise ValueError("Cannot build parameters for an empty billing chunk.")
    first = lines[0]
    service_ids = sorted(
        {
            str(line.get("service_id_normalized") or line.get("service_id_raw") or "")
            for line in lines
            if line.get("service_id_normalized") or line.get("service_id_raw")
        }
    )
    return {
        "provider": first.get("provider"),
        "provider_account": first.get("provider_account"),
        "billing_period_start": first.get("billing_period_start"),
        "billing_period_end": first.get("billing_period_end"),
        "service_ids_json": json.dumps(service_ids),
    }


def _param_hashes(params: dict[str, Any]) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for key, value in params.items():
        if value in (None, ""):
            continue
        hashes[key] = hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:12]
    return hashes


def _row_to_dict(cursor: Any, row: Any) -> dict[str, Any]:
    columns = [column[0] for column in cursor.description]
    return {columns[index]: row[index] for index in range(len(columns))}


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    if isinstance(value, date):
        return value
    text = str(value)[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _normalize_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _same_text(left: Any, right: Any) -> bool:
    return bool(left and right and str(left).strip().lower() == str(right).strip().lower())


def _date_in_window(candidate_date: Any, start: Any, end: Any) -> bool:
    candidate = _parse_date(candidate_date)
    start_date = _parse_date(start)
    end_date = _parse_date(end)
    if not candidate or not start_date or not end_date:
        return False
    return start_date <= candidate <= end_date


def _candidate_from_row(line: dict[str, Any], row: dict[str, Any], index: int) -> dict[str, Any]:
    service_id = row.get("service_id") or row.get("carrier_service_id") or row.get("circuit_id") or row.get("line_number")
    provider = row.get("provider") or row.get("carrier_name") or row.get("service_provider")
    transaction_date = row.get("transaction_date") or row.get("charge_start") or row.get("ledger_date") or row.get("creation_time")
    candidate_id = row.get("candidate_id") or (
        "candidate-"
        + hashlib.sha256(
            json.dumps(row, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()[:20]
    )
    return {
        **row,
        "candidate_id": candidate_id,
        "line_id": line.get("line_id"),
        "service_id": service_id,
        "carrier_name": provider,
        "transaction_date": transaction_date,
        "service_id_match": _normalize_bool(row.get("service_id_match"))
        or _same_text(service_id, line.get("service_id_normalized"))
        or _same_text(service_id, line.get("service_id_raw")),
        "provider_match": _normalize_bool(row.get("provider_match")) or _same_text(provider, line.get("provider")),
        "billing_period_match": _normalize_bool(row.get("billing_period_match"))
        or _date_in_window(transaction_date, line.get("billing_period_start"), line.get("billing_period_end")),
        "conflicting_candidate": _normalize_bool(row.get("conflicting_candidate")),
        "one_to_many": _normalize_bool(row.get("one_to_many")),
    }


def _execute_sqlite(
    dsn: str, sql: str, params: dict[str, Any], *, timeout_seconds: int, row_limit: int
) -> list[dict[str, Any]]:
    connection = sqlite3.connect(dsn)
    try:
        connection.execute(f"PRAGMA busy_timeout={timeout_seconds * 1000}")
        cursor = connection.execute(sql, params)
        rows = cursor.fetchmany(row_limit + 1)
        if len(rows) > row_limit:
            raise RuntimeError(f"billing_query_row_limit_exceeded: query returned more than {row_limit} rows.")
        return [_row_to_dict(cursor, row) for row in rows]
    finally:
        connection.close()


def _postgres_sql(sql: str) -> str:
    return re.sub(r":([A-Za-z_][A-Za-z0-9_]*)", r"%(\1)s", sql)


def _execute_postgres(
    dsn: str, sql: str, params: dict[str, Any], *, timeout_seconds: int, row_limit: int
) -> list[dict[str, Any]]:
    try:
        import psycopg  # type: ignore
        from psycopg.rows import dict_row  # type: ignore
    except ImportError as exc:
        raise RuntimeError("psycopg is required for NEXON_RECON_BILLING_MODE=postgres.") from exc

    with psycopg.connect(dsn, row_factory=dict_row) as connection:
        connection.execute(f"SET statement_timeout = {int(timeout_seconds * 1000)}")
        connection.execute("SET TRANSACTION READ ONLY")
        with connection.cursor() as cursor:
            cursor.execute(_postgres_sql(sql), params)
            rows = cursor.fetchmany(row_limit + 1)
            if len(rows) > row_limit:
                raise RuntimeError(f"billing_query_row_limit_exceeded: query returned more than {row_limit} rows.")
            return [dict(row) for row in rows]


def _qmark_sql(sql: str, params: dict[str, Any]) -> tuple[str, list[Any]]:
    ordered: list[Any] = []

    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in params:
            raise RuntimeError(f"billing_query_parameter_missing: {key}")
        ordered.append(params[key])
        return "?"

    return re.sub(r":([A-Za-z_][A-Za-z0-9_]*)", replace, sql), ordered


def _execute_sqlserver(
    dsn: str, sql: str, params: dict[str, Any], *, timeout_seconds: int, row_limit: int
) -> list[dict[str, Any]]:
    try:
        import pyodbc  # type: ignore
    except ImportError as exc:
        raise RuntimeError("pyodbc is required for NEXON_RECON_BILLING_MODE=sqlserver.") from exc
    query, values = _qmark_sql(sql, params)
    connection = pyodbc.connect(dsn, readonly=True, autocommit=False, timeout=timeout_seconds)
    try:
        cursor = connection.cursor()
        cursor.timeout = timeout_seconds
        cursor.execute("SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED")
        cursor.execute(query, values)
        rows = cursor.fetchmany(row_limit + 1)
        if len(rows) > row_limit:
            raise RuntimeError(f"billing_query_row_limit_exceeded: query returned more than {row_limit} rows.")
        return [_row_to_dict(cursor, row) for row in rows]
    finally:
        connection.rollback()
        connection.close()


def _execute_query(
    sql: str, params: dict[str, Any], *, timeout_seconds: int = 30, row_limit: int = 5000
) -> list[dict[str, Any]]:
    mode = os.environ.get("NEXON_RECON_BILLING_MODE", "sqlite").strip().lower()
    dsn = os.environ.get("NEXON_RECON_BILLING_DSN")
    if not dsn:
        raise RuntimeError("billing_profile_missing: Set NEXON_RECON_BILLING_DSN in the approved runtime profile.")
    if mode == "sqlite":
        return _execute_sqlite(dsn, sql, params, timeout_seconds=timeout_seconds, row_limit=row_limit)
    if mode in {"postgres", "postgresql", "inomial_postgres"}:
        return _execute_postgres(dsn, sql, params, timeout_seconds=timeout_seconds, row_limit=row_limit)
    if mode in {"sqlserver", "azure_sql"}:
        return _execute_sqlserver(dsn, sql, params, timeout_seconds=timeout_seconds, row_limit=row_limit)
    raise RuntimeError(f"Unsupported billing mode: {mode}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only billing query adapter.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--normalized", type=Path, required=True)
    sql_group = parser.add_mutually_exclusive_group(required=True)
    sql_group.add_argument("--sql-file", type=Path)
    sql_group.add_argument("--sql")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--query-log", type=Path, required=True)
    parser.add_argument("--line-ids-file", type=Path)
    parser.add_argument("--exception-input", type=Path)
    parser.add_argument("--audit-manifest", type=Path)
    parser.add_argument("--query-round", type=int)
    parser.add_argument("--query-budget", type=int)
    args = parser.parse_args()

    config = load_config(args.config)
    _assert_billing_config(config)

    normalized = read_json(args.normalized)
    lines = normalized.get("lines", [])
    if not isinstance(lines, list):
        raise RuntimeError("billing_query_scope_invalid: normalized lines must be a list.")
    run_ids = {str(line.get("run_id") or "") for line in lines}
    providers = {str(line.get("provider") or "") for line in lines}
    if len(run_ids) != 1 or "" in run_ids or len(providers) != 1 or "" in providers:
        raise RuntimeError("billing_query_scope_invalid: normalized rows must belong to one run/provider.")
    run_id = next(iter(run_ids))
    provider = next(iter(providers))
    if args.line_ids_file:
        requested_payload = read_json(args.line_ids_file)
        requested = (
            requested_payload.get("line_ids", requested_payload)
            if isinstance(requested_payload, dict)
            else requested_payload
        )
        if not isinstance(requested, list) or not requested:
            raise RuntimeError("billing_query_scope_invalid: line_ids must be a non-empty list.")
        requested_ids = {str(value) for value in requested}
        if len(requested_ids) != len(requested):
            raise RuntimeError("billing_query_scope_invalid: line_ids must be unique.")
        known_ids = {str(line.get("line_id")) for line in lines}
        if not requested_ids.issubset(known_ids):
            raise RuntimeError("billing_query_scope_invalid: line_ids include unknown rows.")
        lines = [line for line in lines if str(line.get("line_id")) in requested_ids]
        exception_input = read_json(args.exception_input) if args.exception_input else None
        if not isinstance(exception_input, dict):
            raise RuntimeError("billing_query_scope_invalid: evidence queries require --exception-input.")
        if exception_input.get("run_id") != run_id:
            raise RuntimeError("billing_query_scope_invalid: exception input run_id does not match.")
        if args.audit_manifest is None:
            raise RuntimeError("billing_query_scope_invalid: evidence queries require --audit-manifest.")
        unresolved_ids = {
            str(row.get("line_id"))
            for row in exception_input.get("rows", [])
            if isinstance(row, dict)
        }
        if not requested_ids.issubset(unresolved_ids):
            raise RuntimeError("billing_query_scope_invalid: line_ids are not an unresolved subset.")
        log_identity = exception_input.get("query_log_identity", {})
        if Path(str(log_identity.get("path") or "")).resolve() != args.query_log.resolve():
            raise RuntimeError("billing_query_scope_invalid: query log path does not match exception provenance.")
        if args.query_round is None or args.query_budget is None:
            raise RuntimeError("billing_query_scope_invalid: evidence queries require round and budget.")
        configured_budget = positive_limit(config, "investigation_query_rounds", 2)
        if args.query_budget != configured_budget:
            raise RuntimeError("billing_query_scope_invalid: query budget must equal configured limit.")
        existing_log = read_json(args.query_log) if args.query_log.is_file() else []
        initial_count = int(log_identity.get("chunk_count", -1))
        if (
            initial_count < 1
            or len(existing_log) < initial_count
            or _json_payload_hash(existing_log[:initial_count]) != log_identity.get("sha256")
        ):
            raise RuntimeError("billing_query_scope_invalid: initial query log provenance is invalid.")
        used_rounds = {
            int(item["query_round"])
            for item in existing_log
            if isinstance(item, dict) and item.get("query_round") is not None
        }
        expected_round = max(used_rounds, default=0) + 1
        if (
            args.query_round < 1
            or args.query_round > configured_budget
            or args.query_round != expected_round
        ):
            raise RuntimeError("billing_query_scope_invalid: query round exceeds the evidence budget.")
    elif args.query_round is not None or args.query_budget is not None:
        raise RuntimeError("billing_query_scope_invalid: query round is valid only with --line-ids-file.")
    sql, sql_source = _load_sql(args.sql, args.sql_file)
    _assert_read_only_sql(sql)
    billing_mode = os.environ.get("NEXON_RECON_BILLING_MODE", "sqlite").strip().lower()
    sql = _scoped_sql(sql, billing_mode)
    query_hash = _sql_hash(sql)
    batch_size = positive_limit(config, "investigation_rows_per_batch", 100)
    row_limit = positive_limit(config, "billing_query_row_limit", 5000)
    timeout_seconds = positive_limit(config, "billing_query_timeout_seconds", 30)
    candidate_limit = positive_limit(config, "investigation_candidates_per_row", 20)

    candidates_by_line: dict[str, list[dict[str, Any]]] = {}
    query_log: list[dict[str, Any]] = (
        read_json(args.query_log)
        if args.line_ids_file and args.query_log.is_file()
        else []
    )
    grouped: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for line in lines:
        grouped[_chunk_key(line)].append(line)
    chunk_index = len(query_log)
    for group_lines in grouped.values():
        for start in range(0, len(group_lines), batch_size):
            chunk_index += 1
            chunk = group_lines[start : start + batch_size]
            params = _chunk_params(chunk)
            started = time.monotonic()
            raw_rows = _execute_query(
                sql,
                params,
                timeout_seconds=timeout_seconds,
                row_limit=row_limit,
            )
            duration_ms = round((time.monotonic() - started) * 1000, 3)
            by_service: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for row in raw_rows:
                service_id = row.get("service_id") or row.get("carrier_service_id") or row.get("circuit_id") or row.get("line_number")
                by_service[str(service_id or "").strip().lower()].append(row)
            for line in chunk:
                line_id = str(line.get("line_id", ""))
                service_keys = {
                    str(line.get("service_id_raw") or "").strip().lower(),
                    str(line.get("service_id_normalized") or "").strip().lower(),
                }
                line_rows: list[dict[str, Any]] = []
                for service_key in service_keys - {""}:
                    line_rows.extend(by_service.get(service_key, []))
                unique_rows = list({str(row): row for row in line_rows}.values())
                if len(unique_rows) > candidate_limit:
                    raise RuntimeError(
                        f"billing_query_candidate_limit_exceeded: line {line_id} has more than "
                        f"{candidate_limit} candidates."
                    )
                candidates_by_line[line_id] = [
                    _candidate_from_row(line, row, index) for index, row in enumerate(unique_rows)
                ]
            query_log.append(
                {
                    "chunk_id": chunk_index,
                    "line_count": len(chunk),
                    "billing_mode": os.environ.get("NEXON_RECON_BILLING_MODE", "sqlite"),
                    "sql_source": sql_source,
                    "sql_hash": query_hash,
                    "read_only_validation": "passed",
                    "parameter_keys": sorted(key for key, value in params.items() if value not in (None, "")),
                    "parameter_hashes": _param_hashes(params),
                    "row_count": len(raw_rows),
                    "duration_ms": duration_ms,
                    "timeout_seconds": timeout_seconds,
                    "row_limit": row_limit,
                    "candidate_limit_per_line": candidate_limit,
                    "query_round": args.query_round,
                    "query_budget": args.query_budget,
                }
            )

    write_json(
        args.output,
        {
            "run_id": run_id,
            "provider": provider,
            "billing_mode": "read_only_sql",
            "sql_source": sql_source,
            "sql_hash": query_hash,
            "candidates_by_line": candidates_by_line,
        },
    )
    write_json(args.query_log, query_log)
    if args.line_ids_file:
        audit = read_json(args.audit_manifest)
        if audit.get("run_id") != run_id:
            raise RuntimeError("billing_query_scope_invalid: audit manifest run_id does not match.")
        audit["query_logs"] = [
            {
                "path": str(args.query_log.resolve()),
                "sha256": _json_payload_hash(query_log),
                "chunk_count": len(query_log),
            }
        ]
        write_json(args.audit_manifest, audit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
