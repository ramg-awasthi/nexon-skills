from __future__ import annotations

import argparse
import os
import sqlite3
from pathlib import Path
from typing import Any

from .common import DEFAULT_CONFIG_PATH, load_config, read_json, write_json
from .sqlserver_persistence import persist_sqlserver_run

STATUS_IDS = {
    "Matched": 1,
    "Not Matched": 2,
    "Supplier Only": 3,
    "Billing System Only": 4,
    "Dispute": 5,
    "Manual Matched": 6,
    "Billing Initiated": 8,
    "Service Cancelled": 9,
}

SHADOW_SCHEMA = """
CREATE TABLE IF NOT EXISTS AccountPayableReconRequest (
    Id INTEGER PRIMARY KEY AUTOINCREMENT,
    RequestKey TEXT NOT NULL UNIQUE,
    ServiceProviderAccountId INTEGER NOT NULL,
    ReconRunUTCDateTime TEXT NOT NULL,
    PeriodStartDate TEXT NOT NULL,
    PeriodEndDate TEXT NOT NULL,
    OneDrivePath TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS GenericSupplierInvoice (
    Id INTEGER PRIMARY KEY AUTOINCREMENT,
    InvoiceIdentity TEXT NOT NULL UNIQUE,
    AccountPayableReconRequestId INTEGER NOT NULL,
    ServiceProviderAccountId INTEGER NOT NULL,
    ServiceProviderInvoiceNumber TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS GenericSupplierInvoiceLineItem (
    Id INTEGER PRIMARY KEY AUTOINCREMENT,
    LineId TEXT NOT NULL UNIQUE,
    GenericSupplierInvoiceId INTEGER NOT NULL,
    ServiceNumber TEXT NOT NULL,
    ChargeType TEXT,
    DetailDescription TEXT,
    NexonCustomerReference TEXT,
    InvoiceStartDate TEXT,
    InvoiceEndDate TEXT,
    AmountExclGST TEXT,
    InfrastructureCost TEXT,
    ServiceType TEXT,
    ServiceLocationCenter TEXT
);
CREATE TABLE IF NOT EXISTS GenericNexonBilling (
    Id INTEGER PRIMARY KEY AUTOINCREMENT,
    CandidateKey TEXT NOT NULL UNIQUE,
    AccountPayableReconRequestId INTEGER NOT NULL,
    BillingSystemId INTEGER NOT NULL,
    InvoiceNumber TEXT NOT NULL,
    CustomerName TEXT NOT NULL,
    AccountNumber TEXT NOT NULL,
    ServiceNumber TEXT,
    ServiceDescription TEXT,
    BillingDate TEXT NOT NULL,
    AmountExclGST TEXT NOT NULL,
    RecurringAmountExclGST TEXT,
    UsageAmountExclGST TEXT,
    ServiceSpecName TEXT,
    ChargeType TEXT
);
CREATE TABLE IF NOT EXISTS AccountPayableReconResult (
    Id INTEGER PRIMARY KEY AUTOINCREMENT,
    ResultKey TEXT NOT NULL UNIQUE,
    AccountPayableReconRequestId INTEGER NOT NULL,
    GenericSupplierInvoiceLineItemId INTEGER,
    GenericNexonBillingId INTEGER,
    ReconMatchStatusId INTEGER NOT NULL,
    serviceLogin TEXT,
    serviceLastInvoiceDate TEXT,
    serviceLastInvoiceNumber TEXT,
    serviceLastInvoiceAmount TEXT
);
"""


