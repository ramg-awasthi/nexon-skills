from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from .common import DEFAULT_CONFIG_PATH, ensure_provider, load_config, sharepoint_roots, write_json


GRAPH_ROOT = "https://graph.microsoft.com/v1.0"


def _token() -> str:
    token = os.environ.get("NEXON_RECON_GRAPH_ACCESS_TOKEN")
    if token:
        return token

    tenant_id = os.environ.get("NEXON_RECON_SHAREPOINT_TENANT_ID")
    client_id = os.environ.get("NEXON_RECON_SHAREPOINT_CLIENT_ID")
    client_secret = os.environ.get("NEXON_RECON_SHAREPOINT_CLIENT_SECRET")
    if not all((tenant_id, client_id, client_secret)):
        raise RuntimeError(
            "sharepoint_auth_missing: Configure NEXON_RECON_SHAREPOINT_TENANT_ID, "
            "NEXON_RECON_SHAREPOINT_CLIENT_ID, and "
            "NEXON_RECON_SHAREPOINT_CLIENT_SECRET for application authentication."
        )

    body = urlencode(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": "https://graph.microsoft.com/.default",
            "grant_type": "client_credentials",
        }
    ).encode("utf-8")
    request = Request(
        f"https://login.microsoftonline.com/{quote(tenant_id, safe='')}/oauth2/v2.0/token",
        data=body,
        headers={"content-type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=60) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            "sharepoint_auth_failed: client-credential token request returned "
            f"HTTP {exc.code}: {detail[:500]}"
        ) from exc
    access_token = payload.get("access_token")
    if not access_token:
        raise RuntimeError(
            "sharepoint_auth_failed: token response did not include access_token."
        )
    return str(access_token)


def _drive_id() -> str:
    drive_id = os.environ.get("NEXON_RECON_SHAREPOINT_DRIVE_ID")
    if not drive_id:
        raise RuntimeError(
            "sharepoint_drive_missing: This legacy fallback connector requires "
            "NEXON_RECON_SHAREPOINT_DRIVE_ID in the environment. Fleet runtime should use the native SharePoint tool."
        )
    return drive_id


def _graph_request(method: str, path: str, body: dict | bytes | None = None, content_type: str = "application/json") -> bytes:
    headers = {"authorization": f"Bearer {_token()}"}
    data: bytes | None = None
    if isinstance(body, dict):
        data = json.dumps(body).encode("utf-8")
        headers["content-type"] = content_type
    elif isinstance(body, bytes):
        data = body
        headers["content-type"] = content_type
    request = Request(f"{GRAPH_ROOT}{path}", data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=180) as response:
            return response.read()
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"SharePoint Graph request failed: {method} {path} -> HTTP {exc.code}: {detail[:500]}") from exc


def _graph_json(method: str, path: str, body: dict | None = None) -> dict:
    payload = _graph_request(method, path, body)
    return json.loads(payload.decode("utf-8")) if payload else {}


def _drive_path(path: str) -> str:
    normalized = "/" + path.strip("/")
    return f"/drives/{_drive_id()}/root:{quote(normalized, safe='/')}:"


def _get_item(path: str) -> dict:
    return _graph_json("GET", _drive_path(path))


def _children(path: str) -> list[dict]:
    return _graph_json("GET", f"{_drive_path(path)}/children").get("value", [])


def _ensure_folder(path: str) -> dict:
    parts = [part for part in path.strip("/").split("/") if part]
    current = ""
    item: dict = {}
    for part in parts:
        parent = current
        current = f"{current}/{part}" if current else part
        try:
            item = _get_item(current)
        except RuntimeError:
            body = {"name": part, "folder": {}, "@microsoft.graph.conflictBehavior": "fail"}
            item = _graph_json("POST", f"{_drive_path(parent)}/children", body)
    return item


def _provider_paths(config: dict, provider: str) -> tuple[Path, Path]:
    upload_root, result_root = sharepoint_roots(config)
    return upload_root / provider, result_root / provider


def check_spaces(args: argparse.Namespace, config: dict) -> int:
    providers = [args.provider] if args.provider else sorted(config["providers"])
    if args.mode == "local":
        missing: list[str] = []
        for provider in providers:
            ensure_provider(config, provider)
            for path in _provider_paths(config, provider):
                if not path.is_dir():
                    missing.append(str(path))
        if missing:
            write_json(args.output, {"status": "setup_incomplete", "missing": missing})
            return 2
        write_json(args.output, {"status": "ok", "providers": providers})
        return 0

    checked: list[str] = []
    for provider in providers:
        ensure_provider(config, provider)
        for path_obj in _provider_paths(config, provider):
            path = path_obj.as_posix()
            _get_item(path)
            checked.append(path)
    write_json(args.output, {"status": "ok", "checked": checked})
    return 0


