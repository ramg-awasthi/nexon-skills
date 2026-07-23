from __future__ import annotations

from pathlib import Path

from provider_adapters.common import build_result, csv_rows, first_non_empty, make_line


SERVICE_TYPES = {
    "6852570800": "Wsale Ethernet Business Access",
    "1436132800": "Wsale Ethernet Business Access",
    "2368757800": "Private Line",
    "329254800": "Telstra IP Solutions",
    "0329254800": "Telstra IP Solutions",
}


def _charge_type(raw: str, fallback: str = "") -> str:
    value = raw or fallback
    if value == "Services & equipment rental":
        return "Recurring"
    if value == "Telstra other charges and credits":
        return "Non-recurring"
    return value


def parse(source_files: list[Path], context: dict) -> dict:
    csv_files = [path for path in source_files if path.suffix.lower() == ".csv"]
    if not csv_files:
        raise ValueError("checkFail: Telstra parser expects CSV detail report input.")

    output = []
    line_index = 1
    for source_file in csv_files:
        rows = csv_rows(source_file)
        if not rows:
            continue
        account_number = first_non_empty(rows[0], "ACC_NUM", "Account Number", "Account")
        invoice_number = first_non_empty(rows[0], "BILL_NUM", "Bill Number", "Invoice Number")
        service_type = SERVICE_TYPES.get(account_number, "")
        for source_row, row in enumerate(rows, start=2):
            service_id = first_non_empty(row, "SERVICE_NUMBER", "Service Number", "Service number")
            detail = first_non_empty(
                row,
                "CALL_TYPE",
                "Number Description 2",
                "Service Description 2",
                "Number Description 1",
                "Service Description 1",
                "Service Descriptions",
            )
            charge_type = _charge_type(
                first_non_empty(row, "CHARGE_TYPE", "Charge Type"),
                first_non_empty(row, "Service Description 2"),
            )
            amount = first_non_empty(row, "EXCL_GST", "Amount", "Amount Ex GST")
            start = first_non_empty(row, "FROM_DATE", "From Date")
            end = first_non_empty(row, "TO_DATE", "To Date")
            output.append(
                make_line(
                    context=context,
                    source_file=source_file,
                    source_row=source_row,
                    provider_account=account_number,
                    service_id=service_id,
                    invoice_number=invoice_number,
                    amount=amount,
                    charge_type=charge_type,
                    detail_description=detail,
                    service_type=service_type,
                    billing_period_start=start,
                    billing_period_end=end,
                    currency=first_non_empty(row, "Currency") or "AUD",
                    line_index=line_index,
                )
            )
            line_index += 1
    return build_result(output)
