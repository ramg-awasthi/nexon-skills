from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote, unquote
from urllib.request import Request, urlopen

from .common import (
    DEFAULT_CONFIG_PATH,
    ensure_provider,
    load_config,
    sha256_file,
    sharepoint_roots,
    write_json,
)
from .sharepoint_binding import load_binding


GRAPH_ROOT = "https://graph.microsoft.com/v1.0"
_AUTH_MODE = "auth_proxy"
_BINDING: dict | None = None
_BINDING_SHA256 = ""
_RESOLUTION_SITE_ID = ""
_AUTHORIZED_ITEM_IDS: set[str] = set()


def configure_runtime(*, auth_mode: str, binding_path: Path | None) -> None:
    global _AUTH_MODE, _BINDING, _BINDING_SHA256, _RESOLUTION_SITE_ID
    global _AUTHORIZED_ITEM_IDS
    if auth_mode != "auth_proxy":
        raise RuntimeError(f"sharepoint_auth_mode_invalid: {auth_mode}")
    _AUTH_MODE = auth_mode
    _BINDING = load_binding(binding_path) if binding_path is not None else None
    _BINDING_SHA256 = sha256_file(binding_path) if binding_path is not None else ""
    _RESOLUTION_SITE_ID = ""
    _AUTHORIZED_ITEM_IDS = set()


def _drive_id() -> str:
    if _BINDING is not None:
        return str(_BINDING["drive_id"])
    raise RuntimeError(
        "sharepoint_drive_missing: a validated SharePoint binding is required."
    )


def _graph_request(method: str, path: str, body: dict | bytes | None = None, content_type: str = "application/json") -> bytes:
    if method != "GET" or body is not None:
        raise RuntimeError(
            "sharepoint_read_only_violation: the profile-backed connector permits GET without a request body only."
        )
    if not _graph_path_allowed(path):
        raise RuntimeError(
            "sharepoint_route_violation: Graph request is outside the resolved reconciliation site/drive."
        )
    request = Request(f"{GRAPH_ROOT}{path}", method="GET")
    try:
        with urlopen(request, timeout=180) as response:
            return response.read()
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"SharePoint Graph request failed: {method} {path} -> HTTP {exc.code}: {detail[:500]}") from exc


def _graph_json(method: str, path: str, body: dict | None = None) -> dict:
    global _RESOLUTION_SITE_ID, _AUTHORIZED_ITEM_IDS
    if body is not None:
        raise RuntimeError(
            "sharepoint_read_only_violation: Graph JSON requests cannot include a body."
        )
    payload = _graph_request(method, path, body)
    result = json.loads(payload.decode("utf-8")) if payload else {}
    if _BINDING is None and re.fullmatch(
        r"/sites/[A-Za-z0-9.-]+:/sites/NexonReconciliationAutomation", path
    ):
        _RESOLUTION_SITE_ID = str(result.get("id") or "")
    elif _BINDING is not None and "/root:" in path:
        item_id = str(result.get("id") or "").strip()
        if item_id:
            _AUTHORIZED_ITEM_IDS.add(item_id)
    return result


def _graph_path_allowed(path: str) -> bool:
    if "?" in path or "#" in path:
        return False
    if _BINDING is None:
        if re.fullmatch(
            r"/sites/[A-Za-z0-9.-]+\.sharepoint\.com:/sites/NexonReconciliationAutomation",
            path,
        ):
            return True
        return bool(
            _RESOLUTION_SITE_ID
            and path == f"/sites/{quote(_RESOLUTION_SITE_ID, safe='')}/drive"
        )

    site_id = str(_BINDING["site_id"])
    drive_id = str(_BINDING["drive_id"])
    if path in {f"/sites/{site_id}", f"/drives/{drive_id}"}:
        return True
    root_prefix = f"/drives/{drive_id}/root:"
    if path.startswith(root_prefix) and path.endswith(":"):
        try:
            drive_path = _safe_drive_path(path[len(root_prefix) : -1])
        except RuntimeError:
            return False
        return drive_path.startswith(
            ("/recon-upload-space/", "/recon-result-space/")
        )
    item_match = re.fullmatch(
        rf"/drives/{re.escape(drive_id)}/items/([^/]+)(?:/content)?",
        path,
    )
    return bool(
        item_match and item_match.group(1) in _AUTHORIZED_ITEM_IDS
    )


