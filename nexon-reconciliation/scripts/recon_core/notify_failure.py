from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .common import DEFAULT_CONFIG_PATH, load_config, read_json, write_json


def _enabled(config: dict) -> bool:
    return (
        config.get("features", {}).get("failure_notifications_enabled") is True
        and config.get("failure_handling", {}).get("notify_operator") is True
    )


def _safe_payload(manifest: dict) -> dict:
    return {
        "status": manifest.get("status"),
        "provider": manifest.get("provider"),
        "stage": manifest.get("failed_stage"),
        "reason": manifest.get("failure_code"),
        "run_id": manifest.get("run_id"),
        "recorded_at": manifest.get("recorded_at"),
    }


def _notification_recipients() -> list[str]:
    return [
        item.strip()
        for item in os.environ.get("NEXON_RECON_FAILURE_NOTIFICATION_EMAIL_TO", "").split(",")
        if item.strip()
    ]


def _failure_subject(payload: dict) -> str:
    return f"Nexon reconciliation failure: {payload.get('provider')} {payload.get('stage')}"


def _failure_body(payload: dict, failure_manifest_path: Path) -> str:
    lines = [
        "Nexon reconciliation failure notification",
        "",
        f"Provider: {payload.get('provider')}",
        f"Stage: {payload.get('stage')}",
        f"Reason: {payload.get('reason')}",
        f"Run ID: {payload.get('run_id') or 'not available'}",
        f"Recorded at: {payload.get('recorded_at')}",
        "",
        f"Failure manifest: {failure_manifest_path}",
        "",
        "No files are attached. Review SharePoint run artifacts or the failure manifest path above.",
    ]
    return "\n".join(lines)


def _prepare_outlook_tool_manifest(manifest: dict, failure_manifest_path: Path) -> dict:
    recipients = _notification_recipients()
    if not recipients:
        raise RuntimeError("failure_notification_recipients_missing: NEXON_RECON_FAILURE_NOTIFICATION_EMAIL_TO is required.")
    payload = _safe_payload(manifest)
    return {
        "status": "ready_for_outlook_tool",
        "mode": "outlook",
        "content": "text_only",
        "delivery_tool": "native_outlook_send_email",
        "notification_sent": False,
        "notification_delivery_required": True,
        "attachments_allowed": False,
        "attachment_policy": "Do not attach files; include SharePoint artifact links or manifest paths in the email body.",
        "to": recipients,
        "subject": _failure_subject(payload),
        "body_text": _failure_body(payload, failure_manifest_path),
        "safe_payload": payload,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Send a configured reconciliation failure notification.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--failure-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    config = load_config(args.config)
    manifest = read_json(args.failure_manifest)
    if not _enabled(config):
        result = {"status": "disabled", "notification_sent": False}
    else:
        failure_handling = config.get("failure_handling", {})
        mode = (failure_handling.get("notification_mode") or "outlook").strip().lower()
        content = (failure_handling.get("notification_content") or "text_only").strip().lower()
        if mode != "outlook":
            raise RuntimeError(f"Unsupported failure notification mode: {mode}")
        if content != "text_only":
            raise RuntimeError(f"Unsupported failure notification content: {content}")
        result = _prepare_outlook_tool_manifest(manifest, args.failure_manifest)

    if args.output:
        write_json(args.output, result)
    else:
        print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
