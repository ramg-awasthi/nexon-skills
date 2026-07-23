from __future__ import annotations

import io
import json
import runpy
import sys
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.parse import parse_qs
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
    for name in (
        "NEXON_RECON_GRAPH_ACCESS_TOKEN",
        "NEXON_RECON_SHAREPOINT_TENANT_ID",
        "NEXON_RECON_SHAREPOINT_CLIENT_ID",
        "NEXON_RECON_SHAREPOINT_CLIENT_SECRET",
        "NEXON_RECON_SHAREPOINT_DRIVE_ID",
    ):
        monkeypatch.delenv(name, raising=False)


def test_token_sources_and_auth_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEXON_RECON_GRAPH_ACCESS_TOKEN", "ready-token")
    assert connector._token() == "ready-token"
    monkeypatch.delenv("NEXON_RECON_GRAPH_ACCESS_TOKEN")

    with pytest.raises(RuntimeError, match="sharepoint_auth_missing"):
        connector._token()

    monkeypatch.setenv("NEXON_RECON_SHAREPOINT_TENANT_ID", "tenant / name")
    monkeypatch.setenv("NEXON_RECON_SHAREPOINT_CLIENT_ID", "client")
    monkeypatch.setenv("NEXON_RECON_SHAREPOINT_CLIENT_SECRET", "secret")
    requests = []

    def successful_urlopen(request: object, timeout: int) -> FakeResponse:
        requests.append((request, timeout))
        return FakeResponse(b'{"access_token": 12345}')

    monkeypatch.setattr(connector, "urlopen", successful_urlopen)
    assert connector._token() == "12345"
    request, timeout = requests[0]
    assert timeout == 60
    assert request.full_url.endswith("tenant%20%2F%20name/oauth2/v2.0/token")
    assert parse_qs(request.data.decode("utf-8")) == {
        "client_id": ["client"],
        "client_secret": ["secret"],
        "scope": ["https://graph.microsoft.com/.default"],
        "grant_type": ["client_credentials"],
    }

    monkeypatch.setattr(connector, "urlopen", lambda *_args, **_kwargs: FakeResponse(b"{}"))
    with pytest.raises(RuntimeError, match="did not include access_token"):
        connector._token()

    def failed_urlopen(*_args: object, **_kwargs: object) -> FakeResponse:
        raise graph_error(401, b"bad credentials")

    monkeypatch.setattr(connector, "urlopen", failed_urlopen)
    with pytest.raises(RuntimeError, match=r"HTTP 401: bad credentials"):
        connector._token()


