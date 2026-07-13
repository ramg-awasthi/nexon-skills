from __future__ import annotations

from pathlib import Path

from provider_adapters.common import build_result, csv_rows, first_non_empty, make_line, period_from_date


def parse(source_files: list[Path], context: dict) -> dict:
    csv_files = [path for path in source_files if path.suffix.lower() == ".csv"]
    if not csv_files:
        raise ValueError("checkFail: Megaport parser expects CSV invoice input.")

    output = []
    line_index = 1
    for source_file in csv_files:
        rows = csv_rows(source_file)
        if not rows:
            continue
        account_number = first_non_empty(rows[0], "Customer number")
        invoice_number = first_non_empty(rows[0], "Invoice number")
        invoice_date = first_non_empty(rows[0], "Invoice Date")
        period_start, period_end = period_from_date(invoice_date, previous_month=True)
        for source_row, row in enumerate(rows, start=2):
            description = first_non_empty(row, "Description", "Product")
            output.append(
                make_line(
                    context=context,
                    source_file=source_file,
                    source_row=source_row,
                    provider_account=account_number,
                    service_id=first_non_empty(row, "Service id"),
                    invoice_number=invoice_number,
                    invoice_date=first_non_empty(row, "Invoice Date"),
                    billing_period_start=period_start,
                    billing_period_end=period_end,
                    amount=first_non_empty(row, "Amount"),
                    charge_type="Recurring",
                    detail_description=description,
                    service_type=f"Megaport-{description}" if description else "Megaport",
                    currency=first_non_empty(row, "Currency") or "AUD",
                    line_index=line_index,
                    product=first_non_empty(row, "Product"),
                    purchase_order=first_non_empty(row, "Purchase Order"),
                )
            )
            line_index += 1
    return build_result(output)
