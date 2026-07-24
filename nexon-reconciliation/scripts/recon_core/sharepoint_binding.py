from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse


SHAREPOINT_BINDING_CONTRACT_VERSION = 1
SHAREPOINT_SITE_NAME = "Nexon Reconciliation Automation"
SHAREPOINT_SITE_PATH = "/sites/NexonReconciliationAutomation"


def _normalized_path(value: str) -> str:
    path = unquote(value).strip()
    if not path.startswith("/"):
        path = f"/{path}"
    if any(part in {".", ".."} for part in path.split("/")):
        raise RuntimeError(
            "sharepoint_binding_invalid: SharePoint paths cannot contain dot segments."
        )
    return path.rstrip("/") or "/"


def _validated_https_url(value: Any, *, field: str) -> tuple[str, str]:
    text = str(value or "").strip()
    parsed = urlparse(text)
    try:
        port = parsed.port
    except ValueError as exc:
        raise RuntimeError(f"sharepoint_binding_invalid: {field} has an invalid port.") from exc
    hostname = (parsed.hostname or "").lower()
    if (
        parsed.scheme.lower() != "https"
        or not hostname.endswith(".sharepoint.com")
        or parsed.username
        or parsed.password
        or port not in {None, 443}
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeError(
            f"sharepoint_binding_invalid: {field} must be a clean HTTPS SharePoint URL."
        )
    return text, hostname


def validate_binding(binding: Any) -> dict[str, Any]:
    if not isinstance(binding, dict):
        raise RuntimeError("sharepoint_binding_invalid: binding must be an object.")
    if binding.get("contract_version") != SHAREPOINT_BINDING_CONTRACT_VERSION:
        raise RuntimeError("sharepoint_binding_invalid: unsupported contract_version.")
    if binding.get("site_name") != SHAREPOINT_SITE_NAME:
        raise RuntimeError("sharepoint_binding_invalid: site_name does not match the fixed contract.")

    site_url, hostname = _validated_https_url(binding.get("site_url"), field="site_url")
    site_path = _normalized_path(str(binding.get("site_path") or ""))
    if site_path.casefold() != SHAREPOINT_SITE_PATH.casefold():
        raise RuntimeError("sharepoint_binding_invalid: site_path does not match the fixed contract.")
    if _normalized_path(urlparse(site_url).path).casefold() != site_path.casefold():
        raise RuntimeError("sharepoint_binding_invalid: site_url and site_path disagree.")
    if str(binding.get("hostname") or "").strip().lower() != hostname:
        raise RuntimeError("sharepoint_binding_invalid: hostname and site_url disagree.")

    site_id = str(binding.get("site_id") or "").strip()
    drive_id = str(binding.get("drive_id") or "").strip()
    if not site_id or not drive_id:
        raise RuntimeError("sharepoint_binding_invalid: site_id and drive_id are required.")

    drive_url, drive_hostname = _validated_https_url(
        binding.get("drive_web_url"), field="drive_web_url"
    )
    drive_path = _normalized_path(urlparse(drive_url).path)
    if drive_hostname != hostname or not drive_path.casefold().startswith(
        f"{site_path.casefold()}/"
    ):
        raise RuntimeError(
            "sharepoint_binding_invalid: default drive is outside the selected site."
        )
    drive_name = str(binding.get("drive_name") or "").strip()
    if not drive_name:
        raise RuntimeError("sharepoint_binding_invalid: drive_name is required.")
    discovery_sha256 = str(binding.get("discovery_sha256") or "").strip().lower()
    if not re.fullmatch(r"[a-f0-9]{64}", discovery_sha256):
        raise RuntimeError(
            "sharepoint_binding_invalid: discovery_sha256 must be a SHA-256 digest."
        )

    return {
        "contract_version": SHAREPOINT_BINDING_CONTRACT_VERSION,
        "site_name": SHAREPOINT_SITE_NAME,
        "site_id": site_id,
        "site_url": site_url.rstrip("/"),
        "hostname": hostname,
        "site_path": site_path,
        "drive_id": drive_id,
        "drive_name": drive_name,
        "drive_web_url": drive_url.rstrip("/"),
        "discovery_sha256": discovery_sha256,
    }


def load_binding(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"sharepoint_binding_invalid: cannot read {path}.") from exc
    return validate_binding(payload)
