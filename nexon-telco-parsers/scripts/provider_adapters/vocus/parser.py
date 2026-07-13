from __future__ import annotations

from pathlib import Path

from provider_adapters.common import build_result, csv_rows, first_non_empty, make_line, period_from_date


def parse(source_files: list[Path], context: dict) -> dict:
    csv_files = [path for path in source_files if path.suffix.lower() == ".csv"]
    if not csv_files:
        raise ValueError("checkFail: Vocus parser expects CSV invoice input.")

    output = []
    line_index = 1
    for source_file in csv_files:
        rows = csv_rows(source_file)
        if not rows:
            continue
        account_number = first_non_empty(rows[0], "Account_ID")
        invoice_number = first_non_empty(rows[0], "Invoice_ID")
        invoice_date = first_non_empty(rows[0], "Invoice_Issue_Date")
        period_start, period_end = period_from_date(invoice_date)
        service_type_default = first_non_empty(rows[0], "Service_Type")
        for source_row, row in enumerate(rows, start=2):
            output.append(
                make_line(
                    context=context,
                    source_file=source_file,
                    source_row=source_row,
                    provider_account=first_non_empty(row, "Account_ID") or account_number,
                    service_id=first_non_empty(row, "Service_ID_Secondary", "Service_ID_Primary"),
                    invoice_number=first_non_empty(row, "Invoice_ID") or invoice_number,
                    invoice_date=first_non_empty(row, "Invoice_Issue_Date") or invoice_date,
                    billing_period_start=period_start,
                    billing_period_end=period_end,
                    amount=first_non_empty(row, "Charge_ex_Tax_Total_Amount"),
                    charge_type="Recurring",
                    detail_description=first_non_empty(row, "Charge_Description", "Service_Invoice_Description"),
                    service_type=first_non_empty(row, "Service_Type") or service_type_default,
                    currency=first_non_empty(row, "Charge_Currency") or "AUD",
                    line_index=line_index,
                    account_name=first_non_empty(row, "Account_Name"),
                    service_id_primary=first_non_empty(row, "Service_ID_Primary"),
                )
            )
            line_index += 1
    return build_result(output)
