from __future__ import annotations

import calendar
import csv
from collections.abc import Iterable
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


BASE_HEADERS = [
    "line_id",
    "run_id",
    "provider",
    "source_file",
    "source_row",
    "source_page_or_sheet",
    "provider_account",
    "service_id_raw",
    "service_id_normalized",
    "invoice_number",
    "invoice_date",
    "billing_period_start",
    "billing_period_end",
    "currency",
    "invoice_total",
    "amount",
    "supplier_amount",
    "charge_type",
    "detail_description",
    "service_type",
]


def csv_rows(path_or_file: Any) -> list[dict[str, str]]:
    text_file = path_or_file
    close_after = False
    if isinstance(path_or_file, (str, Path)):
        text_file = open(path_or_file, "r", encoding="utf-8-sig", newline="")
        close_after = True
    try:
        reader = csv.DictReader(text_file)
        rows = []
        for row in reader:
            clean = {}
            for key, value in row.items():
                if key is None:
                    continue
                clean[key] = str(value or "").strip()
            rows.append(clean)
        return rows
    finally:
        if close_after:
            text_file.close()


def parse_money(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    original = text
    negative = "(" in text and ")" in text
    text = text.replace("$", "").replace(",", "").replace("(", "").replace(")", "").strip()
    if text.upper().endswith("CR"):
        negative = True
        text = text[:-2].strip()
    try:
        amount = Decimal(text)
    except InvalidOperation:
        raise ValueError(f"parser_failed: invalid money value {original!r}") from None
    if negative:
        amount = -amount
    return f"{amount.quantize(Decimal('0.01'))}"


def decimal_amount(value: Any) -> Decimal:
    parsed = parse_money(value)
    if not parsed:
        return Decimal("0.00")
    return Decimal(parsed)


def parse_date(value: Any) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value or "").strip()
    if not text:
        return ""
    for fmt in (
        "%d/%m/%Y",
        "%d/%m/%y",
        "%Y/%m/%d",
        "%Y-%m-%d",
        "%d %b %Y",
        "%d %B %Y",
        "%m/%d/%Y",
        "%m/%d/%y",
    ):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            pass
    return text


def period_from_date(value: Any, *, previous_month: bool = False) -> tuple[str, str]:
    parsed = parse_date(value)
    if not parsed:
        return "", ""
    try:
        parsed_date = date.fromisoformat(parsed)
    except ValueError:
        return "", ""
    year = parsed_date.year
    month = parsed_date.month
    if previous_month:
        month -= 1
        if month == 0:
            month = 12
            year -= 1
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, 1).isoformat(), date(year, month, last_day).isoformat()


def normalize_service_id(value: Any) -> str:
    text = str(value or "").strip().replace(" ", "")
    if not text:
        return ""
    if text.isdigit():
        return text.lstrip("0") or "0"
    return text


def first_non_empty(row: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = str(row.get(key, "") or "").strip()
        if value:
            return value
    return ""


def build_result(lines: Iterable[dict[str, Any]]) -> dict[str, Any]:
    line_list = list(lines)
    headers = list(BASE_HEADERS)
    for line in line_list:
        for key in line:
            if key not in headers:
                headers.append(key)
    return {"headers": headers, "lines": line_list}


def make_line(
    *,
    context: dict[str, Any],
    source_file: Path,
    source_row: int,
    provider_account: str,
    service_id: str,
    invoice_number: str,
    amount: Any,
    charge_type: str = "",
    detail_description: str = "",
    service_type: str = "",
    billing_period_start: Any = "",
    billing_period_end: Any = "",
    invoice_date: Any = "",
    currency: str = "AUD",
    source_page_or_sheet: str = "",
    line_index: int,
    **extra: Any,
) -> dict[str, Any]:
    parsed_amount = parse_money(amount)
    row = {
        "line_id": f"{context.get('provider', 'provider')}_{line_index:06d}",
        "run_id": context.get("run_id") or "",
        "provider": context.get("provider") or "",
        "source_file": source_file.name,
        "source_row": source_row,
        "source_page_or_sheet": source_page_or_sheet,
        "provider_account": str(provider_account or ""),
        "service_id_raw": str(service_id or ""),
        "service_id_normalized": normalize_service_id(service_id),
        "invoice_number": str(invoice_number or ""),
        "invoice_date": parse_date(invoice_date),
        "billing_period_start": parse_date(billing_period_start),
        "billing_period_end": parse_date(billing_period_end),
        "currency": currency or "AUD",
        "invoice_total": "",
        "amount": parsed_amount,
        "supplier_amount": parsed_amount,
        "charge_type": charge_type,
        "detail_description": detail_description,
        "service_type": service_type,
        "parser_source": "deterministic_provider_adapter",
    }
    row.update({key: value for key, value in extra.items() if value not in (None, "")})
    return row
