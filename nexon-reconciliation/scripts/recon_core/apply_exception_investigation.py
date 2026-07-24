from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from .common import APPROVED_REFINED_COLUMNS, INVESTIGATOR_MATCH_STATUS_VALUES, read_json, write_json


AGENT_OUTPUT_COLUMNS = {
    column
    for column in APPROVED_REFINED_COLUMNS
    if column.startswith("agent_")
}


def _rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("rows", "investigations", "updates"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
    raise RuntimeError("Exception investigation payload must contain a row/update list.")


def _line_key(row: dict[str, Any]) -> str:
    line_id = row.get("line_id")
    if line_id:
        return str(line_id)
    raise RuntimeError("Investigation row is missing required line_id.")


def _validated_update(row: dict[str, Any]) -> dict[str, Any]:
    disallowed = sorted((set(row) - AGENT_OUTPUT_COLUMNS) - {"line_id", "run_id"})
    if disallowed:
        raise RuntimeError(f"Exception investigation included disallowed fields: {disallowed}")

    update = {column: row[column] for column in AGENT_OUTPUT_COLUMNS if column in row}
    status = update.get("agent_match_status")
    if status and status not in INVESTIGATOR_MATCH_STATUS_VALUES:
        raise RuntimeError(f"Invalid agent_match_status from exception investigation: {status}")
    if update and not str(update.get("agent_evidence_summary", "")).strip():
        raise RuntimeError("Exception investigation update requires agent_evidence_summary.")
    if update:
        update["agent_review_required"] = "true"
    return update


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply exception-investigator suggestions to match results.")
    parser.add_argument("--matches", type=Path, required=True)
    parser.add_argument("--investigation", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args()

    match_payload = read_json(args.matches)
    match_rows = match_payload.get("rows", [])
    if not isinstance(match_rows, list):
        raise RuntimeError("Match results must contain a rows list.")

    updates_by_key: dict[str, dict[str, Any]] = {}
    for row in _rows(read_json(args.investigation)):
        if not isinstance(row, dict):
            raise RuntimeError("Exception investigation updates must be objects.")
        key = _line_key(row)
        if key in updates_by_key:
            raise RuntimeError(f"Duplicate exception investigation update for row: {key}")
        updates_by_key[key] = _validated_update(row)

    applied = 0
    missing_keys = set(updates_by_key)
    merged_rows: list[dict[str, Any]] = []
    for row in match_rows:
        output = dict(row)
        key = _line_key(output)
        update = updates_by_key.get(key)
        if update:
            output.update(update)
            applied += 1
            missing_keys.discard(key)
        merged_rows.append(output)

    if missing_keys:
        raise RuntimeError(f"Exception investigation referenced unknown rows: {sorted(missing_keys)}")

    output_payload = dict(match_payload)
    output_payload["rows"] = merged_rows
    write_json(args.output, output_payload)
    if args.manifest:
        write_json(
            args.manifest,
            {
                "input_match_rows": len(match_rows),
                "investigation_updates": len(updates_by_key),
                "applied_updates": applied,
                "output": str(args.output),
            },
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
