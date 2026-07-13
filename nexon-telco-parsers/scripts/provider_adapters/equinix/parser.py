from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from provider_adapters.common import build_result, decimal_amount, make_line


def _load_xlsx_rows(path: Path) -> tuple[list[str], list[dict[str, object]]]:
    try:
        import openpyxl  # type: ignore
    except ImportError as exc:
        raise ValueError("parser_unavailable: Equinix parser requires the openpyxl package.") from exc
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    sheet = workbook.worksheets[0]
    values = list(sheet.iter_rows(values_only=True))
    if not values:
        raise ValueError("fileFail: Equinix workbook is empty.")
    headers = [str(value or "").strip() for value in values[0]]
    rows: list[dict[str, object]] = []
    for row_values in values[1:]:
        rows.append({headers[index]: value for index, value in enumerate(row_values) if index < len(headers)})
    return headers, rows


def _value(row: dict[str, object], *keys: str) -> object:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return ""


def _amount(value: object) -> Decimal:
    if value in (None, ""):
        return Decimal("0.00")
    return decimal_amount(value)


def _number_text(value: object) -> str:
    text = str(value or "").strip()
    if text.endswith(".0"):
        text = text[:-2]
    return str(int(value)) if isinstance(value, float) else text


def _service_number(value: object) -> str:
    text = str(value or "").replace('"', "").strip()
    if text.endswith(".0"):
        text = text[:-2]
    return text.split(".")[0]


def _explode_service_number(value: str) -> list[str]:
    import re

    parts = [part for part in re.split(r"[-._]", value) if part]
    filtered = [part for part in parts if not part.startswith(("0", "10")) and len(part) >= 5]
    return filtered or ([value] if value else ["11111"])


def _line(
    *,
    context: dict,
    source_file: Path,
    source_row: int,
    provider_account: str,
    service_number: str,
    invoice_number: str,
    period_start: object,
    period_end: object,
    amount: Decimal,
    charge_type: str,
    description: str,
    service_type: str,
    service_location: str,
    line_index: int,
    infrastructure_cost: Decimal | None = None,
) -> dict:
    extra = {"service_location": service_location}
    if infrastructure_cost is not None:
        extra["infrastructure_cost"] = f"{infrastructure_cost.quantize(Decimal('0.01'))}"
    return make_line(
        context=context,
        source_file=source_file,
        source_row=source_row,
        source_page_or_sheet="Detail",
        provider_account=provider_account,
        service_id=service_number,
        invoice_number=invoice_number,
        invoice_date=period_start,
        billing_period_start=period_start,
        billing_period_end=period_end,
        amount=f"{amount.quantize(Decimal('0.01'))}",
        charge_type=charge_type,
        detail_description=description,
        service_type=service_type,
        line_index=line_index,
        **extra,
    )