def _drive_path(path: str) -> str:
    normalized = _safe_drive_path(path)
    return f"/drives/{_drive_id()}/root:{quote(normalized, safe='/')}:"


def _safe_drive_path(path: str) -> str:
    decoded = unquote(path).replace("\\", "/")
    parts = [part for part in decoded.split("/") if part]
    if not parts or any(part in {".", ".."} for part in parts):
        raise RuntimeError("sharepoint_path_invalid: path is empty or contains traversal.")
    return "/" + "/".join(parts)


def _safe_basename(value: str) -> str:
    decoded = value.strip()
    if (
        not decoded
        or Path(decoded).name != decoded
        or "/" in decoded
        or "\\" in decoded
        or decoded in {".", ".."}
    ):
        raise RuntimeError("sharepoint_source_name_invalid: source name must be a basename.")
    return decoded


def _get_item(path: str) -> dict:
    return _graph_json("GET", _drive_path(path))


def _provider_paths(config: dict, provider: str) -> tuple[Path, Path]:
    upload_root, result_root = sharepoint_roots(config)
    return upload_root / provider, result_root / provider


def download_upload(args: argparse.Namespace, config: dict) -> int:
    ensure_provider(config, args.provider)
    source_name = _safe_basename(args.source_name)
    upload_root, _result_path = _provider_paths(config, args.provider)
    upload_path = upload_root / source_name
    destination = args.destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    if args.mode == "local":
        shutil.copy2(upload_path, destination)
        write_json(
            args.output,
            {
                "status": "downloaded",
                "provider": args.provider,
                "source_name": source_name,
                "destination": str(destination.resolve()),
                "byte_count": destination.stat().st_size,
                "downloaded_sha256": sha256_file(destination),
            },
        )
        return 0

    item = _get_item(upload_path.as_posix())
    if not args.source_item_id or item.get("id") != args.source_item_id:
        raise RuntimeError(
            "sharepoint_source_identity_changed: selected item ID no longer matches the upload path."
        )
    data = _graph_request("GET", f"/drives/{_drive_id()}/items/{item['id']}/content")
    destination.write_bytes(data)
    write_json(
        args.output,
        {
            "status": "downloaded",
            "provider": args.provider,
            "source_name": source_name,
            "destination": str(destination.resolve()),
            "source_item_id": item["id"],
            "source_web_url": item.get("webUrl"),
            "site_id": _BINDING["site_id"] if _BINDING else "",
            "drive_id": _drive_id(),
            "binding_sha256": _BINDING_SHA256,
            "byte_count": len(data),
            "downloaded_sha256": hashlib.sha256(data).hexdigest(),
        },
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="SharePoint connector for Nexon reconciliation spaces.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--mode", choices=["graph", "local"], default=os.environ.get("NEXON_RECON_SHAREPOINT_MODE", "graph"))
    parser.add_argument(
        "--auth-mode",
        choices=["auth_proxy"],
        default=os.environ.get("NEXON_RECON_SHAREPOINT_AUTH_MODE", "auth_proxy"),
    )
    parser.add_argument("--binding", type=Path)
    subparsers = parser.add_subparsers(dest="command", required=True)

    download = subparsers.add_parser("download-upload")
    download.add_argument("--provider", required=True)
    download.add_argument("--source-name", required=True)
    download.add_argument("--source-item-id")
    download.add_argument("--destination", type=Path, required=True)
    download.add_argument("--output", type=Path, required=True)

    args = parser.parse_args()
    configure_runtime(auth_mode=args.auth_mode, binding_path=args.binding)
    if args.mode == "graph" and args.binding is None:
        raise RuntimeError(
            "sharepoint_binding_required: graph operations require a validated target binding."
        )
    config = load_config(args.config)
    return {
        "download-upload": download_upload,
    }[args.command](args, config)


if __name__ == "__main__":
    raise SystemExit(main())
