from __future__ import annotations

import argparse
import hashlib
import json
from collections import deque
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

from . import sharepoint_connector
from .common import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_SHAREPOINT_REFERENCE_ROOT,
    DEFAULT_SHAREPOINT_UPLOAD_ROOT,
    PROVIDERS,
    ensure_provider,
    load_config,
    sha256_file,
    write_json,
)


INDEX_CONTRACT_VERSION = 1
MAX_INDEX_ITEMS = 10_000
MAX_INDEX_DEPTH = 16
MAX_INDEX_PAGES_PER_FOLDER = 100
MAX_PUBLIC_CANDIDATES = 50
PROVIDER_SOURCE_SUFFIXES = {
    "AAPT": {".zip"},
    "Telstra": {".csv", ".zip"},
    "Optus": {".dat", ".pdf", ".zip"},
    "Vocus": {".csv", ".zip"},
    "Megaport": {".csv", ".zip"},
    "Equinix": {".xlsx", ".zip"},
}
SPACE_ROOTS = {
    "upload": DEFAULT_SHAREPOINT_UPLOAD_ROOT,
    "reference": DEFAULT_SHAREPOINT_REFERENCE_ROOT,
}


def _selection_id(
    *, binding_sha256: str, drive_id: str, item_id: str, etag: str
) -> str:
    identity = "\0".join((binding_sha256, drive_id, item_id, etag))
    return f"SRC-{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:12].upper()}"


def _space_root(space: str) -> Path:
    try:
        return SPACE_ROOTS[space]
    except KeyError as exc:
        raise RuntimeError(f"sharepoint_space_invalid: {space}") from exc


def _next_page_path(next_link: Any) -> str | None:
    if next_link is None:
        return None
    if not isinstance(next_link, str) or not next_link.strip():
        raise RuntimeError("sharepoint_index_invalid: Graph returned an invalid nextLink.")
    parsed = urlsplit(next_link)
    if (
        parsed.scheme != "https"
        or parsed.netloc.lower() != "graph.microsoft.com"
        or not parsed.path.startswith("/v1.0/")
        or parsed.fragment
    ):
        raise RuntimeError("sharepoint_index_invalid: Graph nextLink escaped the v1.0 API.")
    path = parsed.path[len("/v1.0") :]
    return f"{path}?{parsed.query}" if parsed.query else path


def _list_children(folder_id: str) -> list[dict[str, Any]]:
    drive_id = sharepoint_connector._drive_id()
    path: str | None = (
        f"/drives/{drive_id}/items/{folder_id}/children?$top=200"
    )
    children: list[dict[str, Any]] = []
    seen_pages: set[str] = set()
    while path:
        if path in seen_pages or len(seen_pages) >= MAX_INDEX_PAGES_PER_FOLDER:
            raise RuntimeError(
                "sharepoint_index_limit: pagination repeated or exceeded the limit."
            )
        seen_pages.add(path)
        payload = sharepoint_connector._graph_json("GET", path)
        page = payload.get("value")
        if not isinstance(page, list) or not all(isinstance(item, dict) for item in page):
            raise RuntimeError(
                "sharepoint_index_invalid: Graph children response is malformed."
            )
        children.extend(page)
        if len(children) > MAX_INDEX_ITEMS:
            raise RuntimeError(
                "sharepoint_index_limit: folder item count exceeds the limit."
            )
        path = _next_page_path(payload.get("@odata.nextLink"))
    return children


def _provider_for(relative_path: str) -> str | None:
    first = PurePosixPath(relative_path).parts[0]
    return first if first in PROVIDERS else None