def _rows_to_lines(*, rows: list[dict[str, object]], source_file: Path, context: dict, line_index_start: int) -> tuple[list[dict], int]:
    if not rows:
        raise ValueError("fileFail: Equinix workbook has no detail rows.")

    first = rows[0]
    provider_account = _number_text(_value(first, "Customer Account #"))
    period_start = _value(first, "Recurring From Date")
    period_end = _value(first, "Recurring To Date")
    invoice_number = _number_text(_value(first, "Transaction #"))

    output: list[dict] = []
    line_index = line_index_start
    for source_row, row in enumerate(rows, start=2):
        raw_service = str(_value(row, "Serial #")).replace('"', "").strip()
        service_number = _service_number(raw_service)
        description = str(_value(row, "Product Description"))
        service_location = str(_value(row, "Ibx Center"))
        service_type = str(_value(row, "Product Category"))
        activity_type = str(_value(row, "Activity Type"))
        adjustments = _amount(_value(row, "Adjustments"))
        recurring_amount = _amount(_value(row, "Recurring Amount"))
        nonrecurring_amount = _amount(_value(row, "Non Recurring Amount"))

        def append_customer(amount: Decimal, charge_type: str, service_type_value: str, service_id: str = "") -> None:
            nonlocal line_index
            for exploded_service in _explode_service_number(service_id or service_number or "11111"):
                output.append(
                    _line(
                        context=context,
                        source_file=source_file,
                        source_row=source_row,
                        provider_account=provider_account,
                        service_number=exploded_service,
                        invoice_number=invoice_number,
                        period_start=period_start,
                        period_end=period_end,
                        amount=amount,
                        charge_type=charge_type,
                        description=description,
                        service_type=service_type_value,
                        service_location=service_location,
                        line_index=line_index,
                    )
                )
                line_index += 1

        def append_infra(amount: Decimal, charge_type: str, service_type_value: str, service_id: str = "") -> None:
            nonlocal line_index
            output.append(
                _line(
                    context=context,
                    source_file=source_file,
                    source_row=source_row,
                    provider_account=provider_account,
                    service_number=service_id or service_number or "11111",
                    invoice_number=invoice_number,
                    period_start=period_start,
                    period_end=period_end,
                    amount=amount,
                    infrastructure_cost=amount,
                    charge_type=charge_type,
                    description=description,
                    service_type=service_type_value,
                    service_location=service_location,
                    line_index=line_index,
                )
            )
            line_index += 1

        if raw_service == "00110-13929691.1":
            amount = recurring_amount + adjustments
            cage = amount / Decimal("40")
            append_customer(Decimal("19.5") * cage, "Recurring", "Equinix Space - Customer Cost", "13929691")
            append_infra(Decimal("12") * cage, "Recurring", "Equinix Space - Cloud Infrastructure Cost", "13929691")
            append_infra(Decimal("6.5") * cage, "Recurring", "Equinix Space - Network Infrastructure Cost", "13929691")
            append_infra(Decimal("2") * cage, "Recurring", "Equinix Space - UC Infrastructure Cost", "13929691")
        elif raw_service == "4-24685509452":
            amount = recurring_amount + adjustments
            append_customer(amount * Decimal("0.4"), "Recurring", "Equinix Power - Customer Cost", "24685509452")
            append_infra(amount * Decimal("0.5"), "Recurring", "Equinix Power - Cloud Infrastructure Cost", "24685509452")
            append_infra(amount * Decimal("0.05"), "Recurring", "Equinix Power - Network Infrastructure Cost", "24685509452")
            append_infra(amount * Decimal("0.05"), "Recurring", "Equinix Power - UC Infrastructure Cost", "24685509452")
        elif raw_service == "17_13103686568":
            amount = nonrecurring_amount + adjustments
            append_customer(amount * Decimal("0.4"), "Non-recurring", "Equinix Power - Customer Cost", "13103686568")
            append_infra(amount * Decimal("0.5"), "Non-recurring", "Equinix Power - Cloud Infrastructure Cost", "13103686568")
            append_infra(amount * Decimal("0.05"), "Non-recurring", "Equinix Power - Network Infrastructure Cost", "13103686568")
            append_infra(amount * Decimal("0.05"), "Non-recurring", "Equinix Power - UC Infrastructure Cost", "13103686568")
        elif service_location == "ME2":
            charge_type = "Recurring" if "Recurring" in activity_type else "Non-recurring"
            amount = (recurring_amount if charge_type == "Recurring" else nonrecurring_amount) + adjustments
            append_infra(amount, charge_type, service_type, service_number or "11111")
        else:
            charge_type = "Recurring" if "Recurring" in activity_type else "Non-recurring"
            amount = (recurring_amount if charge_type == "Recurring" else nonrecurring_amount) + adjustments
            append_customer(amount, charge_type, service_type, service_number or "11111")

    return output, line_index


def parse(source_files: list[Path], context: dict) -> dict:
    xlsx_files = [path for path in source_files if path.suffix.lower() == ".xlsx"]
    if not xlsx_files:
        raise ValueError("checkFail: Equinix parser expects XLSX detail invoice input.")

    output: list[dict] = []
    line_index = 1
    for source_file in xlsx_files:
        _headers, rows = _load_xlsx_rows(source_file)
        lines, line_index = _rows_to_lines(rows=rows, source_file=source_file, context=context, line_index_start=line_index)
        output.extend(lines)
    return build_result(output)
