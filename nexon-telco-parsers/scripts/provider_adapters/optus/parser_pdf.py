from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from provider_adapters.common import build_result, make_line


MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "sept": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}


def _read_pdf_lines(path: Path) -> list[str]:
    try:
        import pypdf  # type: ignore
    except ImportError as exc:
        raise ValueError("parser_unavailable: Optus PDF parser requires the pypdf package.") from exc
    reader = pypdf.PdfReader(str(path))
    lines: list[str] = []
    for page in reader.pages:
        lines.extend((page.extract_text() or "").splitlines())
    return [line.strip() for line in lines if line.strip()]


def _parse_short_date(text: str) -> str:
    match = re.search(r"(\d{1,2})\s+([A-Za-z]{3,4})\s+(\d{2,4})", text)
    if not match:
        return ""
    day = int(match.group(1))
    month = MONTHS[match.group(2).lower()]
    year = int(match.group(3))
    if year < 100:
        year += 2000
    return date(year, month, day).isoformat()


def _extract_invoice_context(lines: list[str]) -> tuple[str, str, str, str]:
    text = "\n".join(lines)
    account = ""
    invoice = ""
    period_start = ""
    period_end = ""

    account_match = re.search(r"(?:Customer account number|ACCOUNT NUMBER|Account No:|Account Number)\s*([0-9 ]{6,})", text, re.IGNORECASE)
    if account_match:
        account = re.sub(r"\D", "", account_match.group(1))
    migrated_match = re.search(r"Migrated Account\s*([0-9 ]{6,})", text, re.IGNORECASE)
    if migrated_match and not account:
        account = re.sub(r"\D", "", migrated_match.group(1))

    invoice_match = re.search(r"(?:Invoice number|Invoice No:)\s*([0-9]+)", text, re.IGNORECASE)
    if invoice_match:
        invoice = invoice_match.group(1).lstrip("0") or "0"

    period_match = re.search(
        r"(?:Account period|Invoice Period:?)\s*(\d{1,2}\s+[A-Za-z]{3,4}\s+\d{2,4})\s+to\s+(\d{1,2}\s+[A-Za-z]{3,4}\s+\d{2,4})",
        text,
        re.IGNORECASE,
    )
    if period_match:
        period_start = _parse_short_date(period_match.group(1))
        period_end = _parse_short_date(period_match.group(2))

    return account, invoice, period_start, period_end


def _summary_sections(lines: list[str]) -> list[tuple[str, str, str]]:
    in_summary = False
    active_type = ""
    rows: list[tuple[str, str, str]] = []
    service_re = re.compile(r"^(?P<service>.+?)\s+(?P<page>\d+)\s+(?P<amount>[0-9,]+\.\d{2})(?P<credit>\s+CR)?$")
    ignored = {
        "Service number Page ref Amount",
        "continued",
        "NEXON ASIA PACIFIC",
        "NEXON ASIA PACIFIC (continued)",
    }

    for raw_line in lines:
        line = raw_line.replace("\xa0", " ").strip()
        if line == "SERVICE SUMMARY":
            in_summary = True
            continue
        if in_summary and line == "SERVICE DETAILS":
            break
        if not in_summary or line in ignored or not line:
            continue
        if "Total cost" in line or line.startswith("$") or line.startswith("Issue Date") or line.startswith("Page "):
            continue
        match = service_re.match(line)
        if match:
            amount = match.group("amount").replace(",", "")
            if match.group("credit"):
                amount = f"-{amount}"
            rows.append((match.group("service").strip(), active_type, amount))
            continue
        if not re.search(r"\d", line) and "Optus" in line:
            active_type = line.replace("(continued)", "").strip()
    return rows


def _lines_from_pdf_text(*, lines: list[str], source_file: Path, context: dict, line_index_start: int) -> tuple[list[dict], int]:
    account, invoice, period_start, period_end = _extract_invoice_context(lines)
    if not account or not invoice:
        raise ValueError("fileFail: Optus PDF is missing account or invoice number.")

    summary_rows = _summary_sections(lines)
    if not summary_rows:
        raise ValueError("fileFail: Optus PDF has no extractable service line rows.")

    output: list[dict] = []
    line_index = line_index_start
    for source_row, (service_id, service_type, amount) in enumerate(summary_rows, start=1):
        output.append(
            make_line(
                context=context,
                source_file=source_file,
                source_row=source_row,
                source_page_or_sheet="SERVICE SUMMARY",
                provider_account=account,
                service_id=service_id,
                invoice_number=invoice,
                billing_period_start=period_start,
                billing_period_end=period_end,
                amount=amount,
                charge_type="Non-recurring" if str(amount).startswith("-") else "Recurring",
                detail_description=service_type or "Optus service",
                service_type=service_type or "Optus",
                line_index=line_index,
            )
        )
        line_index += 1
    return output, line_index


def parse(source_files: list[Path], context: dict) -> dict:
    pdf_files = [path for path in source_files if path.suffix.lower() == ".pdf"]
    if not pdf_files:
        raise ValueError("checkFail: Optus PDF parser expects PDF input.")

    output: list[dict] = []
    line_index = 1
    for source_file in pdf_files:
        lines, line_index = _lines_from_pdf_text(lines=_read_pdf_lines(source_file), source_file=source_file, context=context, line_index_start=line_index)
        output.extend(lines)
    return build_result(output)