def test_drive_and_graph_request_variants(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(RuntimeError, match="sharepoint_drive_missing"):
        connector._drive_id()
    monkeypatch.setenv("NEXON_RECON_SHAREPOINT_DRIVE_ID", "drive id")
    assert connector._drive_id() == "drive id"

    monkeypatch.setattr(connector, "_token", lambda: "token")
    requests = []

    def urlopen(request: object, timeout: int) -> FakeResponse:
        requests.append((request, timeout))
        return FakeResponse(b"response")

    monkeypatch.setattr(connector, "urlopen", urlopen)
    assert connector._graph_request("POST", "/dict", {"answer": 42}) == b"response"
    assert connector._graph_request("PUT", "/bytes", b"raw", "application/octet-stream") == b"response"
    assert connector._graph_request("GET", "/none") == b"response"

    dict_request, dict_timeout = requests[0]
    bytes_request, _ = requests[1]
    none_request, _ = requests[2]
    assert dict_timeout == 180
    assert json.loads(dict_request.data) == {"answer": 42}
    assert dict_request.get_header("Content-type") == "application/json"
    assert bytes_request.data == b"raw"
    assert bytes_request.get_header("Content-type") == "application/octet-stream"
    assert none_request.data is None
    assert none_request.get_header("Content-type") is None
    assert none_request.get_header("Authorization") == "Bearer token"

    def failed_urlopen(*_args: object, **_kwargs: object) -> FakeResponse:
        raise graph_error(429, b"retry later")

    monkeypatch.setattr(connector, "urlopen", failed_urlopen)
    with pytest.raises(
        RuntimeError,
        match=r"SharePoint Graph request failed: GET /limited -> HTTP 429: retry later",
    ):
        connector._graph_request("GET", "/limited")


def test_graph_json_paths_and_folder_creation(monkeypatch: pytest.MonkeyPatch) -> None:
    request = Mock(side_effect=[b'{"value": 7}', b""])
    monkeypatch.setattr(connector, "_graph_request", request)
    assert connector._graph_json("GET", "/value") == {"value": 7}
    assert connector._graph_json("GET", "/empty") == {}

    monkeypatch.setenv("NEXON_RECON_SHAREPOINT_DRIVE_ID", "drive")
    calls = []

    def graph_json(method: str, path: str, body: dict | None = None) -> dict:
        calls.append((method, path, body))
        if path.endswith("/children"):
            return {"value": [{"id": "child"}]}
        return {"id": "item"}

    monkeypatch.setattr(connector, "_graph_json", graph_json)
    assert connector._drive_path("/folder with space/") == (
        "/drives/drive/root:/folder%20with%20space:"
    )
    assert connector._get_item("folder") == {"id": "item"}
    assert connector._children("folder") == [{"id": "child"}]

    assert connector._ensure_folder("///") == {}

    def get_item(path: str) -> dict:
        if path in {"new", "new/existing/newest"}:
            raise RuntimeError("not found")
        return {"id": f"found:{path}"}

    monkeypatch.setattr(connector, "_get_item", get_item)
    created = []

    def create(method: str, path: str, body: dict | None = None) -> dict:
        created.append((method, path, body))
        return {"id": f"created:{body['name']}"}

    monkeypatch.setattr(connector, "_graph_json", create)
    assert connector._ensure_folder("/new/existing/newest/") == {"id": "created:newest"}
    assert [call[2]["name"] for call in created] == ["new", "newest"]
    assert created[0][1] == "/drives/drive/root:/:/children"
    assert created[1][1] == "/drives/drive/root:/new/existing:/children"


def test_provider_paths_and_check_spaces(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    config: dict,
) -> None:
    upload_root = tmp_path / "upload"
    result_root = tmp_path / "result"
    monkeypatch.setattr(connector, "sharepoint_roots", lambda _config: (upload_root, result_root))
    monkeypatch.setattr(connector, "ensure_provider", Mock())

    assert connector._provider_paths(config, "AAPT") == (
        upload_root / "AAPT",
        result_root / "AAPT",
    )

    missing_output = tmp_path / "missing.json"
    missing_args = SimpleNamespace(provider="AAPT", mode="local", output=missing_output)
    assert connector.check_spaces(missing_args, config) == 2
    assert payload(missing_output)["status"] == "setup_incomplete"

    for provider in config["providers"]:
        (upload_root / provider).mkdir(parents=True)
        (result_root / provider).mkdir(parents=True)
    local_output = tmp_path / "local.json"
    local_args = SimpleNamespace(provider=None, mode="local", output=local_output)
    assert connector.check_spaces(local_args, config) == 0
    assert payload(local_output) == {"providers": ["AAPT", "Telstra"], "status": "ok"}

    checked = []
    monkeypatch.setattr(connector, "_get_item", lambda path: checked.append(path) or {"id": path})
    graph_output = tmp_path / "graph.json"
    graph_args = SimpleNamespace(provider=None, mode="graph", output=graph_output)
    assert connector.check_spaces(graph_args, config) == 0
    assert checked == [
        (upload_root / "AAPT").as_posix(),
        (result_root / "AAPT").as_posix(),
        (upload_root / "Telstra").as_posix(),
        (result_root / "Telstra").as_posix(),
    ]
    assert payload(graph_output)["checked"] == checked


def test_find_upload_local_variants(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    config: dict,
) -> None:
    upload_root = tmp_path / "upload"
    provider_root = upload_root / "AAPT"
    provider_root.mkdir(parents=True)
    (provider_root / "one.csv").write_text("one", encoding="utf-8")
    (provider_root / "two.csv").write_text("two", encoding="utf-8")
    (provider_root / "directory").mkdir()
    monkeypatch.setattr(connector, "sharepoint_roots", lambda _config: (upload_root, tmp_path / "result"))
    monkeypatch.setattr(connector, "ensure_provider", Mock())

    all_output = tmp_path / "all.json"
    all_args = SimpleNamespace(
        provider="AAPT", source_name=None, mode="local", output=all_output
    )
    assert connector.find_upload(all_args, config) == 2
    assert payload(all_output)["count"] == 2

    one_output = tmp_path / "one.json"
    one_args = SimpleNamespace(
        provider="AAPT", source_name="one.csv", mode="local", output=one_output
    )
    assert connector.find_upload(one_args, config) == 0
    assert payload(one_output)["matches"] == [str(provider_root / "one.csv")]

    none_output = tmp_path / "none.json"
    none_args = SimpleNamespace(
        provider="AAPT", source_name="absent.csv", mode="local", output=none_output
    )
    assert connector.find_upload(none_args, config) == 2
    assert payload(none_output)["count"] == 0


def test_find_upload_graph_variants(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    config: dict,
) -> None:
    monkeypatch.setattr(
        connector,
        "sharepoint_roots",
        lambda _config: (Path("/upload"), Path("/result")),
    )
    monkeypatch.setattr(connector, "ensure_provider", Mock())
    monkeypatch.setattr(
        connector,
        "_children",
        lambda _path: [
            {"name": "one.csv", "id": "1", "size": 10, "file": {}},
            {"name": "two.csv", "id": "2", "size": 20, "file": {}},
            {"name": "folder", "id": "3", "folder": {}},
        ],
    )

    all_output = tmp_path / "all-graph.json"
    all_args = SimpleNamespace(
        provider="AAPT", source_name=None, mode="graph", output=all_output
    )
    assert connector.find_upload(all_args, config) == 2
    assert payload(all_output)["count"] == 2

    one_output = tmp_path / "one-graph.json"
    one_args = SimpleNamespace(
        provider="AAPT", source_name="one.csv", mode="graph", output=one_output
    )
    assert connector.find_upload(one_args, config) == 0
    assert payload(one_output)["matches"] == [{"id": "1", "name": "one.csv", "size": 10}]

    none_output = tmp_path / "none-graph.json"
    none_args = SimpleNamespace(
        provider="AAPT", source_name="absent.csv", mode="graph", output=none_output
    )
    assert connector.find_upload(none_args, config) == 2


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

    monkeypatch.setattr(connector, "_get_item", lambda _path: {"id": "item-7"})
    monkeypatch.setattr(connector, "_drive_id", lambda: "drive-9")
    graph_request = Mock(return_value=b"graph invoice")
    monkeypatch.setattr(connector, "_graph_request", graph_request)
    graph_destination = tmp_path / "staging" / "graph.csv"
    graph_output = tmp_path / "graph-download.json"
    graph_args = SimpleNamespace(
        provider="AAPT",
        source_name=source.name,
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


@pytest.mark.parametrize(
    ("copy", "expected_status"),
    [(True, "copied"), (False, "moved")],
)
def test_move_upload_local(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    config: dict,
    copy: bool,
    expected_status: str,
) -> None:
    upload_root = tmp_path / f"upload-{copy}"
    provider_root = upload_root / "AAPT"
    provider_root.mkdir(parents=True)
    source = provider_root / "invoice.csv"
    source.write_bytes(b"invoice")
    monkeypatch.setattr(connector, "sharepoint_roots", lambda _config: (upload_root, tmp_path / "result"))
    monkeypatch.setattr(connector, "ensure_provider", Mock())
    output = tmp_path / f"local-move-{copy}.json"
    args = SimpleNamespace(
        provider="AAPT",
        source_name=source.name,
        run_root=str(tmp_path / f"run-{copy}"),
        copy=copy,
        mode="local",
        output=output,
    )

    assert connector.move_upload_to_run_source(args, config) == 0
    target = Path(args.run_root) / "source" / source.name
    assert target.read_bytes() == b"invoice"
    assert source.exists() is copy
    assert payload(output)["status"] == expected_status


@pytest.mark.parametrize(
    ("copy", "method", "suffix", "expected_status"),
    [
        (True, "POST", "/copy", "copy_started"),
        (False, "PATCH", "", "moved"),
    ],
)
def test_move_upload_graph(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    config: dict,
    copy: bool,
    method: str,
    suffix: str,
    expected_status: str,
) -> None:
    monkeypatch.setattr(
        connector,
        "sharepoint_roots",
        lambda _config: (Path("/upload"), Path("/result")),
    )
    monkeypatch.setattr(connector, "ensure_provider", Mock())
    monkeypatch.setattr(connector, "_get_item", lambda _path: {"id": "source-id"})
    monkeypatch.setattr(connector, "_ensure_folder", lambda _path: {"id": "parent-id"})
    monkeypatch.setattr(connector, "_drive_id", lambda: "drive-id")
    graph_json = Mock(return_value={"operation": method})
    monkeypatch.setattr(connector, "_graph_json", graph_json)
    output = tmp_path / f"graph-move-{copy}.json"
    args = SimpleNamespace(
        provider="AAPT",
        source_name="invoice.csv",
        run_root="/result/AAPT/run/",
        copy=copy,
        mode="graph",
        output=output,
    )

    assert connector.move_upload_to_run_source(args, config) == 0
    graph_json.assert_called_once_with(
        method,
        f"/drives/drive-id/items/source-id{suffix}",
        {"parentReference": {"id": "parent-id"}, "name": "invoice.csv"},
    )
    assert payload(output)["status"] == expected_status


def test_upload_artifact_local_and_graph(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "artifact.json"
    source.write_bytes(b'{"ok": true}')
    local_target = tmp_path / "published" / "artifact.json"
    local_output = tmp_path / "local-upload.json"
    local_args = SimpleNamespace(
        local_file=source,
        sharepoint_path=str(local_target),
        mode="local",
        output=local_output,
    )
    assert connector.upload_artifact(local_args, {}) == 0
    assert local_target.read_bytes() == source.read_bytes()

    monkeypatch.setattr(connector, "_drive_path", lambda path: f"/root:{path}:")
    graph_request = Mock(return_value=b"")
    monkeypatch.setattr(connector, "_graph_request", graph_request)
    graph_output = tmp_path / "graph-upload.json"
    graph_args = SimpleNamespace(
        local_file=source,
        sharepoint_path="/result/artifact.json",
        mode="graph",
        output=graph_output,
    )
    assert connector.upload_artifact(graph_args, {}) == 0
    graph_request.assert_called_once_with(
        "PUT",
        "/root:/result/artifact.json:/content",
        b'{"ok": true}',
        content_type="application/octet-stream",
    )
    assert payload(graph_output)["target"] == "/result/artifact.json"


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
    monkeypatch.setattr(connector, "check_spaces", command)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "sharepoint_connector.py",
            "--config",
            str(config_path),
            "--mode",
            "local",
            "check-spaces",
            "--provider",
            "AAPT",
            "--output",
            str(output),
        ],
    )

    assert connector.main() == 17
    load_config.assert_called_once_with(config_path)
    args, passed_config = command.call_args.args
    assert passed_config is loaded_config
    assert vars(args) == {
        "command": "check-spaces",
        "config": config_path,
        "mode": "local",
        "output": output,
        "provider": "AAPT",
    }


def test_module_entry_point(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.txt"
    source.write_text("artifact", encoding="utf-8")
    target = tmp_path / "published" / "target.txt"
    output = tmp_path / "entry-point.json"
    monkeypatch.setattr(common, "load_config", lambda _path: {})
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "sharepoint_connector.py",
            "--mode",
            "local",
            "upload-artifact",
            "--local-file",
            str(source),
            "--sharepoint-path",
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
    assert payload(output)["status"] == "uploaded"
