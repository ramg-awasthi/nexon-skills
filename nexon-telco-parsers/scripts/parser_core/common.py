from __future__ import annotations

import json
from pathlib import Path
from typing import Any


PROVIDERS = {"AAPT", "Telstra", "Optus", "Vocus", "Megaport", "Equinix"}
SKILLS_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG_PATH = SKILLS_ROOT / "nexon-reconciliation" / "config" / "recon_settings.yaml"


def load_config(path: Path) -> dict[str, Any]:
    try:
        import yaml  # type: ignore
    except ImportError as exc:
        raise RuntimeError("PyYAML is required to read recon_settings.yaml.") from exc

    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Config must be a mapping: {path}")
    return data


def ensure_provider(config: dict[str, Any], provider: str) -> dict[str, Any]:
    if provider not in PROVIDERS:
        raise ValueError(f"Provider is not supported: {provider}")
    return {"provider": provider}


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
