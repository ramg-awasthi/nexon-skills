from __future__ import annotations

import argparse
import csv
from pathlib import Path

from .common import DEFAULT_CONFIG_PATH, HUMAN_VERIFIED_STATUS_VALUES, load_config, write_json

UPDATE_STATUS_VALUES = {"verified", "deferred", "rejected"}
REQUIRE_INVOICE_NUMBER_FOR_STATUSES = {"verified"}
PARTIAL_DEFERRED_UPDATE_ACTION = "write_partial_deferred_update"
HUMAN_VERIFICATION_UPDATE_ACTION = "write_human_verification_update"


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _build_update_plan(rows: list[dict[str, str]], config: dict) -> dict:
    policy = config.get("db_update_policy", {})
    allow_deferred_without_invoice = policy.get("allow_deferred_without_invoice_number") is True

    planned_updates: list[dict] = []
    skipped_rows: list[dict] = []
    for index, row in enumerate(rows, start=1):
        status = row.get("human_verified_status", "")
        invoice_number = row.get("human_verified_invoice_number", "").strip()
        if status not in HUMAN_VERIFIED_STATUS_VALUES:
            raise RuntimeError(f"Invalid human_verified_status in refined report row {index}: {status}")
        if status not in UPDATE_STATUS_VALUES:
            skipped_rows.append({"row_number": index, "human_verified_status": status, "reason": "status_not_updateable"})
            continue
        if status in REQUIRE_INVOICE_NUMBER_FOR_STATUSES and not invoice_number:
            raise RuntimeError(f"Row {index} requires human_verified_invoice_number for status={status}.")

        partial_update = status == "deferred" and not invoice_number
        if partial_update and not allow_deferred_without_invoice:
            raise RuntimeError("Deferred rows without invoice number are not allowed by db_update_policy.")

        planned_updates.append(
            {
                "row_number": index,
                "line_id": row.get("line_id"),
                "provider": row.get("provider"),
                "service_id": row.get("agent_suggested_service_id") or row.get("service_id_normalized") or row.get("service_id_raw"),
                "subscription_id": row.get("agent_suggested_subscription_id"),
                "customer_account": row.get("agent_suggested_customer_account"),
                "invoice_number": invoice_number,
                "human_verified_status": status,
                "partial_update": partial_update,
                "update_action": PARTIAL_DEFERRED_UPDATE_ACTION if partial_update else HUMAN_VERIFICATION_UPDATE_ACTION,
            }
        )

    return {
        "mode": "dry_run",
        "db_update_enabled": True,
        "live_write_performed": False,
        "planned_update_count": len(planned_updates),
        "skipped_row_count": len(skipped_rows),
        "planned_updates": planned_updates,
        "skipped_rows": skipped_rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Disabled/config-gated DB update adapter.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--refined-report", type=Path, required=True)
    parser.add_argument("--audit-output", type=Path, required=True)
    parser.add_argument("--approved-change-ticket")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    config = load_config(args.config)
    enabled = config.get("features", {}).get("db_update_enabled") is True
    if not enabled:
        raise RuntimeError("DB update is disabled. Default Phase 1 mode is report-only.")
    if not args.approved_change_ticket:
        raise RuntimeError("DB update requires an approved change ticket.")
    if not args.dry_run:
        raise RuntimeError("DB update implementation must support and pass dry-run before live writes.")

    plan = _build_update_plan(_read_rows(args.refined_report), config)
    plan["approved_change_ticket"] = args.approved_change_ticket
    write_json(args.audit_output, plan)
    print(args.audit_output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