def find_upload(args: argparse.Namespace, config: dict) -> int:
    ensure_provider(config, args.provider)
    upload_path, _result_path = _provider_paths(config, args.provider)
    if args.mode == "local":
        files = [path for path in upload_path.iterdir() if path.is_file()]
        if args.source_name:
            files = [path for path in files if path.name == args.source_name]
        write_json(args.output, {"matches": [str(path) for path in files], "count": len(files)})
        return 0 if len(files) == 1 else 2

    children = [item for item in _children(upload_path.as_posix()) if "file" in item]
    if args.source_name:
        children = [item for item in children if item.get("name") == args.source_name]
    write_json(
        args.output,
        {"matches": [{"name": item.get("name"), "id": item.get("id"), "size": item.get("size")} for item in children], "count": len(children)},
    )
    return 0 if len(children) == 1 else 2


def download_upload(args: argparse.Namespace, config: dict) -> int:
    ensure_provider(config, args.provider)
    upload_root, _result_path = _provider_paths(config, args.provider)
    upload_path = upload_root / args.source_name
    destination = args.destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    if args.mode == "local":
        shutil.copy2(upload_path, destination)
        write_json(args.output, {"status": "downloaded", "destination": str(destination)})
        return 0

    item = _get_item(upload_path.as_posix())
    data = _graph_request("GET", f"/drives/{_drive_id()}/items/{item['id']}/content")
    destination.write_bytes(data)
    write_json(args.output, {"status": "downloaded", "source_item_id": item["id"], "destination": str(destination)})
    return 0


def move_upload_to_run_source(args: argparse.Namespace, config: dict) -> int:
    ensure_provider(config, args.provider)
    upload_root, _result_path = _provider_paths(config, args.provider)
    upload_path = upload_root / args.source_name
    if args.mode == "local":
        target_dir = Path(args.run_root) / "source"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / upload_path.name
        if args.copy:
            shutil.copy2(upload_path, target)
            action = "copied"
        else:
            shutil.move(str(upload_path), str(target))
            action = "moved"
        write_json(args.output, {"status": action, "target": str(target)})
        return 0

    run_source = f"{args.run_root.strip('/')}/source"
    source_item = _get_item(upload_path.as_posix())
    parent = _ensure_folder(run_source)
    if args.copy:
        body = {"parentReference": {"id": parent["id"]}, "name": args.source_name}
        result = _graph_json("POST", f"/drives/{_drive_id()}/items/{source_item['id']}/copy", body)
        status = "copy_started"
    else:
        body = {"parentReference": {"id": parent["id"]}, "name": args.source_name}
        result = _graph_json("PATCH", f"/drives/{_drive_id()}/items/{source_item['id']}", body)
        status = "moved"
    write_json(args.output, {"status": status, "result": result})
    return 0


def upload_artifact(args: argparse.Namespace, config: dict) -> int:
    if args.mode == "local":
        destination = Path(args.sharepoint_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(args.local_file, destination)
        write_json(args.output, {"status": "uploaded", "target": str(destination)})
        return 0

    data = args.local_file.read_bytes()
    _graph_request("PUT", f"{_drive_path(args.sharepoint_path)}/content", data, content_type="application/octet-stream")
    write_json(args.output, {"status": "uploaded", "target": args.sharepoint_path})
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="SharePoint connector for Nexon reconciliation spaces.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--mode", choices=["graph", "local"], default=os.environ.get("NEXON_RECON_SHAREPOINT_MODE", "graph"))
    subparsers = parser.add_subparsers(dest="command", required=True)

    check = subparsers.add_parser("check-spaces")
    check.add_argument("--provider")
    check.add_argument("--output", type=Path, required=True)

    find = subparsers.add_parser("find-upload")
    find.add_argument("--provider", required=True)
    find.add_argument("--source-name")
    find.add_argument("--output", type=Path, required=True)

    download = subparsers.add_parser("download-upload")
    download.add_argument("--provider", required=True)
    download.add_argument("--source-name", required=True)
    download.add_argument("--destination", type=Path, required=True)
    download.add_argument("--output", type=Path, required=True)

    move = subparsers.add_parser("move-upload-to-run-source")
    move.add_argument("--provider", required=True)
    move.add_argument("--source-name", required=True)
    move.add_argument("--run-root", required=True)
    move.add_argument("--copy", action="store_true")
    move.add_argument("--output", type=Path, required=True)

    upload = subparsers.add_parser("upload-artifact")
    upload.add_argument("--local-file", type=Path, required=True)
    upload.add_argument("--sharepoint-path", required=True)
    upload.add_argument("--output", type=Path, required=True)

    args = parser.parse_args()
    config = load_config(args.config)
    return {
        "check-spaces": check_spaces,
        "find-upload": find_upload,
        "download-upload": download_upload,
        "move-upload-to-run-source": move_upload_to_run_source,
        "upload-artifact": upload_artifact,
    }[args.command](args, config)


if __name__ == "__main__":
    raise SystemExit(main())
