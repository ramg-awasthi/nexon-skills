from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


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

SCHEMA = "Finance"


def _required_text(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise RuntimeError(f"core_persistence_invalid_input: {field} is required.")
    return text


def _normalized_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        if value.time().isoformat() == "00:00:00":
            return value.date().isoformat()
        return value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (Decimal, float, int)) and not isinstance(value, bool):
        return format(Decimal(str(value)).normalize(), "f")
    text = str(value)
    try:
        if text and all(character in "+-.0123456789" for character in text):
            return format(Decimal(text).normalize(), "f")
    except InvalidOperation:
        pass
    return text


def _date_value(value: Any, field: str) -> date:
    text = _required_text(value, field)
    try:
        return date.fromisoformat(text[:10])
    except ValueError as exc:
        raise RuntimeError(
            f"core_persistence_invalid_input: {field} must be an ISO date."
        ) from exc


def _financial_period(value: Any) -> tuple[str, str, int]:
    billing_date = _date_value(value, "candidate.billing_date")
    financial_start = (
        billing_date.year if billing_date.month >= 7 else billing_date.year - 1
    )
    quarter = ((billing_date.month - 7) % 12) // 3 + 1
    return (
        f"{financial_start}Q{quarter}",
        f"FY{financial_start}-{financial_start + 1}",
        billing_date.month,
    )


def _row_dict(cursor: Any, row: Any) -> dict[str, Any]:
    columns = [item[0] for item in cursor.description]
    return {columns[index]: row[index] for index in range(len(columns))}


def _fetch_all(cursor: Any, sql: str, values: tuple[Any, ...]) -> list[dict[str, Any]]:
    cursor.execute(sql, values)
    return [_row_dict(cursor, row) for row in cursor.fetchall()]


def _insert_id(cursor: Any, sql: str, values: tuple[Any, ...]) -> int:
    cursor.execute(sql, values)
    row = cursor.fetchone()
    if row is None:
        raise RuntimeError("core_persistence_write_failed: INSERT returned no identity.")
    return int(row[0])


def _assert_equal(
    actual: dict[str, Any],
    expected: dict[str, Any],
    *,
    identity: str,
) -> None:
    changed = sorted(
        key
        for key, expected_value in expected.items()
        if _normalized_value(actual.get(key)) != _normalized_value(expected_value)
    )
    if changed:
        raise RuntimeError(
            "core_persistence_idempotency_conflict: "
            f"{identity} differs in {changed}."
        )


