from __future__ import annotations

import argparse
import hashlib
import os
import re
import sqlite3
import time
from datetime import date
from pathlib import Path
from typing import Any

from .common import DEFAULT_CONFIG_PATH, load_config, read_json, write_json

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


def _load_sql(sql: str | None, sql_file: Path | None) -> tuple[str, str]:
    if sql_file:
        return sql_file.read_text(encoding="utf-8"), str(sql_file)
    if sql:
        return sql, "inline"
    raise RuntimeError("billing_query_sql_missing: Provide --sql-file or --sql.")


def _sql_hash(sql: str) -> str:
    return hashlib.sha256(sql.encode("utf-8")).hexdigest()


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
    return {
        **row,
        "candidate_id": row.get("candidate_id") or f"{line.get('line_id')}-candidate-{index + 1}",
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


def _execute_sqlite(dsn: str, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    connection = sqlite3.connect(dsn)
    try:
        cursor = connection.execute(sql, params)
        return [_row_to_dict(cursor, row) for row in cursor.fetchall()]
    finally:
        connection.close()


def _postgres_sql(sql: str) -> str:
    return re.sub(r":([A-Za-z_][A-Za-z0-9_]*)", r"%(\1)s", sql)


def _execute_postgres(dsn: str, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        import psycopg  # type: ignore
        from psycopg.rows import dict_row  # type: ignore
    except ImportError as exc:
        raise RuntimeError("psycopg is required for NEXON_RECON_BILLING_MODE=postgres.") from exc

    with psycopg.connect(dsn, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(_postgres_sql(sql), params)
            return [dict(row) for row in cursor.fetchall()]


def _execute_query(sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    mode = os.environ.get("NEXON_RECON_BILLING_MODE", "sqlite").strip().lower()
    dsn = os.environ.get("NEXON_RECON_BILLING_DSN")
    if not dsn:
        raise RuntimeError("billing_profile_missing: Set NEXON_RECON_BILLING_DSN in the approved runtime profile.")
    if mode == "sqlite":
        return _execute_sqlite(dsn, sql, params)
    if mode in {"postgres", "postgresql", "inomial_postgres"}:
        return _execute_postgres(dsn, sql, params)
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
    args = parser.parse_args()

    config = load_config(args.config)
    _assert_billing_config(config)

    normalized = read_json(args.normalized)
    lines = normalized.get("lines", [])
    sql, sql_source = _load_sql(args.sql, args.sql_file)
    query_hash = _sql_hash(sql)
    _assert_read_only_sql(sql)

    candidates_by_line: dict[str, list[dict[str, Any]]] = {}
    query_log: list[dict[str, Any]] = []
    for line in lines:
        params = _line_params(line)
        started = time.monotonic()
        raw_rows = _execute_query(sql, params)
        duration_ms = round((time.monotonic() - started) * 1000, 3)
        candidates = [_candidate_from_row(line, row, index) for index, row in enumerate(raw_rows)]
        line_id = str(line.get("line_id", ""))
        candidates_by_line[line_id] = candidates
        query_log.append(
            {
                "line_id": line_id,
                "billing_mode": "read_only_sql",
                "sql_source": sql_source,
                "sql_hash": query_hash,
                "read_only_validation": "passed",
                "parameter_keys": sorted(key for key, value in params.items() if value not in (None, "")),
                "parameter_hashes": _param_hashes(params),
                "row_count": len(candidates),
                "duration_ms": duration_ms,
            }
        )

    write_json(
        args.output,
        {
            "billing_mode": "read_only_sql",
            "sql_source": sql_source,
            "sql_hash": query_hash,
            "candidates_by_line": candidates_by_line,
        },
    )
    write_json(args.query_log, query_log)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
