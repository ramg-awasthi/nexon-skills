from __future__ import annotations

import argparse
import shutil
import stat
import zipfile
from pathlib import Path

from .common import DEFAULT_CONFIG_PATH, load_config, positive_limit, sha256_file, write_json


DEFAULT_MAX_ZIP_MEMBERS = 2000
DEFAULT_MAX_SINGLE_FILE_BYTES = 250 * 1024 * 1024
DEFAULT_MAX_TOTAL_EXPANDED_BYTES = 1000 * 1024 * 1024
DEFAULT_MAX_COMPRESSION_RATIO = 100


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
    max_total_expanded_bytes: int = DEFAULT_MAX_TOTAL_EXPANDED_BYTES,
    max_compression_ratio: int = DEFAULT_MAX_COMPRESSION_RATIO,
) -> dict:
    inventory = {
        "archive": str(zip_path),
        "archive_sha256": sha256_file(zip_path),
        "members": [],
        "blocked": [],
        "total_expanded_bytes": 0,
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
            total_expanded = 0
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
                    total_expanded += info.file_size
                    if total_expanded > max_total_expanded_bytes:
                        raise ValueError(
                            f"Archive expanded size {total_expanded} exceeds limit {max_total_expanded_bytes}."
                        )
                    if info.file_size and info.compress_size == 0:
                        raise ValueError(f"Archive member has invalid zero compressed size: {name}")
                    if info.compress_size:
                        ratio = info.file_size / info.compress_size
                        if ratio > max_compression_ratio:
                            raise ValueError(
                                f"Archive member compression ratio {ratio:.2f} exceeds limit "
                                f"{max_compression_ratio}: {name}"
                            )
                    unix_mode = (info.external_attr >> 16) & 0xFFFF
                    if unix_mode and stat.S_ISLNK(unix_mode):
                        raise ValueError(f"Archive symlink blocked: {name}")
                    if not info.is_dir():
                        planned.append((info, target))
                except Exception as exc:
                    inventory["blocked"].append({"name": name, "reason": str(exc)})

            if inventory["blocked"]:
                return inventory

            inventory["total_expanded_bytes"] = total_expanded
            output_dir.mkdir(parents=True, exist_ok=True)
            for info, target in planned:
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as src, target.open("wb") as dst:
                    shutil.copyfileobj(src, dst, length=1024 * 1024)
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
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--zip", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--max-members", type=int)
    parser.add_argument("--max-single-file-mb", type=int)
    parser.add_argument("--max-total-expanded-mb", type=int)
    parser.add_argument("--max-compression-ratio", type=int)
    args = parser.parse_args()

    config = load_config(args.config) if args.config.is_file() else {}
    max_members = args.max_members or positive_limit(config, "max_zip_members", DEFAULT_MAX_ZIP_MEMBERS)
    max_single_file_mb = args.max_single_file_mb or positive_limit(config, "max_single_file_mb", 250)
    max_total_expanded_mb = args.max_total_expanded_mb or positive_limit(config, "max_total_expanded_mb", 1000)
    max_compression_ratio = args.max_compression_ratio or positive_limit(
        config, "max_compression_ratio", DEFAULT_MAX_COMPRESSION_RATIO
    )
    inventory = extract_zip(
        args.zip,
        args.output_dir,
        max_members=max_members,
        max_single_file_bytes=max_single_file_mb * 1024 * 1024,
        max_total_expanded_bytes=max_total_expanded_mb * 1024 * 1024,
        max_compression_ratio=max_compression_ratio,
    )
    write_json(args.manifest, inventory)
    if inventory["blocked"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