def _prepare(
    *,
    normalized: dict[str, Any],
    candidates: dict[str, Any],
    matches: dict[str, Any],
    provider_account_id: int,
    run_path: str,
) -> dict[str, Any]:
    headers = normalized.get("invoice_headers", [])
    lines = normalized.get("lines", [])
    match_rows = matches.get("rows", [])
    if (
        not isinstance(headers, list)
        or not isinstance(lines, list)
        or not isinstance(match_rows, list)
        or not headers
        or not lines
    ):
        raise RuntimeError(
            "core_persistence_invalid_input: normalized and matches payloads are malformed."
        )
    run_ids = {_required_text(line.get("run_id"), "run_id") for line in lines}
    providers = {_required_text(line.get("provider"), "provider") for line in lines}
    request_keys = {
        _required_text(header.get("request_key"), "request_key") for header in headers
    }
    periods = {
        (
            _required_text(header.get("billing_period_start"), "billing_period_start"),
            _required_text(header.get("billing_period_end"), "billing_period_end"),
        )
        for header in headers
    }
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
        raise RuntimeError(
            "core_persistence_invalid_input: run_path does not match row run_id."
        )
    if candidates.get("run_id") != run_id or candidates.get("provider") != provider:
        raise RuntimeError(
            "core_persistence_invalid_input: candidate evidence identity does not match."
        )
    if any(
        row.get("run_id") != run_id or row.get("provider") != provider
        for row in match_rows
    ):
        raise RuntimeError(
            "core_persistence_invalid_input: match rows are not bound to the run/provider."
        )

    line_by_id = {
        _required_text(line.get("line_id"), "line_id"): line for line in lines
    }
    result_line_ids = [
        _required_text(row.get("line_id"), "line_id") for row in match_rows
    ]
    if set(result_line_ids) != set(line_by_id):
        raise RuntimeError(
            "core_persistence_invalid_input: every parser line must have at least one result."
        )

    header_identities = {
        _required_text(header.get("invoice_identity"), "invoice_identity")
        for header in headers
    }
    if any(
        _required_text(line.get("invoice_identity"), "invoice_identity")
        not in header_identities
        for line in lines
    ):
        raise RuntimeError(
            "core_persistence_invalid_input: a supplier line has no invoice header."
        )

    candidate_rows_by_id: dict[str, tuple[str, dict[str, Any]]] = {}
    candidates_by_line = candidates.get("candidates_by_line", {})
    if not isinstance(candidates_by_line, dict):
        raise RuntimeError(
            "core_persistence_invalid_input: candidates_by_line must be a mapping."
        )
    for line in lines:
        line_id = _required_text(line.get("line_id"), "line_id")
        rows = candidates_by_line.get(line_id, [])
        if not isinstance(rows, list):
            raise RuntimeError(
                "core_persistence_invalid_input: candidate rows must be a list."
            )
        for candidate in rows:
            candidate_id = _required_text(
                candidate.get("candidate_id"), "candidate.candidate_id"
            )
            existing = candidate_rows_by_id.get(candidate_id)
            if existing is not None:
                persisted_fields = (
                    "billing_system_id",
                    "invoice_number",
                    "customer_invoice_number",
                    "customer_name",
                    "customer_account",
                    "account_number",
                    "service_number",
                    "service_id",
                    "service_description",
                    "billing_date",
                    "transaction_date",
                    "amount_excl_gst",
                    "customer_invoice_amount",
                )
                if any(
                    _normalized_value(existing[1].get(field))
                    != _normalized_value(candidate.get(field))
                    for field in persisted_fields
                ):
                    raise RuntimeError(
                        "core_persistence_invalid_input: duplicate candidate_id "
                        "has conflicting persisted values."
                    )
                continue
            candidate_rows_by_id[candidate_id] = (line_id, candidate)

    start, end = next(iter(periods))
    return {
        "run_id": run_id,
        "provider": provider,
        "provider_account_id": provider_account_id,
        "run_path": run_path,
        "period_start": start,
        "period_end": end,
        "headers": headers,
        "lines": lines,
        "line_by_id": line_by_id,
        "candidate_rows": list(candidate_rows_by_id.values()),
        "match_rows": match_rows,
    }


def _connect(dsn: str, timeout_seconds: int) -> Any:
    try:
        import pyodbc  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "pyodbc is required for NEXON_RECON_CORE_MODE=sqlserver."
        ) from exc
    return pyodbc.connect(
        dsn,
        readonly=False,
        autocommit=False,
        timeout=timeout_seconds,
    )


