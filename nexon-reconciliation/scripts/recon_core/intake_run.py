from __future__ import annotations

import argparse
import shutil
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .common import (
    DEFAULT_CONFIG_PATH,
    create_run_layout,
    ensure_db_update_disabled,
    ensure_provider,
    load_config,
    resolve_run_id_collision,
    sha256_file,
    sharepoint_roots,
    write_json,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a Nexon recon run package from one source file.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--source-file", type=Path, required=True)
    parser.add_argument("--result-root", type=Path, help="Override result root for local/testing runs.")
    parser.add_argument("--intake-mode", choices=["manual_upload", "provider_api"], default="manual_upload")
    parser.add_argument(
        "--source-identity",
        help="Stable source identity for run-id hashing, such as a provider API invoice id. Defaults to source checksum.",
    )
    parser.add_argument("--copy", action="store_true", help="Copy instead of move. Testing only.")
    parser.add_argument("--move", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()

    config = load_config(args.config)
    ensure_db_update_disabled(config)
    provider_config = ensure_provider(config, args.provider)

    if not args.source_file.is_file():
        raise FileNotFoundError(args.source_file)
    if args.intake_mode == "provider_api" and (
        config.get("features", {}).get("provider_api_enabled") is not True
        or provider_config["provider_api_adapter_enabled"] is not True
    ):
        raise RuntimeError("provider_api_not_available: Provider API intake is not enabled in feature flags.")

    checksum = sha256_file(args.source_file)
    source_identity = args.source_identity or checksum
    created_at = datetime.now(ZoneInfo("Australia/Sydney"))
    _upload_root, default_result_root = sharepoint_roots(config)
    result_root = args.result_root or default_result_root
    year = created_at.strftime("%Y")
    month = created_at.strftime("%m")
    run_parent = result_root / args.provider / year / month
    run_id, collision = resolve_run_id_collision(args.provider, source_identity, run_parent, created_at)
    run_root = run_parent / run_id
    create_run_layout(run_root)

    target = run_root / "source" / args.source_file.name
    if args.copy:
        shutil.copy2(args.source_file, target)
        intake_action = "copy"
    else:
        shutil.move(str(args.source_file), str(target))
        intake_action = "move"

    write_json(
        run_root / "manifest" / "run_manifest.json",
        {
            "run_id": run_id,
            "provider": args.provider,
            "created_at": created_at.isoformat(),
            "timezone": "Australia/Sydney",
            "source_file": str(target),
            "source_checksum_sha256": checksum,
            "source_identity": source_identity,
            "intake_mode": args.intake_mode,
            "intake_action": intake_action,
            "db_update_enabled": False,
            "run_id_collision": collision,
        },
    )
    print(run_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
