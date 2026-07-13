from __future__ import annotations

import io
import zipfile
from datetime import datetime
from pathlib import Path

from provider_adapters.common import build_result, make_line


def _pipe_rows_from_dat(path: Path) -> list[list[str]]:
    return [line.strip("\n").split("|") for line in path.read_text(encoding="utf-8", errors="replace").splitlines()]


def _pipe_rows_from_zip(path: Path) -> list[list[str]]:
    rows: list[list[str]] = []
    with zipfile.ZipFile(path) as zfile:
        for member in zfile.infolist():
            if member.is_dir():
                continue
            with zfile.open(member, "r") as handle:
                text = io.TextIOWrapper(handle, encoding="utf-8", errors="replace")
                rows.extend(line.strip("\n").split("|") for line in text)
    return rows


def _period_date(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return datetime.strptime(text, "%Y%m%d").date().isoformat()


def _parse_pipe_rows(rows: list[list[str]], *, source_file: Path, context: dict, line_index_start: int) -> tuple[list[dict], int]:
    if len(rows) < 2:
        raise ValueError("fileFail: Optus voice package has no account summary row.")

    summary = rows[1]
    service_rows = [row for row in rows if "SRVS" in row]
    withdrawn_rows = [row for row in rows if "WUSG" in row]
    if not service_rows and not withdrawn_rows:
        raise ValueError("fileFail: Optus voice package has no SRVS/WUSG rows.")

    provider_account = str(int(summary[2])) if len(summary) > 2 and str(summary[2]).strip().isdigit() else str(summary[2] if len(summary) > 2 else "")
    period_start = _period_date(summary[18]) if len(summary) > 18 else ""
    period_end = _period_date(summary[19]) if len(summary) > 19 else ""
    invoice_number = str(int(service_rows[0][3])) if len(service_rows[0]) > 3 and str(service_rows[0][3]).strip().isdigit() else (service_rows[0][3] if len(service_rows[0]) > 3 else "")

    output: list[dict] = []
    line_index = line_index_start
    for source_row, row in enumerate(service_rows, start=1):
        charge_amount = float(row[6] or 0)
        if round(charge_amount, 2) == 0:
            continue
        recurring_charge = round(float(row[12] or 0), 2)
        usage_charge = round(float(row[14] or 0), 2)
        service_number = str(int(row[8])) if len(row) > 8 and str(row[8]).strip().isdigit() else str(row[8] if len(row) > 8 else "")
        description = row[11] if len(row) > 11 and row[11] else "Optus Evolve Voice"

        amounts: list[tuple[str, float]] = []
        if recurring_charge != 0 and usage_charge != 0:
            amounts = [("Recurring", recurring_charge), ("Usage", usage_charge)]
        elif recurring_charge != 0:
            amounts = [("Recurring", recurring_charge)]
        else:
            amounts = [("Usage", usage_charge)]

        for charge_type, amount in amounts:
            output.append(
                make_line(
                    context=context,
                    source_file=source_file,
                    source_row=source_row,
                    source_page_or_sheet="SRVS",
                    provider_account=provider_account,
                    service_id=service_number,
                    invoice_number=invoice_number,
                    billing_period_start=period_start,
                    billing_period_end=period_end,
                    amount=amount,
                    charge_type=charge_type,
                    detail_description=description,
                    service_type=description,
                    line_index=line_index,
                )
            )
            line_index += 1

    for source_row, row in enumerate(withdrawn_rows, start=1):
        amount = row[6] if len(row) > 6 else ""
        output.append(
            make_line(
                context=context,
                source_file=source_file,
                source_row=source_row,
                source_page_or_sheet="WUSG",
                provider_account=provider_account,
                service_id="10000",
                invoice_number=invoice_number,
                billing_period_start=period_start,
                billing_period_end=period_end,
                amount=amount,
                charge_type="Non-recurring",
                detail_description="Withdrawn Usage",
                service_type="Optus Evolve Voice",
                line_index=line_index,
            )
        )
        line_index += 1

    return output, line_index


def parse(source_files: list[Path], context: dict) -> dict:
    invoice_files = [path for path in source_files if path.suffix.lower() in {".zip", ".dat"}]
    if not invoice_files:
        raise ValueError("checkFail: Optus voice parser expects ZIP or extracted DAT input.")

    output: list[dict] = []
    line_index = 1
    for source_file in invoice_files:
        rows = _pipe_rows_from_zip(source_file) if source_file.suffix.lower() == ".zip" else _pipe_rows_from_dat(source_file)
        lines, line_index = _parse_pipe_rows(rows, source_file=source_file, context=context, line_index_start=line_index)
        output.extend(lines)
    if not output:
        raise ValueError("fileFail: Optus voice package produced no billable rows.")
    return build_result(output)
