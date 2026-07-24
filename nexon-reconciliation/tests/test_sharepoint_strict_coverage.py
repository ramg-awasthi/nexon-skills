from __future__ import annotations

import io
import json
import runpy
import sys
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from unittest.mock import Mock

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from recon_core import sharepoint_connector as connector  # noqa: E402
from recon_core import common  # noqa: E402


class FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.payload


def graph_error(code: int = 403, detail: bytes = b"denied") -> HTTPError:
    return HTTPError("https://example.invalid", code, "error", {}, io.BytesIO(detail))


def payload(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def config() -> dict:
    return {
        "providers": {"Telstra": {}, "AAPT": {}},
        "provider_api_adapters": {"aapt": True},
    }


@pytest.fixture(autouse=True)
def isolate_connector(monkeypatch: pytest.MonkeyPatch) -> None:
    connector.configure_runtime(auth_mode="auth_proxy", binding_path=None)
    for name in (
        "NEXON_RECON_GRAPH_ACCESS_TOKEN",
        "NEXON_RECON_SHAREPOINT_TENANT_ID",
        "NEXON_RECON_SHAREPOINT_CLIENT_ID",
        "NEXON_RECON_SHAREPOINT_CLIENT_SECRET",
        "NEXON_RECON_SHAREPOINT_DRIVE_ID",
    ):
        monkeypatch.delenv(name, raising=False)


def test_drive_and_graph_request_variants(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(RuntimeError, match="sharepoint_drive_missing"):
        connector._drive_id()
    monkeypatch.setattr(
        connector, "_BINDING", {"site_id": "site-id", "drive_id": "drive-id"}
    )
    monkeypatch.setattr(connector, "_AUTHORIZED_ITEM_IDS", {"item-1", "limited"})
    assert connector._drive_id() == "drive-id"

    requests = []

    def urlopen(request: object, timeout: int) -> FakeResponse:
        requests.append((request, timeout))
        return FakeResponse(b"response")

    monkeypatch.setattr(connector, "urlopen", urlopen)
    with pytest.raises(RuntimeError, match="sharepoint_read_only_violation"):
        connector._graph_request("POST", "/dict", {"answer": 42})
    with pytest.raises(RuntimeError, match="sharepoint_read_only_violation"):
        connector._graph_request("PUT", "/bytes", b"raw", "application/octet-stream")
    with pytest.raises(RuntimeError, match="sharepoint_read_only_violation"):
        connector._graph_json("GET", "/body", {"answer": 42})
    with pytest.raises(RuntimeError, match="sharepoint_route_violation"):
        connector._graph_request("GET", "/me")
    assert (
        connector._graph_request("GET", "/drives/drive-id/items/item-1")
        == b"response"
    )

    none_request, request_timeout = requests[0]
    assert request_timeout == 180
    assert none_request.data is None
    assert none_request.get_header("Content-type") is None
    assert none_request.get_header("Authorization") is None

    def failed_urlopen(*_args: object, **_kwargs: object) -> FakeResponse:
        raise graph_error(429, b"retry later")

    monkeypatch.setattr(connector, "urlopen", failed_urlopen)
    with pytest.raises(
        RuntimeError,
        match=r"SharePoint Graph request failed: GET /drives/drive-id/items/limited -> HTTP 429: retry later",
    ):
        connector._graph_request("GET", "/drives/drive-id/items/limited")


def test_graph_json_and_read_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    request = Mock(side_effect=[b'{"value": 7}', b""])
    monkeypatch.setattr(connector, "_graph_request", request)
    assert connector._graph_json("GET", "/value") == {"value": 7}
    assert connector._graph_json("GET", "/empty") == {}

    monkeypatch.setattr(connector, "_BINDING", {"drive_id": "drive"})
    calls = []

    def graph_json(method: str, path: str, body: dict | None = None) -> dict:
        calls.append((method, path, body))
        return {"id": "item"}

    monkeypatch.setattr(connector, "_graph_json", graph_json)
    assert connector._drive_path("/folder with space/") == (
        "/drives/drive/root:/folder%20with%20space:"
    )
    assert connector._get_item("folder") == {"id": "item"}
    with pytest.raises(RuntimeError, match="sharepoint_path_invalid"):
        connector._drive_path("../outside")


def test_graph_route_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    connector.configure_runtime(auth_mode="auth_proxy", binding_path=None)
    site_lookup = (
        "/sites/tenant.sharepoint.com:/sites/NexonReconciliationAutomation"
    )
    assert connector._graph_path_allowed(site_lookup)
    assert not connector._graph_path_allowed("/sites/tenant.sharepoint.com:/sites/Other")
    monkeypatch.setattr(
        connector,
        "_graph_request",
        lambda *_args, **_kwargs: b'{"id":"tenant,site,web"}',
    )
    assert connector._graph_json("GET", site_lookup)["id"] == "tenant,site,web"
    assert connector._graph_path_allowed("/sites/tenant%2Csite%2Cweb/drive")
    assert not connector._graph_path_allowed("/sites/different/drive")

    monkeypatch.setattr(
        connector,
        "_BINDING",
        {"site_id": "tenant,site,web", "drive_id": "drive-id"},
    )
    monkeypatch.setattr(connector, "_AUTHORIZED_ITEM_IDS", set())
    root_item_path = (
        "/drives/drive-id/root:/recon-upload-space/AAPT/invoice.zip:"
    )
    monkeypatch.setattr(connector, "_graph_request", lambda *_args: b"{}")
    assert connector._graph_json("GET", root_item_path) == {}
    monkeypatch.setattr(
        connector, "_graph_request", lambda *_args: b'{"id":"item-1"}'
    )
    assert connector._graph_json("GET", root_item_path)["id"] == "item-1"
    assert connector._AUTHORIZED_ITEM_IDS == {"item-1"}
    assert connector._graph_path_allowed("/sites/tenant,site,web")
    assert connector._graph_path_allowed("/drives/drive-id")
    assert connector._graph_path_allowed(
        "/drives/drive-id/root:/recon-upload-space/AAPT/invoice.zip:"
    )
    assert connector._graph_path_allowed(
        "/drives/drive-id/root:/recon-result-space/AAPT/run/report.xlsx:"
    )
    assert not connector._graph_path_allowed(
        "/drives/drive-id/root:/Shared/other.txt:"
    )
    assert not connector._graph_path_allowed(
        "/drives/drive-id/root:/recon-upload-space/../outside:"
    )
    assert not connector._graph_path_allowed(
        "/drives/drive-id/root:/recon-result-space/%2e%2e/outside:"
    )
    assert connector._graph_path_allowed("/drives/drive-id/items/item-1")
    assert connector._graph_path_allowed("/drives/drive-id/items/item-1/content")
    assert not connector._graph_path_allowed("/drives/drive-id/items/unpinned")
    assert not connector._graph_path_allowed(
        "/drives/drive-id/items/item-1?expand=children"
    )
    assert not connector._graph_path_allowed("/users")


def test_download_upload_local_and_graph(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    config: dict,
) -> None:
    upload_root = tmp_path / "upload"
    provider_root = upload_root / "AAPT"
    provider_root.mkdir(parents=True)
    source = provider_root / "invoice.csv"
    source.write_bytes(b"local invoice")
    monkeypatch.setattr(connector, "sharepoint_roots", lambda _config: (upload_root, tmp_path / "result"))
    monkeypatch.setattr(connector, "ensure_provider", Mock())

    local_destination = tmp_path / "staging" / "local.csv"
    local_output = tmp_path / "local-download.json"
    local_args = SimpleNamespace(
        provider="AAPT",
        source_name=source.name,
        destination=local_destination,
        mode="local",
        output=local_output,
    )
    assert connector.download_upload(local_args, config) == 0
    assert local_destination.read_bytes() == b"local invoice"

    monkeypatch.setattr(
        connector,
        "_get_item",
        lambda _path: {"id": "item-7", "webUrl": "https://tenant/item"},
    )
    monkeypatch.setattr(connector, "_drive_id", lambda: "drive-9")
    monkeypatch.setattr(
        connector,
        "_BINDING",
        {"site_id": "site-1", "drive_id": "drive-9"},
    )
    monkeypatch.setattr(connector, "_BINDING_SHA256", "a" * 64)
    graph_request = Mock(return_value=b"graph invoice")
    monkeypatch.setattr(connector, "_graph_request", graph_request)
    graph_destination = tmp_path / "staging" / "graph.csv"
    graph_output = tmp_path / "graph-download.json"
    graph_args = SimpleNamespace(
        provider="AAPT",
        source_name=source.name,
        source_item_id="item-7",
        destination=graph_destination,
        mode="graph",
        output=graph_output,
    )
    assert connector.download_upload(graph_args, config) == 0
    assert graph_destination.read_bytes() == b"graph invoice"
    graph_request.assert_called_once_with(
        "GET", "/drives/drive-9/items/item-7/content"
    )
    assert payload(graph_output)["source_item_id"] == "item-7"


def test_main_builds_cli_and_dispatches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.yaml"
    output = tmp_path / "output.json"
    loaded_config = {"providers": {}}
    load_config = Mock(return_value=loaded_config)
    command = Mock(return_value=17)
    monkeypatch.setattr(connector, "load_config", load_config)
    monkeypatch.setattr(connector, "download_upload", command)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "sharepoint_connector.py",
            "--config",
            str(config_path),
            "--mode",
            "local",
            "download-upload",
            "--provider",
            "AAPT",
            "--source-name",
            "invoice.zip",
            "--destination",
            str(tmp_path / "invoice.zip"),
            "--output",
            str(output),
        ],
    )

    assert connector.main() == 17
    load_config.assert_called_once_with(config_path)
    args, passed_config = command.call_args.args
    assert passed_config is loaded_config
    assert vars(args) == {
        "auth_mode": "auth_proxy",
        "binding": None,
        "command": "download-upload",
        "config": config_path,
        "destination": tmp_path / "invoice.zip",
        "mode": "local",
        "output": output,
        "provider": "AAPT",
        "source_item_id": None,
        "source_name": "invoice.zip",
    }


def test_module_entry_point(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upload = tmp_path / "upload" / "AAPT"
    upload.mkdir(parents=True)
    source = upload / "source.txt"
    source.write_text("artifact", encoding="utf-8")
    target = tmp_path / "staging" / "target.txt"
    output = tmp_path / "entry-point.json"
    monkeypatch.setattr(
        common,
        "load_config",
        lambda _path: {"providers": {"AAPT": {}}},
    )
    monkeypatch.setattr(
        common,
        "sharepoint_roots",
        lambda _config: (tmp_path / "upload", tmp_path / "result"),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "sharepoint_connector.py",
            "--mode",
            "local",
            "download-upload",
            "--provider",
            "AAPT",
            "--source-name",
            source.name,
            "--destination",
            str(target),
            "--output",
            str(output),
        ],
    )

    with pytest.warns(RuntimeWarning, match="found in sys.modules"):
        with pytest.raises(SystemExit) as raised:
            runpy.run_module("recon_core.sharepoint_connector", run_name="__main__")

    assert raised.value.code == 0
    assert target.read_text(encoding="utf-8") == "artifact"
    assert payload(output)["status"] == "downloaded"