def build_index(args: argparse.Namespace, config: dict[str, Any]) -> int:
    root = _space_root(args.space)
    root_path = root.as_posix()
    root_item = sharepoint_connector._get_item(root_path)
    if not isinstance(root_item.get("folder"), dict):
        raise RuntimeError(
            "sharepoint_index_root_invalid: the approved space path is not a folder."
        )

    queue = deque([(root_item, root_path, 0)])
    files: list[dict[str, Any]] = []
    folder_count = 0
    seen_ids: set[str] = set()
    seen_paths: set[str] = set()
    seen_selection_ids: set[str] = set()
    item_count = 0
    drive_id = sharepoint_connector._drive_id()
    binding_sha256 = sharepoint_connector._BINDING_SHA256
    while queue:
        folder, folder_path, depth = queue.popleft()
        if depth > MAX_INDEX_DEPTH:
            raise RuntimeError("sharepoint_index_limit: folder depth exceeds the limit.")
        folder_id = str(folder.get("id") or "").strip()
        if not folder_id or folder_id in seen_ids:
            raise RuntimeError(
                "sharepoint_index_invalid: folder IDs are missing or duplicated."
            )
        seen_ids.add(folder_id)
        folder_count += 1
        for item in _list_children(folder_id):
            item_count += 1
            if item_count > MAX_INDEX_ITEMS:
                raise RuntimeError("sharepoint_index_limit: item count exceeds the limit.")
            item_id = str(item.get("id") or "").strip()
            name = sharepoint_connector._safe_basename(str(item.get("name") or ""))
            item_path = f"{folder_path.rstrip('/')}/{name}"
            normalized_path = sharepoint_connector._safe_drive_path(item_path)
            path_key = normalized_path.casefold()
            if not item_id or item_id in seen_ids or path_key in seen_paths:
                raise RuntimeError(
                    "sharepoint_index_invalid: item IDs or paths are duplicated."
                )
            seen_paths.add(path_key)
            relative_path = normalized_path[len(root_path.rstrip("/")) :].lstrip("/")
            if isinstance(item.get("folder"), dict):
                if depth == 0 and relative_path not in PROVIDERS:
                    continue
                queue.append((item, normalized_path, depth + 1))
                continue
            if not isinstance(item.get("file"), dict):
                raise RuntimeError(
                    "sharepoint_index_invalid: an item is neither a file nor a folder."
                )
            etag = str(item.get("eTag") or "").strip()
            if not etag:
                raise RuntimeError(
                    "sharepoint_index_invalid: file eTag is required."
                )
            selection_id = _selection_id(
                binding_sha256=binding_sha256,
                drive_id=drive_id,
                item_id=item_id,
                etag=etag,
            )
            if selection_id in seen_selection_ids:
                raise RuntimeError(
                    "sharepoint_index_invalid: selection IDs are duplicated."
                )
            seen_ids.add(item_id)
            seen_selection_ids.add(selection_id)
            files.append(
                {
                    "selection_id": selection_id,
                    "provider": _provider_for(relative_path),
                    "relative_path": relative_path,
                    "sharepoint_path": normalized_path,
                    "name": name,
                    "item_id": item_id,
                    "size": int(item.get("size") or 0),
                    "etag": etag,
                    "ctag": str(item.get("cTag") or ""),
                    "last_modified_utc": str(item.get("lastModifiedDateTime") or ""),
                    "mime_type": str(item["file"].get("mimeType") or ""),
                    "web_url": str(item.get("webUrl") or ""),
                }
            )

    files.sort(key=lambda item: (item["relative_path"].casefold(), item["item_id"]))
    binding = sharepoint_connector._BINDING or {}
    write_json(
        args.output,
        {
            "contract_version": INDEX_CONTRACT_VERSION,
            "status": "indexed",
            "space": args.space,
            "root_path": root_path,
            "site_id": binding.get("site_id"),
            "drive_id": drive_id,
            "binding_sha256": binding_sha256,
            "generated_at_utc": datetime.now(UTC).isoformat(),
            "folder_count": folder_count,
            "file_count": len(files),
            "files": files,
        },
    )
    return 0


