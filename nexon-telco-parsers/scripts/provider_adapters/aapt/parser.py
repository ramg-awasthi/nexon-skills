from __future__ import annotations

import io
import zipfile
from decimal import Decimal
from pathlib import Path

from provider_adapters.common import (
    build_result,
    csv_rows,
    decimal_amount,
    first_non_empty,
    make_line,
    parse_date,
)


def _zip_csv_rows(zfile: zipfile.ZipFile, member: str) -> list[dict[str, str]]:
    with zfile.open(member) as handle:
        wrapper = io.TextIOWrapper(handle, encoding="utf-8-sig", newline="")
        return csv_rows(wrapper)


def _find_member(zfile: zipfile.ZipFile, fragment: str) -> str | None:
    for member in zfile.namelist():
        if fragment in member.lower() and member.lower().endswith(".csv"):
            return member
    return None


def _append_usage_groups(
    *,
    rows: list[dict[str, str]],
    group_key: str,
    description: str,
    amount_key: str,
    service_type_value: str | None,
    context: dict,
    source_file: Path,
    provider_account: str,
    invoice_number: str,
    lines: list[dict],
    line_index_start: int,
    rollup_suffix_chars: int = 0,
) -> int:
    grouped: dict[str, dict[str, object]] = {}
    for row in rows:
        service_id = first_non_empty(row, group_key)
        if not service_id:
            continue
        grouping_key = service_id[:-rollup_suffix_chars] if rollup_suffix_chars else service_id
        current = grouped.setdefault(
            grouping_key,
            {
                "amount": Decimal("0.00"),
                "start": first_non_empty(row, "Date"),
                "end": first_non_empty(row, "Date"),
                "service_type": service_type_value or first_non_empty(row, "Service Type"),
                "service_id": service_id,
            },
        )
        current["amount"] = current["amount"] + decimal_amount(first_non_empty(row, amount_key))
    line_index = line_index_start
    for service_id, data in grouped.items():
        amount = data["amount"]
        if amount == 0 and description == "Internet Usage":
            continue
        lines.append(
            make_line(
                context=context,
                source_file=source_file,
                source_row=0,
                source_page_or_sheet=description,
                provider_account=provider_account,
                service_id=str(data["service_id"]),
                invoice_number=invoice_number,
                billing_period_start=data["start"],
                billing_period_end=data["end"],
                amount=str(amount),
                charge_type="Usage",
                detail_description=description,
                service_type=str(data["service_type"] or ""),
                line_index=line_index,
            )
        )
        line_index += 1
    return line_index


