from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .common import DEFAULT_CONFIG_PATH, load_config, write_json


def main() -> int:
    parser = argparse.ArgumentParser(description="Record a controlled reconciliation failure and stop.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--stage", required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--run-root", type=Path)
    parser.add_argument("--source-file")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    config = load_config(args.config)
    notifications_enabled = config.get("features", {}).get("failure_notifications_enabled") is True
    notify_operator = config.get("failure_handling", {}).get("notify_operator") is True
    now = datetime.now(ZoneInfo(config.get("timezone", "Australia/Sydney"))).isoformat()
    payload = {
        "status": "failed",
        "run_id": args.run_root.name if args.run_root else None,
        "correlation_id": args.run_root.name if args.run_root else f"{args.provider}:{now}",
        "provider": args.provider,
        "failed_stage": args.stage,
        "failure_code": args.reason,
        "sanitized_detail": args.reason,
        "retryable": False,
        "recorded_at": now,
        "source_file": args.source_file,
        "accepted_resolution_update_attempted": False,
        "notification_required": notifications_enabled and notify_operator,
        "notification_sent": False,
    }

    output = args.output
    if output is None:
        if args.run_root:
            output = args.run_root / "manifest" / "failure_manifest.json"
        else:
            output = Path("failure_manifest.json")
    write_json(output, payload)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
