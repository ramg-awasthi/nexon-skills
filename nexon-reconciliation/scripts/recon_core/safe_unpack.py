from __future__ import annotations

import argparse
import zipfile
from pathlib import Path

from .common import sha256_file, write_json


DEFAULT_MAX_ZIP_MEMBERS = 2000
DEFAULT_MAX_SINGLE_FILE_BYTES = 250 * 1024 * 1024


def _safe_member_path(root: Path, member_name: str) -> Path:
    if member_name.startswith(("/", "\\")):
        raise ValueError(f"Absolute archive path blocked: {member_name}")
    if ":" in Path(member_name).parts[0]:
        raise ValueError(f"Drive-qualified archive path blocked: {member_name}")
    target = (root / member_name).resolve()
    root_resolved = root.resolve()
    try:
        target.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError(f"Path traversal blocked: {member_name}")
    return target


def extract_zip(
    zip_path: Path,
    output_dir: Path,
    max_members: int = DEFAULT_MAX_ZIP_MEMBERS,
    max_single_file_bytes: int = DEFAULT_MAX_SINGLE_FILE_BYTES,
) -> dict:
    inventory = {
        "archive": str(zip_path),
        "archive_sha256": sha256_file(zip_path),
        "members": [],
        "blocked": [],
    }
    planned: list[tuple[zipfile.ZipInfo, Path]] = []

    try:
        with zipfile.ZipFile(zip_path) as archive:
            infos = archive.infolist()
            if len(infos) > max_members:
                inventory["blocked"].append(
                    {
                        "name": str(zip_path),
                        "reason": f"Archive member count {len(infos)} exceeds limit {max_members}.",
                    }
                )

            seen: set[str] = set()
            for info in infos:
                name = info.filename
                try:
                    target = _safe_member_path(output_dir, name)
                    normalized = str(target.relative_to(output_dir.resolve()))
                    normalized_key = normalized.replace("\\", "/").lower()
                    if normalized_key in seen:
                        raise ValueError(f"Duplicate normalized archive path: {name}")
                    seen.add(normalized_key)
                    if info.file_size > max_single_file_bytes:
                        raise ValueError(
                            f"Archive member size {info.file_size} exceeds limit {max_single_file_bytes}: {name}"
                        )
                    if not info.is_dir():
                        planned.append((info, target))
                except Exception as exc:
                    inventory["blocked"].append({"name": name, "reason": str(exc)})

            if inventory["blocked"]:
                return inventory

            output_dir.mkdir(parents=True, exist_ok=True)
            for info, target in planned:
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as src, target.open("wb") as dst:
                    dst.write(src.read())
                inventory["members"].append(
                    {
                        "name": info.filename,
                        "path": str(target),
                        "size": info.file_size,
                        "sha256": sha256_file(target),
                    }
                )
    except zipfile.BadZipFile as exc:
        inventory["blocked"].append({"name": zip_path.name, "reason": f"Unreadable archive: {exc}"})
    except OSError as exc:
        inventory["blocked"].append({"name": zip_path.name, "reason": f"Archive read failed: {exc}"})

    return inventory


def main() -> int:
    parser = argparse.ArgumentParser(description="Safely extract a ZIP into a run extracted directory.")
    parser.add_argument("--zip", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--max-members", type=int, default=DEFAULT_MAX_ZIP_MEMBERS)
    parser.add_argument("--max-single-file-mb", type=int, default=250)
    args = parser.parse_args()

    inventory = extract_zip(
        args.zip,
        args.output_dir,
        max_members=args.max_members,
        max_single_file_bytes=args.max_single_file_mb * 1024 * 1024,
    )
    write_json(args.manifest, inventory)
    if inventory["blocked"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