def parse(source_files: list[Path], context: dict) -> dict:
    zip_files = [path for path in source_files if path.suffix.lower() == ".zip"]
    if not zip_files:
        raise ValueError("checkFail: AAPT parser expects supplier ZIP input.")

    lines: list[dict] = []
    line_index = 1
    for source_file in zip_files:
        with zipfile.ZipFile(source_file) as zfile:
            rec001 = _find_member(zfile, "rec001")
            if not rec001:
                raise ValueError("fileFail: AAPT ZIP is missing rec001 account header.")
            account_rows = _zip_csv_rows(zfile, rec001)
            if not account_rows:
                raise ValueError("fileFail: AAPT rec001 has no account header row.")
            account_row = account_rows[0]
            provider_account = f"{first_non_empty(account_row, 'Account Number')}#1"
            account_number = provider_account.removesuffix("#1")
            invoice_number = first_non_empty(account_row, "Bill Number")
            invoice_date = first_non_empty(account_row, "Statement Date")
            default_start = first_non_empty(account_row, "Bill Period From Date")
            default_end = first_non_empty(account_row, "Bill Period To Date")

            rec005 = _find_member(zfile, "rec005")
            if rec005:
                last_start = default_start
                last_end = default_end
                for source_row, row in enumerate(_zip_csv_rows(zfile, rec005), start=2):
                    start = first_non_empty(row, "Date From") or last_start
                    end = first_non_empty(row, "Date To") or last_end or start
                    last_start = start
                    last_end = end
                    raw_charge = first_non_empty(row, "Charge Type")
                    if raw_charge == "Recurring Charge":
                        charge_type = "Recurring"
                    elif raw_charge in {"Adjustment", "Discount"}:
                        charge_type = raw_charge
                    else:
                        charge_type = "Non-recurring"
                    lines.append(
                        make_line(
                            context=context,
                            source_file=source_file,
                            source_row=source_row,
                            source_page_or_sheet=rec005,
                            provider_account=provider_account,
                            service_id=first_non_empty(row, "Service Number").split("-")[0],
                            invoice_number=invoice_number,
                            invoice_date=invoice_date,
                            billing_period_start=start,
                            billing_period_end=end,
                            amount=first_non_empty(row, "Charge(ex GST)"),
                            charge_type=charge_type,
                            detail_description=first_non_empty(row, "Details"),
                            service_type=first_non_empty(row, "Service Type"),
                            line_index=line_index,
                        )
                    )
                    line_index += 1

            rec002 = _find_member(zfile, "rec002")
            if rec002:
                line_index = _append_usage_groups(
                    rows=_zip_csv_rows(zfile, rec002),
                    group_key="Origin",
                    description="Call Usage",
                    amount_key="Charge(ex GST)",
                    service_type_value=None,
                    context=context,
                    source_file=source_file,
                    provider_account=provider_account,
                    invoice_number=invoice_number,
                    lines=lines,
                    line_index_start=line_index,
                    rollup_suffix_chars=2 if account_number == "2000060308" else 0,
                )

            rec006 = _find_member(zfile, "rec006")
            if rec006:
                line_index = _append_usage_groups(
                    rows=_zip_csv_rows(zfile, rec006),
                    group_key="Service Number",
                    description="1800/1300/13 Usage",
                    amount_key="Charge(ex GST)",
                    service_type_value=None,
                    context=context,
                    source_file=source_file,
                    provider_account=provider_account,
                    invoice_number=invoice_number,
                    lines=lines,
                    line_index_start=line_index,
                )

            rec010 = _find_member(zfile, "rec010")
            if rec010:
                line_index = _append_usage_groups(
                    rows=_zip_csv_rows(zfile, rec010),
                    group_key="Service Number",
                    description="Internet Usage",
                    amount_key="Charge(ex GST)",
                    service_type_value="IP-Line Usage",
                    context=context,
                    source_file=source_file,
                    provider_account=provider_account,
                    invoice_number=invoice_number,
                    lines=lines,
                    line_index_start=line_index,
                )

            rec004 = _find_member(zfile, "rec004")
            if rec004:
                for source_row, row in enumerate(_zip_csv_rows(zfile, rec004), start=2):
                    description = first_non_empty(row, "Description")
                    amount = decimal_amount(first_non_empty(row, "Charge(ex GST)"))
                    if description == "Adjustment" and amount < 0:
                        charge_type = "Adjustment"
                    elif description == "Discount":
                        charge_type = "Discount"
                    else:
                        charge_type = "Non-recurring"
                    start = first_non_empty(row, "Date From") or default_start
                    end = first_non_empty(row, "Date To") or start or default_end
                    lines.append(
                        make_line(
                            context=context,
                            source_file=source_file,
                            source_row=source_row,
                            source_page_or_sheet=rec004,
                            provider_account=provider_account,
                            # Legacy Function readAAPTInvoice assigns rec004 account-level charges to service id 10000.
                            service_id="10000",
                            invoice_number=invoice_number,
                            invoice_date=invoice_date,
                            billing_period_start=parse_date(start),
                            billing_period_end=parse_date(end),
                            amount=str(amount),
                            charge_type=charge_type,
                            detail_description=first_non_empty(row, "Details"),
                            service_type="Account Level Charges/Adj/Disc",
                            line_index=line_index,
                        )
                    )
                    line_index += 1
    return build_result(lines)
