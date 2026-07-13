from __future__ import annotations

import argparse
from pathlib import Path

from .common import APPROVED_REFINED_COLUMNS, EXCLUDED_PHASE1_COLUMNS, read_json, write_csv, write_json


BASE_COLUMNS = [
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
]


def ordered_columns(rows: list[dict], preferred: list[str]) -> list[str]:
    columns: list[str] = []
    for column in preferred:
        if column not in columns:
            columns.append(column)
    for row in rows:
        for column in row:
            if column not in columns:
                columns.append(column)
    return [column for column in columns if column not in EXCLUDED_PHASE1_COLUMNS]


def with_refined_defaults(rows: list[dict]) -> list[dict]:
    refined_rows: list[dict] = []
    for row in rows:
        output = dict(row)
        for column in APPROVED_REFINED_COLUMNS:
            if column == "agent_review_required":
                output.setdefault(column, str(output.get("agent_match_status") != "auto_matched").lower())
            elif column == "human_verified_status":
                output.setdefault(column, "not_reviewed")
            else:
                output.setdefault(column, "")
        refined_rows.append(output)
    return refined_rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Write raw and refined reconciliation reports.")
    parser.add_argument("--matches", type=Path, required=True)
    parser.add_argument(
        "--raw-matches",
        type=Path,
        help="Optional pre-investigation match rows for the raw report. Refined report still uses --matches.",
    )
    parser.add_argument("--raw-output", type=Path, required=True)
    parser.add_argument("--refined-output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()

    refined_input_rows = read_json(args.matches).get("rows", [])
    raw_input_rows = read_json(args.raw_matches).get("rows", []) if args.raw_matches else refined_input_rows
    if len(raw_input_rows) != len(refined_input_rows):
        raise RuntimeError("Raw and refined report inputs must have the same row count.")
    if any(EXCLUDED_PHASE1_COLUMNS.intersection(row.keys()) for row in refined_input_rows + raw_input_rows):
        raise RuntimeError("Excluded runtime columns leaked into refined report schema.")
    raw_columns = ordered_columns(raw_input_rows, BASE_COLUMNS)
    refined_rows = with_refined_defaults(refined_input_rows)
    refined_columns = ordered_columns(refined_rows, raw_columns + APPROVED_REFINED_COLUMNS)

    write_csv(args.raw_output, raw_input_rows, raw_columns)
    write_csv(args.refined_output, refined_rows, refined_columns)
    write_json(
        args.manifest,
        {
            "raw_output": str(args.raw_output),
            "refined_output": str(args.refined_output),
            "row_count": len(raw_input_rows),
            "raw_source": str(args.raw_matches or args.matches),
            "refined_source": str(args.matches),
            "raw_columns": raw_columns,
            "refined_added_columns": APPROVED_REFINED_COLUMNS,
            "refined_preserves_all_raw_fields": True,
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
