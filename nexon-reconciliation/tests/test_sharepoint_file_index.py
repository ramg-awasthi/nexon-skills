from __future__ import annotations

import argparse
import json
import runpy
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from recon_core import sharepoint_connector as connector  # noqa: E402
from recon_core import sharepoint_file_index as indexer  # noqa: E402


def binding_file(tmp_path: Path) -> Path:
    path = tmp_path / "binding.json"
    path.write_text(
        json.dumps(
            {
                "contract_version": 1,
                "site_name": "Nexon Reconciliation Automation",
                "site_id": "tenant.sharepoint.com,site,web",
                "site_url": (
                    "https://tenant.sharepoint.com/sites/"
                    "NexonReconciliationAutomation"
                ),
                "hostname": "tenant.sharepoint.com",
                "site_path": "/sites/NexonReconciliationAutomation",
                "drive_id": "drive-id",
                "drive_name": "Documents",
                "drive_web_url": (
                    "https://tenant.sharepoint.com/sites/"
                    "NexonReconciliationAutomation/Shared%20Documents"
                ),
                "discovery_sha256": "a" * 64,
            }
        ),
        encoding="utf-8",
    )
    return path


def configure(tmp_path: Path) -> Path:
    path = binding_file(tmp_path)
    connector.configure_runtime(auth_mode="auth_proxy", binding_path=path)
    return path


def file_item(item_id: str, name: str, size: int = 10) -> dict:
    return {
        "id": item_id,
        "name": name,
        "size": size,
        "eTag": f"etag-{item_id}",
        "cTag": f"ctag-{item_id}",
        "lastModifiedDateTime": "2026-07-24T00:00:00Z",
        "webUrl": f"https://tenant/{item_id}",
        "file": {"mimeType": "application/zip"},
    }


def folder_item(item_id: str, name: str = "folder") -> dict:
    return {"id": item_id, "name": name, "folder": {"childCount": 1}}


def config() -> dict:
    return {"providers": {"AAPT": {}, "Telstra": {}}}


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_space_next_link_and_provider_helpers() -> None:
    assert indexer._space_root("upload").as_posix() == "/recon-upload-space"
    assert indexer._provider_for("AAPT/invoice.zip") == "AAPT"
    assert indexer._provider_for("misc/file.zip") is None
    assert indexer._selection_id(
        binding_sha256="a" * 64,
        drive_id="drive",
        item_id="item",
        etag="etag",
    ) == indexer._selection_id(
        binding_sha256="a" * 64,
        drive_id="drive",
        item_id="item",
        etag="etag",
    )
    assert indexer._next_page_path(None) is None
    assert indexer._next_page_path(
        "https://graph.microsoft.com/v1.0/drives/d/items/i/children?$skiptoken=x"
    ) == "/drives/d/items/i/children?$skiptoken=x"
    assert indexer._next_page_path(
        "https://graph.microsoft.com/v1.0/drives/d/items/i/children"
    ) == "/drives/d/items/i/children"
    with pytest.raises(RuntimeError, match="space_invalid"):
        indexer._space_root("result")
    for value in (
        "",
        7,
        "http://graph.microsoft.com/v1.0/x",
        "https://example.com/v1.0/x",
        "https://graph.microsoft.com/beta/x",
        "https://graph.microsoft.com/v1.0/x#fragment",
    ):
        with pytest.raises(RuntimeError, match="nextLink"):
            indexer._next_page_path(value)


