from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlparse

from .common import write_json
from .sharepoint_binding import (
    SHAREPOINT_BINDING_CONTRACT_VERSION,
    SHAREPOINT_SITE_NAME,
    SHAREPOINT_SITE_PATH,
    validate_binding,
)
from . import sharepoint_connector


_SITE_LIST_KEYS = ("value", "sites", "items", "results")
_SITE_NAME_KEYS = ("displayName", "display_name", "name", "title")
_SITE_URL_KEYS = ("webUrl", "web_url", "url")


def _first_text(record: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _site_records(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        records = payload
    elif isinstance(payload, dict):
        containers = [
            payload[key]
            for key in _SITE_LIST_KEYS
            if isinstance(payload.get(key), list)
        ]
        if len(containers) > 1:
            raise RuntimeError(
                "sharepoint_discovery_invalid: multiple site-list containers are ambiguous."
            )
        if containers:
            records = containers[0]
        elif _first_text(payload, _SITE_NAME_KEYS) and _first_text(payload, _SITE_URL_KEYS):
            records = [payload]
        else:
            raise RuntimeError(
                "sharepoint_discovery_invalid: expected a site list from the native SharePoint tool."
            )
    else:
        raise RuntimeError("sharepoint_discovery_invalid: discovery payload must be an object or list.")
    if not all(isinstance(record, dict) for record in records):
        raise RuntimeError("sharepoint_discovery_invalid: every site result must be an object.")
    return records


def select_site(payload: Any) -> dict[str, str]:
    matches: list[dict[str, str]] = []
    for record in _site_records(payload):
        name = _first_text(record, _SITE_NAME_KEYS)
        web_url = _first_text(record, _SITE_URL_KEYS)
        parsed = urlparse(web_url)
        normalized_path = unquote(parsed.path).rstrip("/") or "/"
        if (
            name.casefold() == SHAREPOINT_SITE_NAME.casefold()
            and normalized_path.casefold() == SHAREPOINT_SITE_PATH.casefold()
            and parsed.scheme.lower() == "https"
            and (parsed.hostname or "").lower().endswith(".sharepoint.com")
        ):
            matches.append(
                {
                    "site_name": SHAREPOINT_SITE_NAME,
                    "site_url": web_url.rstrip("/"),
                    "hostname": (parsed.hostname or "").lower(),
                }
            )
    if not matches:
        raise RuntimeError(
            "sharepoint_site_not_found: no exact site-name and site-path match was returned."
        )
    if len(matches) != 1:
        raise RuntimeError(
            "sharepoint_site_match_ambiguous: multiple exact site matches were returned."
        )
    return matches[0]


def resolve_target(
    *,
    discovery_path: Path,
    output_path: Path,
    auth_mode: str,
) -> dict[str, Any]:
    try:
        discovery_bytes = discovery_path.read_bytes()
        payload = json.loads(discovery_bytes.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"sharepoint_discovery_invalid: cannot read {discovery_path}."
        ) from exc
    selected = select_site(payload)
    sharepoint_connector.configure_runtime(auth_mode=auth_mode, binding_path=None)
    site_lookup = (
        f"/sites/{quote(selected['hostname'], safe='')}:"
        f"{quote(SHAREPOINT_SITE_PATH, safe='/')}"
    )
    try:
        graph_site = sharepoint_connector._graph_json("GET", site_lookup)
        graph_site_id = str(graph_site.get("id") or "").strip()
        if not graph_site_id:
            raise RuntimeError("Graph did not return a site ID.")
        drive = sharepoint_connector._graph_json(
            "GET", f"/sites/{quote(graph_site_id, safe='')}/drive"
        )
    except RuntimeError as exc:
        raise RuntimeError(
            "profile_site_mismatch: the active Graph profile cannot validate the selected site."
        ) from exc

    if (
        str(graph_site.get("webUrl") or "").rstrip("/").casefold()
        != selected["site_url"].casefold()
    ):
        raise RuntimeError(
            "profile_site_mismatch: Graph returned a different site identity."
        )
    if drive.get("driveType") != "documentLibrary":
        raise RuntimeError(
            "sharepoint_default_drive_not_found: selected site has no default document library."
        )

    parsed_site = urlparse(selected["site_url"])
    binding = validate_binding(
        {
            "contract_version": SHAREPOINT_BINDING_CONTRACT_VERSION,
            **selected,
            "site_id": graph_site_id,
            "hostname": (parsed_site.hostname or "").lower(),
            "site_path": SHAREPOINT_SITE_PATH,
            "drive_id": drive.get("id"),
            "drive_name": drive.get("name"),
            "drive_web_url": drive.get("webUrl"),
            "discovery_sha256": hashlib.sha256(discovery_bytes).hexdigest(),
        }
    )
    write_json(output_path, binding)
    return binding


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Resolve and validate the fixed reconciliation SharePoint target."
    )
    parser.add_argument("--sites-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--auth-mode",
        choices=["auth_proxy"],
        default="auth_proxy",
    )
    args = parser.parse_args()
    binding = resolve_target(
        discovery_path=args.sites_file,
        output_path=args.output,
        auth_mode=args.auth_mode,
    )
    print(binding)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