def load_index(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("sharepoint_index_invalid: cannot read the index.") from exc
    if not isinstance(payload, dict) or payload.get("contract_version") != INDEX_CONTRACT_VERSION:
        raise RuntimeError("sharepoint_index_invalid: unsupported index contract.")
    files = payload.get("files")
    if not isinstance(files, list) or not all(isinstance(item, dict) for item in files):
        raise RuntimeError("sharepoint_index_invalid: files must be a list of objects.")
    return payload


def _validate_index_binding(index: dict[str, Any], space: str) -> None:
    binding = sharepoint_connector._BINDING or {}
    if (
        index.get("space") != space
        or index.get("site_id") != binding.get("site_id")
        or index.get("drive_id") != sharepoint_connector._drive_id()
        or index.get("binding_sha256") != sharepoint_connector._BINDING_SHA256
    ):
        raise RuntimeError(
            "sharepoint_index_binding_mismatch: index and active binding disagree."
        )


def _is_source_package(item: dict[str, Any]) -> bool:
    provider = item.get("provider")
    return bool(
        provider in PROVIDER_SOURCE_SUFFIXES
        and Path(str(item.get("name") or "")).suffix.lower()
        in PROVIDER_SOURCE_SUFFIXES[provider]
    )


def _sanitize_candidate(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "selection_id": item["selection_id"],
        "provider": item["provider"],
        "name": item["name"],
        "size": item["size"],
        "last_modified_utc": item["last_modified_utc"],
    }


def resolve_candidates(
    index: dict[str, Any],
    *,
    provider: str | None = None,
    source_name: str | None = None,
    selection_id: str | None = None,
) -> list[dict[str, Any]]:
    normalized_name = (
        sharepoint_connector._safe_basename(source_name)
        if source_name is not None
        else None
    )
    candidates = [item for item in index["files"] if _is_source_package(item)]
    if provider is not None:
        candidates = [
            item for item in candidates if item.get("provider") == provider
        ]
    if normalized_name is not None:
        candidates = [
            item
            for item in candidates
            if str(item.get("name") or "").casefold()
            == normalized_name.casefold()
        ]
    if selection_id is not None:
        candidates = [
            item for item in candidates if item.get("selection_id") == selection_id
        ]
    return candidates


def _download_selected(
    *,
    selected: dict[str, Any],
    space: str,
    index_path: Path,
    destination: Path,
    receipt_path: Path,
) -> dict[str, Any]:
    binding = sharepoint_connector._BINDING or {}
    required = (
        "selection_id",
        "provider",
        "name",
        "sharepoint_path",
        "item_id",
        "etag",
    )
    if any(
        not isinstance(selected.get(key), str) or not selected[key].strip()
        for key in required
    ):
        raise RuntimeError(
            "sharepoint_source_resolution_failed: indexed source identity is incomplete."
        )
    current = sharepoint_connector._get_item(str(selected.get("sharepoint_path") or ""))
    if (
        current.get("id") != selected.get("item_id")
        or int(current.get("size") or 0) != int(selected.get("size") or 0)
        or str(current.get("eTag") or "") != str(selected.get("etag") or "")
    ):
        raise RuntimeError(
            "sharepoint_source_identity_changed: indexed source metadata changed."
        )
    data = sharepoint_connector._graph_request(
        "GET",
        (
            f"/drives/{sharepoint_connector._drive_id()}/items/"
            f"{selected['item_id']}/content"
        ),
        if_match=str(selected["etag"]),
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    receipt = {
        "status": "downloaded",
        "space": space,
        "provider": selected["provider"],
        "source_name": selected["name"],
        "source_path": selected["sharepoint_path"],
        "source_item_id": selected["item_id"],
        "source_etag": selected["etag"],
        "source_web_url": selected.get("web_url"),
        "site_id": binding.get("site_id"),
        "drive_id": sharepoint_connector._drive_id(),
        "binding_sha256": sharepoint_connector._BINDING_SHA256,
        "index_path": str(index_path.resolve()),
        "index_sha256": sha256_file(index_path),
        "destination": str(destination.resolve()),
        "byte_count": len(data),
        "downloaded_sha256": hashlib.sha256(data).hexdigest(),
    }
    write_json(receipt_path, receipt)
    return receipt


def download_indexed(args: argparse.Namespace, config: dict[str, Any]) -> int:
    ensure_provider(config, args.provider)
    source_name = sharepoint_connector._safe_basename(args.source_name)
    index = load_index(args.index)
    _validate_index_binding(index, args.space)
    matches = resolve_candidates(
        index, provider=args.provider, source_name=source_name
    )
    if len(matches) != 1:
        raise RuntimeError(
            "sharepoint_source_resolution_failed: expected exactly one indexed source."
        )
    _download_selected(
        selected=matches[0],
        space=args.space,
        index_path=args.index,
        destination=args.destination,
        receipt_path=args.output,
    )
    return 0


def stage_source(args: argparse.Namespace, config: dict[str, Any]) -> int:
    if args.provider is not None:
        ensure_provider(config, args.provider)
    if args.selection_id and (
        args.provider is not None or args.source_name is not None or args.all
    ):
        raise RuntimeError(
            "sharepoint_stage_invalid: selection_id cannot be combined with filters or --all."
        )
    if args.selection_id:
        if not args.index.is_file():
            raise RuntimeError(
                "sharepoint_stage_invalid: selection requires the existing index."
            )
        expected_index_sha256 = str(
            args.expected_index_sha256 or ""
        ).strip().lower()
        if (
            len(expected_index_sha256) != 64
            or any(character not in "0123456789abcdef" for character in expected_index_sha256)
        ):
            raise RuntimeError(
                "sharepoint_stage_invalid: selection requires the expected index SHA-256."
            )
        if sha256_file(args.index) != expected_index_sha256:
            raise RuntimeError(
                "sharepoint_stage_invalid: selection index changed after choices were produced."
            )
    else:
        if args.expected_index_sha256 is not None:
            raise RuntimeError(
                "sharepoint_stage_invalid: expected index SHA-256 is valid only with selection_id."
            )
        build_index(
            argparse.Namespace(space=args.space, output=args.index),
            config,
        )
    index = load_index(args.index)
    _validate_index_binding(index, args.space)
    candidates = resolve_candidates(
        index,
        provider=args.provider,
        source_name=args.source_name,
        selection_id=args.selection_id,
    )
    sanitized = [_sanitize_candidate(item) for item in candidates]
    ignored_file_count = len(
        [item for item in index["files"] if not _is_source_package(item)]
    )
    if not candidates:
        write_json(
            args.output,
            {
                "status": "source_not_found",
                "space": args.space,
                "candidate_count": 0,
                "ignored_file_count": ignored_file_count,
            },
        )
        return 0
    if len(candidates) > MAX_PUBLIC_CANDIDATES:
        provider_counts = {
            provider: len(
                [item for item in candidates if item.get("provider") == provider]
            )
            for provider in sorted(PROVIDERS)
            if any(item.get("provider") == provider for item in candidates)
        }
        write_json(
            args.output,
            {
                "status": "selection_limit_exceeded",
                "space": args.space,
                "candidate_count": len(candidates),
                "candidate_limit": MAX_PUBLIC_CANDIDATES,
                "ignored_file_count": ignored_file_count,
                "provider_counts": provider_counts,
            },
        )
        return 0
    if args.all:
        write_json(
            args.output,
            {
                "status": "batch_plan_ready",
                "space": args.space,
                "candidate_count": len(sanitized),
                "ignored_file_count": ignored_file_count,
                "candidates": sanitized,
                "index_sha256": sha256_file(args.index),
            },
        )
        return 0
    if len(candidates) > 1:
        write_json(
            args.output,
            {
                "status": "selection_required",
                "space": args.space,
                "candidate_count": len(sanitized),
                "ignored_file_count": ignored_file_count,
                "candidates": sanitized,
                "index_sha256": sha256_file(args.index),
            },
        )
        return 0
    receipt = _download_selected(
        selected=candidates[0],
        space=args.space,
        index_path=args.index,
        destination=args.destination,
        receipt_path=args.receipt,
    )
    write_json(
        args.output,
        {
            "status": "staged",
            "space": args.space,
            "selection": _sanitize_candidate(candidates[0]),
            "destination": receipt["destination"],
            "byte_count": receipt["byte_count"],
            "downloaded_sha256": receipt["downloaded_sha256"],
            "receipt_path": str(args.receipt.resolve()),
            "cloud_action": {
                "source_item_id": receipt["source_item_id"],
            },
        },
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Index and download files from approved reconciliation spaces."
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--binding", type=Path, required=True)
    parser.add_argument("--auth-mode", choices=["auth_proxy"], default="auth_proxy")
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build")
    build.add_argument("--space", choices=sorted(SPACE_ROOTS), required=True)
    build.add_argument("--output", type=Path, required=True)

    download = subparsers.add_parser("download")
    download.add_argument("--space", choices=sorted(SPACE_ROOTS), required=True)
    download.add_argument("--provider", choices=sorted(PROVIDERS), required=True)
    download.add_argument("--source-name", required=True)
    download.add_argument("--index", type=Path, required=True)
    download.add_argument("--destination", type=Path, required=True)
    download.add_argument("--output", type=Path, required=True)

    stage = subparsers.add_parser("stage")
    stage.add_argument("--space", choices=sorted(SPACE_ROOTS), required=True)
    stage.add_argument("--provider", choices=sorted(PROVIDERS))
    stage.add_argument("--source-name")
    stage.add_argument("--selection-id")
    stage.add_argument("--expected-index-sha256")
    stage.add_argument("--all", action="store_true")
    stage.add_argument("--index", type=Path, required=True)
    stage.add_argument("--destination", type=Path, required=True)
    stage.add_argument("--receipt", type=Path, required=True)
    stage.add_argument("--output", type=Path, required=True)

    args = parser.parse_args()
    sharepoint_connector.configure_runtime(
        auth_mode=args.auth_mode,
        binding_path=args.binding,
    )
    config = load_config(args.config)
    return {
        "build": build_index,
        "download": download_indexed,
        "stage": stage_source,
    }[args.command](args, config)


if __name__ == "__main__":
    raise SystemExit(main())