def _required_text(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise RuntimeError(f"core_persistence_invalid_input: {field} is required.")
    return text


def _period(header: dict[str, Any]) -> tuple[str, str]:
    start = _required_text(header.get("billing_period_start"), "billing_period_start")
    end = _required_text(header.get("billing_period_end"), "billing_period_end")
    return start, end


def _shadow_connection(dsn: str) -> sqlite3.Connection:
    connection = sqlite3.connect(dsn)
    connection.row_factory = sqlite3.Row
    connection.executescript(SHADOW_SCHEMA)
    return connection


def _insert_or_get(
    cursor: sqlite3.Cursor,
    insert_sql: str,
    select_sql: str,
    values: tuple[Any, ...],
    immutable_fields: dict[str, Any],
) -> int:
    try:
        cursor.execute(insert_sql, values)
        return int(cursor.lastrowid)
    except sqlite3.IntegrityError:
        cursor.execute(select_sql, (values[0],))
        row = cursor.fetchone()
        if row is None:
            raise
        changed = sorted(
            field
            for field, expected in immutable_fields.items()
            if str(row[field] if row[field] is not None else "")
            != str(expected if expected is not None else "")
        )
        if changed:
            raise RuntimeError(
                f"core_persistence_idempotency_conflict: existing payload differs in {changed}."
            )
        return int(row["Id"])


def persist_shadow_run(
    *,
    dsn: str,
    normalized: dict[str, Any],
    candidates: dict[str, Any],
    matches: dict[str, Any],
    provider_account_id: int,
    run_path: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    headers = normalized.get("invoice_headers", [])
    lines = normalized.get("lines", [])
    match_rows = matches.get("rows", [])
    if not isinstance(headers, list) or not isinstance(lines, list) or not isinstance(match_rows, list):
        raise RuntimeError("core_persistence_invalid_input: normalized and matches payloads are malformed.")
    run_ids = {_required_text(line.get("run_id"), "run_id") for line in lines}
    providers = {_required_text(line.get("provider"), "provider") for line in lines}
    request_keys = {
        _required_text(header.get("request_key"), "request_key") for header in headers
    }
    periods = {_period(header) for header in headers}
    if (
        len(run_ids) != 1
        or len(providers) != 1
        or len(request_keys) != 1
        or len(periods) != 1
    ):
        raise RuntimeError(
            "core_persistence_invalid_input: one run must map to one provider, request, and period."
        )
    run_id = next(iter(run_ids))
    provider = next(iter(providers))
    if Path(run_path).name != run_id:
        raise RuntimeError("core_persistence_invalid_input: run_path does not match row run_id.")
    if candidates.get("run_id") != run_id or candidates.get("provider") != provider:
        raise RuntimeError("core_persistence_invalid_input: candidate evidence identity does not match.")
    if any(
        row.get("run_id") != run_id or row.get("provider") != provider
        for row in match_rows
    ):
        raise RuntimeError("core_persistence_invalid_input: match rows are not bound to the run/provider.")
    if any(
        not str(header.get("request_key") or "").startswith(f"{run_id}:")
        for header in headers
    ):
        raise RuntimeError("core_persistence_invalid_input: invoice request keys are not run-scoped.")
    line_by_id = {_required_text(line.get("line_id"), "line_id"): line for line in lines}
    match_line_ids = [_required_text(row.get("line_id"), "line_id") for row in match_rows]
    if set(line_by_id) != set(match_line_ids):
        raise RuntimeError(
            "core_persistence_invalid_input: every parser line must have at least one result."
        )

    connection = _shadow_connection(dsn)
    request_ids: dict[str, int] = {}
    invoice_ids: dict[str, int] = {}
    line_item_ids: dict[str, int] = {}
    billing_ids: dict[str, int] = {}
    result_ids: dict[str, int] = {}
    try:
        cursor = connection.cursor()
        cursor.execute("BEGIN IMMEDIATE")
        for header in headers:
            request_key = _required_text(header.get("request_key"), "request_key")
            invoice_identity = _required_text(header.get("invoice_identity"), "invoice_identity")
            invoice_storage_key = f"{request_key}:{invoice_identity}"
            start, end = _period(header)
            request_id = _insert_or_get(
                cursor,
                """
                INSERT INTO AccountPayableReconRequest
                    (RequestKey, ServiceProviderAccountId, ReconRunUTCDateTime, PeriodStartDate, PeriodEndDate, OneDrivePath)
                VALUES (?, ?, CURRENT_TIMESTAMP, ?, ?, ?)
                """,
                "SELECT * FROM AccountPayableReconRequest WHERE RequestKey = ?",
                (request_key, provider_account_id, start, end, run_path),
                {
                    "ServiceProviderAccountId": provider_account_id,
                    "PeriodStartDate": start,
                    "PeriodEndDate": end,
                    "OneDrivePath": run_path,
                },
            )
            request_ids[request_key] = request_id
            invoice_id = _insert_or_get(
                cursor,
                """
                INSERT INTO GenericSupplierInvoice
                    (InvoiceIdentity, AccountPayableReconRequestId, ServiceProviderAccountId, ServiceProviderInvoiceNumber)
                VALUES (?, ?, ?, ?)
                """,
                "SELECT * FROM GenericSupplierInvoice WHERE InvoiceIdentity = ?",
                (
                    invoice_storage_key,
                    request_id,
                    provider_account_id,
                    _required_text(header.get("invoice_number"), "invoice_number"),
                ),
                {
                    "AccountPayableReconRequestId": request_id,
                    "ServiceProviderAccountId": provider_account_id,
                    "ServiceProviderInvoiceNumber": _required_text(
                        header.get("invoice_number"), "invoice_number"
                    ),
                },
            )
            invoice_ids[invoice_identity] = invoice_id

        for line_id, line in line_by_id.items():
            invoice_identity = _required_text(line.get("invoice_identity"), "invoice_identity")
            invoice_id = invoice_ids.get(invoice_identity)
            if invoice_id is None:
                raise RuntimeError(f"core_persistence_invalid_input: missing invoice header for line {line_id}.")
            line_item_ids[line_id] = _insert_or_get(
                cursor,
                """
                INSERT INTO GenericSupplierInvoiceLineItem
                    (LineId, GenericSupplierInvoiceId, ServiceNumber, ChargeType, DetailDescription,
                     NexonCustomerReference, InvoiceStartDate, InvoiceEndDate, AmountExclGST,
                     InfrastructureCost, ServiceType, ServiceLocationCenter)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                "SELECT * FROM GenericSupplierInvoiceLineItem WHERE LineId = ?",
                (
                    line_id,
                    invoice_id,
                    _required_text(line.get("service_id_normalized") or line.get("service_id_raw"), "service_number"),
                    line.get("charge_type"),
                    line.get("detail_description"),
                    line.get("nexon_customer_reference"),
                    line.get("billing_period_start"),
                    line.get("billing_period_end"),
                    line.get("supplier_amount") or line.get("amount"),
                    line.get("infrastructure_cost"),
                    line.get("service_type"),
                    line.get("service_location_center"),
                ),
                {
                    "GenericSupplierInvoiceId": invoice_id,
                    "ServiceNumber": _required_text(
                        line.get("service_id_normalized") or line.get("service_id_raw"),
                        "service_number",
                    ),
                    "ChargeType": line.get("charge_type"),
                    "DetailDescription": line.get("detail_description"),
                    "NexonCustomerReference": line.get("nexon_customer_reference"),
                    "InvoiceStartDate": line.get("billing_period_start"),
                    "InvoiceEndDate": line.get("billing_period_end"),
                    "AmountExclGST": line.get("supplier_amount") or line.get("amount"),
                    "InfrastructureCost": line.get("infrastructure_cost"),
                    "ServiceType": line.get("service_type"),
                    "ServiceLocationCenter": line.get("service_location_center"),
                },
            )

        candidates_by_line = candidates.get("candidates_by_line", {})
        for line_id, candidate_rows in candidates_by_line.items():
            line = line_by_id.get(str(line_id))
            if line is None:
                raise RuntimeError(
                    "core_persistence_invalid_input: candidate evidence references an unknown line."
                )
            request_id = request_ids[_required_text(line.get("request_key"), "request_key")]
            for index, candidate in enumerate(candidate_rows):
                candidate_identity = str(candidate.get("candidate_id") or f"{line_id}:candidate:{index}")
                candidate_key = f"{request_id}:{candidate_identity}"
                billing_ids[candidate_key] = _insert_or_get(
                    cursor,
                    """
                    INSERT INTO GenericNexonBilling
                        (CandidateKey, AccountPayableReconRequestId, BillingSystemId, InvoiceNumber,
                         CustomerName, AccountNumber, ServiceNumber, ServiceDescription, BillingDate,
                         AmountExclGST, RecurringAmountExclGST, UsageAmountExclGST, ServiceSpecName, ChargeType)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    "SELECT * FROM GenericNexonBilling WHERE CandidateKey = ?",
                    (
                        candidate_key,
                        request_id,
                        int(candidate.get("billing_system_id") or 5),
                        _required_text(
                            candidate.get("invoice_number") or candidate.get("customer_invoice_number"),
                            "candidate.invoice_number",
                        ),
                        _required_text(
                            candidate.get("customer_name") or candidate.get("customer_account"),
                            "candidate.customer_name",
                        ),
                        _required_text(
                            candidate.get("account_number") or candidate.get("customer_account"),
                            "candidate.account_number",
                        ),
                        candidate.get("service_number") or candidate.get("service_id"),
                        candidate.get("service_description"),
                        _required_text(
                            candidate.get("billing_date") or candidate.get("transaction_date"),
                            "candidate.billing_date",
                        ),
                        _required_text(
                            candidate.get("amount_excl_gst") or candidate.get("customer_invoice_amount"),
                            "candidate.amount_excl_gst",
                        ),
                        candidate.get("recurring_amount_excl_gst"),
                        candidate.get("usage_amount_excl_gst"),
                        candidate.get("service_spec_name"),
                        candidate.get("charge_type"),
                    ),
                    {
                        "AccountPayableReconRequestId": request_id,
                        "BillingSystemId": int(candidate.get("billing_system_id") or 5),
                        "InvoiceNumber": _required_text(
                            candidate.get("invoice_number")
                            or candidate.get("customer_invoice_number"),
                            "candidate.invoice_number",
                        ),
                        "CustomerName": _required_text(
                            candidate.get("customer_name")
                            or candidate.get("customer_account"),
                            "candidate.customer_name",
                        ),
                        "AccountNumber": _required_text(
                            candidate.get("account_number")
                            or candidate.get("customer_account"),
                            "candidate.account_number",
                        ),
                        "ServiceNumber": candidate.get("service_number")
                        or candidate.get("service_id"),
                        "ServiceDescription": candidate.get("service_description"),
                        "BillingDate": _required_text(
                            candidate.get("billing_date")
                            or candidate.get("transaction_date"),
                            "candidate.billing_date",
                        ),
                        "AmountExclGST": _required_text(
                            candidate.get("amount_excl_gst")
                            or candidate.get("customer_invoice_amount"),
                            "candidate.amount_excl_gst",
                        ),
                        "RecurringAmountExclGST": candidate.get(
                            "recurring_amount_excl_gst"
                        ),
                        "UsageAmountExclGST": candidate.get("usage_amount_excl_gst"),
                        "ServiceSpecName": candidate.get("service_spec_name"),
                        "ChargeType": candidate.get("charge_type"),
                    },
                )

        persisted_rows: list[dict[str, Any]] = []
        for result_ordinal, row in enumerate(match_rows):
            line_id = _required_text(row.get("line_id"), "line_id")
            line = line_by_id[line_id]
            request_id = request_ids[_required_text(line.get("request_key"), "request_key")]
            candidate = row.get("candidate_snapshot", {})
            if not isinstance(candidate, dict):
                candidate = {}
            candidate_identity = str(candidate.get("candidate_id") or "")
            candidate_key = f"{request_id}:{candidate_identity}" if candidate_identity else ""
            billing_id = billing_ids.get(candidate_key)
            status = _required_text(row.get("ReconMatchStatus"), "ReconMatchStatus")
            if status not in STATUS_IDS:
                raise RuntimeError(f"core_persistence_invalid_input: unsupported ReconMatchStatus {status!r}.")
            result_key = f"{request_id}:{line_item_ids[line_id]}:{result_ordinal}"
            result_id = _insert_or_get(
                cursor,
                """
                INSERT INTO AccountPayableReconResult
                    (ResultKey, AccountPayableReconRequestId, GenericSupplierInvoiceLineItemId,
                     GenericNexonBillingId, ReconMatchStatusId, serviceLogin, serviceLastInvoiceDate,
                     serviceLastInvoiceNumber, serviceLastInvoiceAmount)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                "SELECT * FROM AccountPayableReconResult WHERE ResultKey = ?",
                (
                    result_key,
                    request_id,
                    line_item_ids[line_id],
                    billing_id,
                    STATUS_IDS[status],
                    candidate.get("service_login"),
                    candidate.get("service_last_invoice_date"),
                    candidate.get("service_last_invoice_number"),
                    candidate.get("service_last_invoice_amount"),
                ),
                {
                    "AccountPayableReconRequestId": request_id,
                    "GenericSupplierInvoiceLineItemId": line_item_ids[line_id],
                    "GenericNexonBillingId": billing_id,
                    "ReconMatchStatusId": STATUS_IDS[status],
                    "serviceLogin": candidate.get("service_login"),
                    "serviceLastInvoiceDate": candidate.get(
                        "service_last_invoice_date"
                    ),
                    "serviceLastInvoiceNumber": candidate.get(
                        "service_last_invoice_number"
                    ),
                    "serviceLastInvoiceAmount": candidate.get(
                        "service_last_invoice_amount"
                    ),
                },
            )
            result_ids[line_id] = result_id
            output = dict(row)
            output["AccountPayableReconRequestId"] = request_id
            output["GenericSupplierInvoiceLineItemId"] = line_item_ids[line_id]
            output["GenericNexonBillingId"] = billing_id or ""
            persisted_rows.append(output)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    manifest = {
        "run_id": run_id,
        "provider": provider,
        "mode": "sqlite_shadow",
        "transaction": "committed",
        "request_count": len(request_ids),
        "invoice_count": len(invoice_ids),
        "supplier_line_count": len(line_item_ids),
        "billing_candidate_count": len(billing_ids),
        "result_count": len(result_ids),
        "idempotency_scope": "request_key/invoice_identity/line_id/candidate_id/result_key",
    }
    return {"rows": persisted_rows}, manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Transactional reconciliation persistence adapter.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--normalized", type=Path, required=True)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--matches", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--provider-account-id", type=int, required=True)
    parser.add_argument("--run-path", required=True)
    args = parser.parse_args()

    config = load_config(args.config)
    if config.get("features", {}).get("core_persistence_enabled") is not True:
        raise RuntimeError("core_persistence_not_available: feature is disabled.")
    mode = os.environ.get("NEXON_RECON_CORE_MODE", "").strip().lower()
    dsn = os.environ.get("NEXON_RECON_CORE_DSN", "").strip()
    if not dsn:
        raise RuntimeError("core_persistence_not_available: NEXON_RECON_CORE_DSN is missing.")
    payload = {
        "dsn": dsn,
        "normalized": read_json(args.normalized),
        "candidates": read_json(args.candidates),
        "matches": read_json(args.matches),
        "provider_account_id": args.provider_account_id,
        "run_path": args.run_path,
    }
    if mode == "sqlite_shadow":
        persisted, manifest = persist_shadow_run(**payload)
    elif mode in {"sqlserver", "azure_sql"}:
        persisted, manifest = persist_sqlserver_run(**payload)
    else:
        raise RuntimeError(
            "core_persistence_not_available: NEXON_RECON_CORE_MODE must be "
            "sqlite_shadow, sqlserver, or azure_sql."
        )
    write_json(args.output, persisted)
    write_json(args.manifest, manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