def test_list_children_paginates_and_rejects_malformed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(connector, "_drive_id", lambda: "drive")
    calls: list[str] = []
    pages = [
        {
            "value": [{"id": "one"}],
            "@odata.nextLink": (
                "https://graph.microsoft.com/v1.0/drives/drive/items/root/"
                "children?$skiptoken=next"
            ),
        },
        {"value": [{"id": "two"}]},
    ]

    def graph_json(_method: str, path: str) -> dict:
        calls.append(path)
        return pages.pop(0)

    monkeypatch.setattr(connector, "_graph_json", graph_json)
    assert indexer._list_children("root") == [{"id": "one"}, {"id": "two"}]
    assert calls[0].endswith("children?$top=200")
    assert calls[1].endswith("children?$skiptoken=next")

    monkeypatch.setattr(connector, "_graph_json", lambda *_args: {"value": {}})
    with pytest.raises(RuntimeError, match="children response"):
        indexer._list_children("root")
    monkeypatch.setattr(connector, "_graph_json", lambda *_args: {"value": [1]})
    with pytest.raises(RuntimeError, match="children response"):
        indexer._list_children("root")

    repeated = (
        "https://graph.microsoft.com/v1.0/drives/drive/items/root/"
        "children?$skiptoken=same"
    )
    monkeypatch.setattr(
        connector,
        "_graph_json",
        lambda *_args: {"value": [], "@odata.nextLink": repeated},
    )
    with pytest.raises(RuntimeError, match="pagination repeated"):
        indexer._list_children("root")

    monkeypatch.setattr(indexer, "MAX_INDEX_ITEMS", 1)
    monkeypatch.setattr(
        connector,
        "_graph_json",
        lambda *_args: {"value": [{"id": "one"}, {"id": "two"}]},
    )
    with pytest.raises(RuntimeError, match="folder item count"):
        indexer._list_children("root")


