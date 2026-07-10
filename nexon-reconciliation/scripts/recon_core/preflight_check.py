from __future__ import annotations

import argparse
from pathlib import Path

from .common import DEFAULT_CONFIG_PATH, PROVIDER_CONFIG_KEYS, PROVIDERS, ensure_db_update_disabled, load_config, sharepoint_roots


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate one-time Nexon recon folder setup.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--local-check", action="store_true", help="Treat fixed roots as local filesystem paths.")
    args = parser.parse_args()

    config = load_config(args.config)
    ensure_db_update_disabled(config)
    upload_root, result_root = sharepoint_roots(config)

    provider_api_adapters = config.get("provider_api_adapters", {})
    if not isinstance(provider_api_adapters, dict):
        raise RuntimeError("provider_api_adapters must be a mapping when present.")
    unsupported_adapters = set(provider_api_adapters) - set(PROVIDER_CONFIG_KEYS)
    if unsupported_adapters:
        raise RuntimeError(f"Unsupported provider API adapter config: {sorted(unsupported_adapters)}")
    disabled_adapters = [provider for provider, enabled in provider_api_adapters.items() if enabled is not True]
    if disabled_adapters:
        raise RuntimeError(f"Remove disabled provider API adapter entries instead of setting false: {sorted(disabled_adapters)}")

    if not args.local_check:
        print("Setup config validated. SharePoint permissions must be checked with the native SharePoint tool.")
        return 0

    problems: list[str] = []
    for provider in sorted(PROVIDERS):
        if not (upload_root / provider).is_dir():
            problems.append(f"Missing upload folder: {upload_root / provider}")
        if not (result_root / provider).is_dir():
            problems.append(f"Missing result folder: {result_root / provider}")

    if problems:
        for problem in problems:
            print(problem)
        return 2

    print("Local setup validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