def persist_sqlserver_run(
    *,
    dsn: str,
    normalized: dict[str, Any],
    candidates: dict[str, Any],
    matches: dict[str, Any],
    provider_account_id: int,
    run_path: str,
    timeout_seconds: int = 30,
) -> tuple[dict[str, Any], dict[str, Any]]:
    prepared = _prepare(
        normalized=normalized,
        candidates=candidates,
        matches=matches,
        provider_account_id=provider_account_id,
        run_path=run_path,
    )
    connection = _connect(dsn, timeout_seconds)
    cursor = connection.cursor()
    cursor.timeout = timeout_seconds
    inserted = False
    try:
        cursor.execute("SET XACT_ABORT ON")
        cursor.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
        cursor.execute("BEGIN TRANSACTION")
        existing_requests = _fetch_all(
            cursor,
            f"""
            SELECT Id, ServiceProviderAccountId, PeriodStartDate, PeriodEndDate, OneDrivePath
            FROM [{SCHEMA}].[AccountPayableReconRequest] WITH (UPDLOCK, HOLDLOCK)
            WHERE OneDrivePath = ?
            """,
            (run_path,),
        )
        if len(existing_requests) > 1:
            raise RuntimeError(
                "core_persistence_idempotency_conflict: multiple requests use the run path."
            )
        if existing_requests:
            request_id = int(existing_requests[0]["Id"])
            _assert_equal(
                existing_requests[0],
                {
                    "ServiceProviderAccountId": provider_account_id,
                    "PeriodStartDate": prepared["period_start"],
                    "PeriodEndDate": prepared["period_end"],
                    "OneDrivePath": run_path,
                },
                identity="request",
            )
        else:
            request_id = _insert_id(
                cursor,
                f"""
                INSERT INTO [{SCHEMA}].[AccountPayableReconRequest]
                    (ServiceProviderAccountId, ReconRunUTCDateTime, PeriodStartDate,
                     PeriodEndDate, OneDrivePath)
                OUTPUT INSERTED.Id
                VALUES (?, SYSUTCDATETIME(), ?, ?, ?)
                """,
                (
                    provider_account_id,
                    prepared["period_start"],
                    prepared["period_end"],
                    run_path,
                ),
            )
            inserted = True

        invoice_rows = _fetch_all(
            cursor,
            f"""
            SELECT Id, AccountPayableReconRequestId, ServiceProviderAccountId,
                   ServiceProviderInvoiceNumber
            FROM [{SCHEMA}].[GenericSupplierInvoice] WITH (UPDLOCK, HOLDLOCK)
            WHERE AccountPayableReconRequestId = ?
            ORDER BY Id
            """,
            (request_id,),
        )
        invoice_ids: dict[str, int] = {}
        if invoice_rows:
            if len(invoice_rows) != len(prepared["headers"]):
                raise RuntimeError(
                    "core_persistence_idempotency_conflict: invoice count differs."
                )
            for header, actual in zip(prepared["headers"], invoice_rows, strict=True):
                _assert_equal(
                    actual,
                    {
                        "AccountPayableReconRequestId": request_id,
                        "ServiceProviderAccountId": provider_account_id,
                        "ServiceProviderInvoiceNumber": _required_text(
                            header.get("invoice_number"), "invoice_number"
                        ),
                    },
                    identity="invoice",
                )
                invoice_ids[
                    _required_text(header.get("invoice_identity"), "invoice_identity")
                ] = int(actual["Id"])
        else:
            for header in prepared["headers"]:
                invoice_identity = _required_text(
                    header.get("invoice_identity"), "invoice_identity"
                )
                invoice_ids[invoice_identity] = _insert_id(
                    cursor,
                    f"""
                    INSERT INTO [{SCHEMA}].[GenericSupplierInvoice]
                        (AccountPayableReconRequestId, ServiceProviderAccountId,
                         ServiceProviderInvoiceNumber)
                    OUTPUT INSERTED.Id
                    VALUES (?, ?, ?)
                    """,
                    (
                        request_id,
                        provider_account_id,
                        _required_text(header.get("invoice_number"), "invoice_number"),
                    ),
                )

        supplier_rows = _fetch_all(
            cursor,
            f"""
            SELECT li.*
            FROM [{SCHEMA}].[GenericSupplierInvoiceLineItem] li WITH (UPDLOCK, HOLDLOCK)
            INNER JOIN [{SCHEMA}].[GenericSupplierInvoice] si
                ON si.Id = li.GenericSupplierInvoiceId
            WHERE si.AccountPayableReconRequestId = ?
            ORDER BY li.Id
            """,
            (request_id,),
        )
        line_item_ids: dict[str, int] = {}
        if supplier_rows and len(supplier_rows) != len(prepared["lines"]):
            raise RuntimeError(
                "core_persistence_idempotency_conflict: supplier line count differs."
            )
        for index, line in enumerate(prepared["lines"]):
            line_id = _required_text(line.get("line_id"), "line_id")
            invoice_id = invoice_ids[
                _required_text(line.get("invoice_identity"), "invoice_identity")
            ]
            expected = {
                "GenericSupplierInvoiceId": invoice_id,
                "ServiceNumber": _required_text(
                    line.get("service_id_normalized") or line.get("service_id_raw"),
                    "service_number",
                ),
                "ChargeType": line.get("charge_type"),
                "DetailDescription": line.get("detail_description"),
                "NexonCustomerReference": line.get("nexon_customer_reference"),
                "InvoiceStartDate": line.get("billing_period_start") or None,
                "InvoiceEndDate": line.get("billing_period_end") or None,
                "AmountExclGST": line.get("supplier_amount")
                or line.get("amount")
                or None,
                "InfrastructureCost": line.get("infrastructure_cost"),
                "ServiceType": line.get("service_type"),
                "ServiceLocationCenter": line.get("service_location_center"),
            }
            if supplier_rows:
                actual = supplier_rows[index]
                _assert_equal(actual, expected, identity=f"supplier line {line_id}")
                line_item_ids[line_id] = int(actual["Id"])
            else:
                line_item_ids[line_id] = _insert_id(
                    cursor,
                    f"""
                    INSERT INTO [{SCHEMA}].[GenericSupplierInvoiceLineItem]
                        (GenericSupplierInvoiceId, ServiceNumber, ChargeType,
                         DetailDescription, NexonCustomerReference, InvoiceStartDate,
                         InvoiceEndDate, AmountExclGST, InfrastructureCost, ServiceType,
                         ServiceLocationCenter)
                    OUTPUT INSERTED.Id
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    tuple(expected.values()),
                )

        billing_rows = _fetch_all(
            cursor,
            f"""
            SELECT *
            FROM [{SCHEMA}].[GenericNexonBilling] WITH (UPDLOCK, HOLDLOCK)
            WHERE AccountPayableReconRequestId = ?
            ORDER BY Id
            """,
            (request_id,),
        )
        if billing_rows and len(billing_rows) != len(prepared["candidate_rows"]):
            raise RuntimeError(
                "core_persistence_idempotency_conflict: billing candidate count differs."
            )
        billing_ids: list[int] = []
        candidate_id_to_billing_id: dict[str, int] = {}
        for index, (_, candidate) in enumerate(prepared["candidate_rows"]):
            billing_date = _required_text(
                candidate.get("billing_date") or candidate.get("transaction_date"),
                "candidate.billing_date",
            )
            quarter, financial_year, financial_month = _financial_period(billing_date)
            expected = {
                "AccountPayableReconRequestId": request_id,
                "BillingSystemId": int(candidate.get("billing_system_id") or 5),
                "InvoiceNumber": _required_text(
                    candidate.get("invoice_number")
                    or candidate.get("customer_invoice_number"),
                    "candidate.invoice_number",
                ),
                "CustomerName": _required_text(
                    candidate.get("customer_name") or candidate.get("customer_account"),
                    "candidate.customer_name",
                ),
                "AccountNumber": _required_text(
                    candidate.get("account_number") or candidate.get("customer_account"),
                    "candidate.account_number",
                ),
                "ServiceNumber": candidate.get("service_number")
                or candidate.get("service_id"),
                "ServiceDescription": candidate.get("service_description"),
                "BillingDate": billing_date,
                "FinancialQuarter": candidate.get("financial_quarter") or quarter,
                "FinancialYear": candidate.get("financial_year") or financial_year,
                "FinancialMonth": int(
                    candidate.get("financial_month") or financial_month
                ),
                "AmountExclGST": _required_text(
                    candidate.get("amount_excl_gst")
                    or candidate.get("customer_invoice_amount"),
                    "candidate.amount_excl_gst",
                ),
                "EmeraldCarrierDetail": candidate.get("emerald_carrier_detail"),
                "RecurringAmountExclGST": candidate.get(
                    "recurring_amount_excl_gst"
                ),
                "UsageAmountExclGST": candidate.get("usage_amount_excl_gst"),
                "ServiceSpecName": candidate.get("service_spec_name"),
                "ChargeType": candidate.get("charge_type"),
            }
            if billing_rows:
                actual = billing_rows[index]
                _assert_equal(actual, expected, identity=f"billing candidate {index}")
                billing_id = int(actual["Id"])
            else:
                billing_id = _insert_id(
                    cursor,
                    f"""
                    INSERT INTO [{SCHEMA}].[GenericNexonBilling]
                        (AccountPayableReconRequestId, BillingSystemId, InvoiceNumber,
                         CustomerName, AccountNumber, ServiceNumber, ServiceDescription,
                         BillingDate, FinancialQuarter, FinancialYear, FinancialMonth,
                         AmountExclGST, EmeraldCarrierDetail, RecurringAmountExclGST,
                         UsageAmountExclGST, ServiceSpecName, ChargeType)
                    OUTPUT INSERTED.Id
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    tuple(expected.values()),
                )
            billing_ids.append(billing_id)
            candidate_id = str(candidate.get("candidate_id") or "")
            if candidate_id:
                candidate_id_to_billing_id[candidate_id] = billing_id

        result_rows = _fetch_all(
            cursor,
            f"""
            SELECT *
            FROM [{SCHEMA}].[AccountPayableReconResult] WITH (UPDLOCK, HOLDLOCK)
            WHERE AccountPayableReconRequestId = ?
            ORDER BY Id
            """,
            (request_id,),
        )
        if result_rows and len(result_rows) != len(prepared["match_rows"]):
            raise RuntimeError(
                "core_persistence_idempotency_conflict: result count differs."
            )
        persisted_rows: list[dict[str, Any]] = []
        for index, match in enumerate(prepared["match_rows"]):
            line_id = _required_text(match.get("line_id"), "line_id")
            status = _required_text(match.get("ReconMatchStatus"), "ReconMatchStatus")
            if status not in STATUS_IDS:
                raise RuntimeError(
                    "core_persistence_invalid_input: "
                    f"unsupported ReconMatchStatus {status!r}."
                )
            candidate = match.get("candidate_snapshot", {})
            if not isinstance(candidate, dict):
                candidate = {}
            billing_id = candidate_id_to_billing_id.get(
                str(candidate.get("candidate_id") or "")
            )
            expected = {
                "AccountPayableReconRequestId": request_id,
                "GenericSupplierInvoiceLineItemId": line_item_ids[line_id],
                "GenericNexonBillingId": billing_id,
                "ReconMatchStatusId": STATUS_IDS[status],
                "serviceLogin": candidate.get("service_login"),
                "serviceLastInvoiceDate": candidate.get("service_last_invoice_date"),
                "serviceLastInvoiceNumber": candidate.get(
                    "service_last_invoice_number"
                ),
                "serviceLastInvoiceAmount": candidate.get(
                    "service_last_invoice_amount"
                ),
            }
            if result_rows:
                _assert_equal(result_rows[index], expected, identity=f"result {index}")
            else:
                _insert_id(
                    cursor,
                    f"""
                    INSERT INTO [{SCHEMA}].[AccountPayableReconResult]
                        (AccountPayableReconRequestId,
                         GenericSupplierInvoiceLineItemId, GenericNexonBillingId,
                         ReconMatchStatusId, serviceLogin, serviceLastInvoiceDate,
                         serviceLastInvoiceNumber, serviceLastInvoiceAmount)
                    OUTPUT INSERTED.Id
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    tuple(expected.values()),
                )
            output = dict(match)
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

    return (
        {"rows": persisted_rows},
        {
            "run_id": prepared["run_id"],
            "provider": prepared["provider"],
            "mode": "sqlserver",
            "transaction": "committed",
            "write_disposition": "inserted" if inserted else "validated_existing",
            "request_count": 1,
            "invoice_count": len(invoice_ids),
            "supplier_line_count": len(line_item_ids),
            "billing_candidate_count": len(billing_ids),
            "result_count": len(persisted_rows),
            "idempotency_scope": "OneDrivePath plus exact child payload validation",
        },
    )