def test_build_full_space_index(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure(tmp_path)
    root = folder_item("root", "recon-reference-space")
    monkeypatch.setattr(connector, "_get_item", lambda _path: root)
    children = {
        "root": [
            folder_item("aapt-folder", "AAPT"),
            folder_item("misc-folder", "misc"),
        ],
        "aapt-folder": [
            file_item("z-file", "z.zip"),
            folder_item("nested-folder", "nested"),
        ],
        "nested-folder": [file_item("a-file", "a.zip")],
        "misc-folder": [file_item("misc-file", "other.zip")],
    }
    monkeypatch.setattr(indexer, "_list_children", lambda item_id: children[item_id])
    output = tmp_path / "reference-index.json"
    args = argparse.Namespace(space="reference", output=output)

    assert indexer.build_index(args, config()) == 0
    payload = read_json(output)
    assert payload["status"] == "indexed"
    assert payload["root_path"] == "/recon-reference-space"
    assert payload["folder_count"] == 3
    assert payload["file_count"] == 2
    assert [item["relative_path"] for item in payload["files"]] == [
        "AAPT/nested/a.zip",
        "AAPT/z.zip",
    ]
    assert [item["provider"] for item in payload["files"]] == [
        "AAPT",
        "AAPT",
    ]


def test_build_index_validation_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure(tmp_path)
    output = tmp_path / "index.json"
    args = argparse.Namespace(space="upload", output=output)
    monkeypatch.setattr(
        connector,
        "_get_item",
        lambda path: folder_item("root", Path(path).name),
    )
    monkeypatch.setattr(
        indexer,
        "_list_children",
        lambda item_id: (
            [folder_item("aapt", "AAPT")]
            if item_id == "root"
            else [file_item("file", "invoice.zip")]
        ),
    )
    assert indexer.build_index(args, config()) == 0
    payload = read_json(output)
    assert payload["root_path"] == "/recon-upload-space"
    assert payload["file_count"] == 1
    assert payload["files"][0]["provider"] == "AAPT"
    assert payload["files"][0]["relative_path"] == "AAPT/invoice.zip"

    monkeypatch.setattr(connector, "_get_item", lambda _path: file_item("x", "x"))
    with pytest.raises(RuntimeError, match="not a folder"):
        indexer.build_index(args, config())

    monkeypatch.setattr(connector, "_get_item", lambda _path: folder_item("", "root"))
    with pytest.raises(RuntimeError, match="folder IDs"):
        indexer.build_index(args, config())

    monkeypatch.setattr(connector, "_get_item", lambda _path: folder_item("root", "root"))
    monkeypatch.setattr(
        indexer,
        "_list_children",
        lambda _item_id: [{"id": "odd", "name": "odd"}],
    )
    with pytest.raises(RuntimeError, match="neither a file nor a folder"):
        indexer.build_index(args, config())

    monkeypatch.setattr(
        indexer,
        "_list_children",
        lambda _item_id: [{**file_item("file", "invoice.zip"), "eTag": ""}],
    )
    with pytest.raises(RuntimeError, match="file eTag is required"):
        indexer.build_index(args, config())

    monkeypatch.setattr(
        indexer,
        "_list_children",
        lambda _item_id: [
            file_item("one", "one.zip"),
            file_item("two", "two.zip"),
        ],
    )
    monkeypatch.setattr(indexer, "_selection_id", lambda **_kwargs: "SRC-SAME")
    with pytest.raises(RuntimeError, match="selection IDs are duplicated"):
        indexer.build_index(args, config())


def test_build_index_limits_and_duplicates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure(tmp_path)
    args = argparse.Namespace(
        space="reference",
        output=tmp_path / "index.json",
    )
    monkeypatch.setattr(connector, "_get_item", lambda _path: folder_item("root", "root"))

    monkeypatch.setattr(indexer, "MAX_INDEX_DEPTH", 0)
    monkeypatch.setattr(
        indexer,
        "_list_children",
        lambda item_id: [folder_item("child", "AAPT")]
        if item_id == "root"
        else [],
    )
    with pytest.raises(RuntimeError, match="folder depth"):
        indexer.build_index(args, config())

    monkeypatch.setattr(indexer, "MAX_INDEX_DEPTH", 16)
    monkeypatch.setattr(indexer, "MAX_INDEX_ITEMS", 1)
    monkeypatch.setattr(
        indexer,
        "_list_children",
        lambda _item_id: [
            file_item("one", "one.zip"),
            file_item("two", "two.zip"),
        ],
    )
    with pytest.raises(RuntimeError, match="item count"):
        indexer.build_index(args, config())

    monkeypatch.setattr(indexer, "MAX_INDEX_ITEMS", 10_000)
    monkeypatch.setattr(
        indexer,
        "_list_children",
        lambda _item_id: [
            file_item("same", "one.zip"),
            file_item("same", "two.zip"),
        ],
    )
    with pytest.raises(RuntimeError, match="duplicated"):
        indexer.build_index(args, config())
    monkeypatch.setattr(
        indexer,
        "_list_children",
        lambda _item_id: [
            file_item("one", "Same.zip"),
            file_item("two", "same.zip"),
        ],
    )
    with pytest.raises(RuntimeError, match="duplicated"):
        indexer.build_index(args, config())


def valid_index(tmp_path: Path, binding: Path, *, files: list[dict] | None = None) -> Path:
    connector.configure_runtime(auth_mode="auth_proxy", binding_path=binding)
    path = tmp_path / "index.json"
    path.write_text(
        json.dumps(
            {
                "contract_version": 1,
                "space": "reference",
                "site_id": connector._BINDING["site_id"],
                "drive_id": connector._BINDING["drive_id"],
                "binding_sha256": connector._BINDING_SHA256,
                "files": files
                if files is not None
                else [
                    {
                        "provider": "AAPT",
                        "selection_id": "SRC-AAPT",
                        "name": "invoice.zip",
                        "sharepoint_path": (
                            "/recon-reference-space/AAPT/invoice.zip"
                        ),
                        "item_id": "item-1",
                        "size": 7,
                        "etag": "etag-1",
                        "last_modified_utc": "2026-07-24T00:00:00Z",
                        "web_url": "https://tenant/item-1",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def test_load_index_failures(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="cannot read"):
        indexer.load_index(tmp_path / "missing.json")
    invalid = tmp_path / "invalid.json"
    invalid.write_text("{", encoding="utf-8")
    with pytest.raises(RuntimeError, match="cannot read"):
        indexer.load_index(invalid)
    invalid.write_text("[]", encoding="utf-8")
    with pytest.raises(RuntimeError, match="unsupported"):
        indexer.load_index(invalid)
    invalid.write_text(json.dumps({"contract_version": 2}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="unsupported"):
        indexer.load_index(invalid)
    invalid.write_text(
        json.dumps({"contract_version": 1, "files": {}}),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="files must"):
        indexer.load_index(invalid)
    invalid.write_text(
        json.dumps({"contract_version": 1, "files": [1]}),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="files must"):
        indexer.load_index(invalid)


def test_download_indexed_success_and_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding = configure(tmp_path)
    index_path = valid_index(tmp_path, binding)
    destination = tmp_path / "staging" / "invoice.zip"
    output = tmp_path / "receipt.json"
    args = argparse.Namespace(
        space="reference",
        provider="AAPT",
        source_name="invoice.zip",
        index=index_path,
        destination=destination,
        output=output,
    )
    monkeypatch.setattr(
        connector,
        "_get_item",
        lambda _path: {"id": "item-1", "size": 7, "eTag": "etag-1"},
    )
    request = Mock(return_value=b"invoice")
    monkeypatch.setattr(connector, "_graph_request", request)
    assert indexer.download_indexed(args, config()) == 0
    assert destination.read_bytes() == b"invoice"
    receipt = read_json(output)
    assert receipt["space"] == "reference"
    assert receipt["source_item_id"] == "item-1"
    assert receipt["index_path"] == str(index_path.resolve())
    assert receipt["downloaded_sha256"]
    request.assert_called_once_with(
        "GET",
        "/drives/drive-id/items/item-1/content",
        if_match="etag-1",
    )

    mismatch = read_json(index_path)
    mismatch["drive_id"] = "other"
    index_path.write_text(json.dumps(mismatch), encoding="utf-8")
    with pytest.raises(RuntimeError, match="binding_mismatch"):
        indexer.download_indexed(args, config())

    index_path = valid_index(tmp_path, binding, files=[])
    args.index = index_path
    with pytest.raises(RuntimeError, match="exactly one"):
        indexer.download_indexed(args, config())

    duplicated = [
        {
            "provider": "AAPT",
            "name": "invoice.zip",
            "sharepoint_path": "/recon-reference-space/AAPT/invoice.zip",
            "item_id": f"item-{number}",
            "size": 7,
            "etag": "etag-1",
        }
        for number in (1, 2)
    ]
    args.index = valid_index(tmp_path, binding, files=duplicated)
    with pytest.raises(RuntimeError, match="exactly one"):
        indexer.download_indexed(args, config())

    args.index = valid_index(tmp_path, binding)
    monkeypatch.setattr(
        connector,
        "_get_item",
        lambda _path: {"id": "changed", "size": 7, "eTag": "etag-1"},
    )
    with pytest.raises(RuntimeError, match="identity_changed"):
        indexer.download_indexed(args, config())


def stage_index(
    tmp_path: Path,
    binding: Path,
    *,
    files: list[dict],
    space: str = "reference",
) -> Path:
    connector.configure_runtime(auth_mode="auth_proxy", binding_path=binding)
    path = tmp_path / "stage-index.json"
    path.write_text(
        json.dumps(
            {
                "contract_version": 1,
                "space": space,
                "site_id": connector._BINDING["site_id"],
                "drive_id": connector._BINDING["drive_id"],
                "binding_sha256": connector._BINDING_SHA256,
                "files": files,
            }
        ),
        encoding="utf-8",
    )
    return path


def candidate(
    *,
    selection_id: str,
    provider: str = "AAPT",
    name: str = "invoice.zip",
    item_id: str = "item-1",
) -> dict:
    return {
        "selection_id": selection_id,
        "provider": provider,
        "name": name,
        "relative_path": f"{provider}/{name}",
        "sharepoint_path": f"/recon-reference-space/{provider}/{name}",
        "item_id": item_id,
        "size": 7,
        "etag": f"etag-{item_id}",
        "ctag": f"ctag-{item_id}",
        "last_modified_utc": "2026-07-24T00:00:00Z",
        "mime_type": "application/zip",
        "web_url": f"https://tenant/{item_id}",
    }


def stage_args(tmp_path: Path, index: Path, **changes: object) -> argparse.Namespace:
    values: dict[str, object] = {
        "space": "reference",
        "provider": None,
        "source_name": None,
        "selection_id": None,
        "expected_index_sha256": None,
        "all": False,
        "index": index,
        "destination": tmp_path / "staged.zip",
        "receipt": tmp_path / "receipt.json",
        "output": tmp_path / "stage-result.json",
    }
    values.update(changes)
    return argparse.Namespace(**values)


def test_candidate_resolution_and_sanitization() -> None:
    files = [
        candidate(selection_id="SRC-A", name="one.zip"),
        candidate(
            selection_id="SRC-B",
            provider="Telstra",
            name="two.csv",
            item_id="item-2",
        ),
        candidate(selection_id="SRC-C", name="notes.txt", item_id="item-3"),
        {**candidate(selection_id="SRC-D", item_id="item-4"), "provider": None},
        candidate(selection_id="SRC-E", name="wrong.csv", item_id="item-5"),
    ]
    index = {"files": files}
    assert [item["selection_id"] for item in indexer.resolve_candidates(index)] == [
        "SRC-A",
        "SRC-B",
    ]
    assert indexer.resolve_candidates(index, provider="Telstra")[0][
        "selection_id"
    ] == "SRC-B"
    assert indexer.resolve_candidates(index, source_name="ONE.ZIP")[0][
        "selection_id"
    ] == "SRC-A"
    assert indexer.resolve_candidates(index, selection_id="SRC-B")[0][
        "selection_id"
    ] == "SRC-B"
    sanitized = indexer._sanitize_candidate(files[0])
    assert set(sanitized) == {
        "selection_id",
        "provider",
        "name",
        "size",
        "last_modified_utc",
    }
    assert "item_id" not in sanitized


def test_stage_source_selection_not_found_and_batch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding = configure(tmp_path)
    files = [
        candidate(selection_id="SRC-A", name="one.zip"),
        candidate(
            selection_id="SRC-B",
            provider="Telstra",
            name="two.csv",
            item_id="item-2",
        ),
        candidate(selection_id="SRC-C", name="notes.txt", item_id="item-3"),
    ]
    index_path = stage_index(tmp_path, binding, files=files)
    build = Mock()
    monkeypatch.setattr(indexer, "build_index", build)

    args = stage_args(tmp_path, index_path)
    assert indexer.stage_source(args, config()) == 0
    result = read_json(args.output)
    assert result["status"] == "selection_required"
    assert result["candidate_count"] == 2
    assert result["ignored_file_count"] == 1
    assert all("item_id" not in item for item in result["candidates"])
    build.assert_called_once()

    args.output = tmp_path / "batch.json"
    args.all = True
    assert indexer.stage_source(args, config()) == 0
    assert read_json(args.output)["status"] == "batch_plan_ready"

    args.output = tmp_path / "missing.json"
    args.all = False
    args.provider = "AAPT"
    args.source_name = "missing.zip"
    assert indexer.stage_source(args, config()) == 0
    assert read_json(args.output)["status"] == "source_not_found"


def test_stage_source_auto_download_and_selection_continuation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding = configure(tmp_path)
    selected = candidate(selection_id="SRC-A")
    index_path = stage_index(tmp_path, binding, files=[selected])
    monkeypatch.setattr(indexer, "build_index", Mock())
    monkeypatch.setattr(
        connector,
        "_get_item",
        lambda _path: {
            "id": selected["item_id"],
            "size": selected["size"],
            "eTag": selected["etag"],
        },
    )
    request = Mock(return_value=b"invoice")
    monkeypatch.setattr(connector, "_graph_request", request)
    args = stage_args(tmp_path, index_path)
    assert indexer.stage_source(args, config()) == 0
    result = read_json(args.output)
    assert result["status"] == "staged"
    assert set(result["selection"]) == {
        "selection_id",
        "provider",
        "name",
        "size",
        "last_modified_utc",
    }
    assert result["cloud_action"] == {"source_item_id": "item-1"}
    assert "source_item_id" not in result["selection"]
    assert read_json(args.receipt)["source_item_id"] == "item-1"

    args.output = tmp_path / "selected-result.json"
    args.receipt = tmp_path / "selected-receipt.json"
    args.destination = tmp_path / "selected.zip"
    args.selection_id = "SRC-A"
    args.expected_index_sha256 = indexer.sha256_file(index_path)
    assert indexer.stage_source(args, config()) == 0
    assert args.destination.read_bytes() == b"invoice"


def test_stage_source_guards_and_incomplete_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding = configure(tmp_path)
    index_path = stage_index(
        tmp_path,
        binding,
        files=[candidate(selection_id="SRC-A")],
    )
    args = stage_args(
        tmp_path,
        index_path,
        provider="AAPT",
        selection_id="SRC-A",
    )
    with pytest.raises(RuntimeError, match="cannot be combined"):
        indexer.stage_source(args, config())

    args.provider = None
    args.index = tmp_path / "missing-index.json"
    with pytest.raises(RuntimeError, match="requires the existing index"):
        indexer.stage_source(args, config())

    args.index = index_path
    args.expected_index_sha256 = None
    with pytest.raises(RuntimeError, match="requires the expected index SHA-256"):
        indexer.stage_source(args, config())

    args.expected_index_sha256 = "0" * 64
    with pytest.raises(RuntimeError, match="selection index changed"):
        indexer.stage_source(args, config())

    args.selection_id = None
    with pytest.raises(RuntimeError, match="valid only with selection_id"):
        indexer.stage_source(args, config())

    incomplete = candidate(selection_id="SRC-A")
    incomplete["item_id"] = ""
    with pytest.raises(RuntimeError, match="identity is incomplete"):
        indexer._download_selected(
            selected=incomplete,
            space="reference",
            index_path=index_path,
            destination=tmp_path / "bad.zip",
            receipt_path=tmp_path / "bad-receipt.json",
        )


def test_stage_source_bounds_public_candidates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding = configure(tmp_path)
    files = [
        candidate(
            selection_id=f"SRC-{number:03d}",
            name=f"invoice-{number}.zip",
            item_id=f"item-{number}",
        )
        for number in range(indexer.MAX_PUBLIC_CANDIDATES + 1)
    ]
    index_path = stage_index(tmp_path, binding, files=files)
    monkeypatch.setattr(indexer, "build_index", Mock())
    args = stage_args(tmp_path, index_path)
    assert indexer.stage_source(args, config()) == 0
    result = read_json(args.output)
    assert result["status"] == "selection_limit_exceeded"
    assert result["candidate_count"] == indexer.MAX_PUBLIC_CANDIDATES + 1
    assert result["provider_counts"] == {
        "AAPT": indexer.MAX_PUBLIC_CANDIDATES + 1
    }
    assert "candidates" not in result


def test_main_dispatch_and_entry_point(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding = binding_file(tmp_path)
    config_path = tmp_path / "config.yaml"
    output = tmp_path / "index.json"
    loaded_config = config()
    command = Mock(return_value=19)
    monkeypatch.setattr(indexer, "load_config", Mock(return_value=loaded_config))
    monkeypatch.setattr(indexer, "build_index", command)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "sharepoint_file_index.py",
            "--config",
            str(config_path),
            "--binding",
            str(binding),
            "build",
            "--space",
            "upload",
            "--output",
            str(output),
        ],
    )
    assert indexer.main() == 19
    args, passed_config = command.call_args.args
    assert args.space == "upload"
    assert not hasattr(args, "provider")
    assert passed_config is loaded_config

    monkeypatch.setattr(indexer, "main", lambda: 0)
    with pytest.raises(SystemExit) as raised:
        runpy.run_path(
            str(SCRIPTS / "sharepoint_file_index.py"),
            run_name="__main__",
        )
    assert raised.value.code == 0
