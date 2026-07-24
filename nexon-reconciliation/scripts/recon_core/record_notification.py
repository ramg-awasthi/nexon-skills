from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

from .common import read_json, write_json


def main() -> int:
    parser = argparse.ArgumentParser(description="Record native Outlook notification delivery.")
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--status", choices=["sent", "failed"], required=True)
    parser.add_argument("--message-id")
    parser.add_argument("--recipient-count", type=int, required=True)
    args = parser.parse_args()

    if args.status == "sent" and not args.message_id:
        raise RuntimeError("notification_receipt_invalid: sent status requires message id.")
    audit_path = args.run_root / "manifest" / "audit_manifest.json"
    audit = read_json(audit_path)
    receipt = {
        "status": args.status,
        "message_id": args.message_id,
        "recipient_count": args.recipient_count,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "attachments": [],
    }
    write_json(args.run_root / "manifest" / "notification_receipt.json", receipt)
    audit["notification_receipt"] = receipt
    write_json(audit_path, audit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
