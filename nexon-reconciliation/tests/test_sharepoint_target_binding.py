from __future__ import annotations

import json
import hashlib
import runpy
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from recon_core import sharepoint_binding as binding_module  # noqa: E402
from recon_core import sharepoint_connector as connector  # noqa: E402
from recon_core import preflight_check  # noqa: E402
from recon_core import run_recon  # noqa: E402
from recon_core import sharepoint_target as target  # noqa: E402
from recon_core.common import sha256_file, write_json  # noqa: E402


def valid_binding(**changes: object) -> dict:
    value = {
        "contract_version": 1,
        "site_name": "Nexon Reconciliation Automation",
        "site_id": "tenant.sharepoint.com,site,web",
        "site_url": "https://tenant.sharepoint.com/sites/NexonReconciliationAutomation",
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
    value.update(changes)
    return value


def site_record(**changes: object) -> dict:
    value = {
        "displayName": "Nexon Reconciliation Automation",
        "id": "tenant.sharepoint.com,site,web",
        "webUrl": "https://tenant.sharepoint.com/sites/NexonReconciliationAutomation",
    }
    value.update(changes)
    return value


def test_binding_validation_and_loading(tmp_path: Path) -> None:
    normalized = binding_module.validate_binding(valid_binding())
    assert normalized["drive_id"] == "drive-id"
    assert normalized["site_url"].endswith("NexonReconciliationAutomation")
    without_leading_slash = valid_binding(
        site_path="sites/NexonReconciliationAutomation"
    )
    assert binding_module.validate_binding(without_leading_slash)["site_path"].startswith("/")

    path = tmp_path / "binding.json"
    path.write_text(json.dumps(valid_binding()), encoding="utf-8")
    assert binding_module.load_binding(path)["site_id"].endswith(",site,web")

    with pytest.raises(RuntimeError, match="cannot read"):
        binding_module.load_binding(tmp_path / "missing.json")
    path.write_text("{", encoding="utf-8")
    with pytest.raises(RuntimeError, match="cannot read"):
        binding_module.load_binding(path)


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ([], "must be an object"),
        (valid_binding(contract_version=2), "contract_version"),
        (valid_binding(site_name="Similar Site"), "site_name"),
        (valid_binding(site_path="/sites/Other"), "site_path"),
        (
            valid_binding(
                site_url="https://tenant.sharepoint.com/sites/Other"
            ),
            "site_url and site_path disagree",
        ),
        (valid_binding(hostname="other.sharepoint.com"), "hostname and site_url disagree"),
        (valid_binding(site_id=""), "site_id and drive_id"),
        (valid_binding(drive_id=""), "site_id and drive_id"),
        (
            valid_binding(
                drive_web_url="https://other.sharepoint.com/sites/"
                "NexonReconciliationAutomation/Documents"
            ),
            "default drive is outside",
        ),
        (
            valid_binding(
                drive_web_url="https://tenant.sharepoint.com/sites/Other/Documents"
            ),
            "default drive is outside",
        ),
        (
            valid_binding(
                drive_web_url=(
                    "https://tenant.sharepoint.com/sites/"
                    "NexonReconciliationAutomation/%2E%2E/Other"
                )
            ),
            "dot segments",
        ),
        (valid_binding(drive_name=""), "drive_name"),
        (valid_binding(discovery_sha256=""), "discovery_sha256"),
    ],
)
def test_binding_rejects_invalid_contract(value: object, message: str) -> None:
    with pytest.raises(RuntimeError, match=message):
        binding_module.validate_binding(value)


@pytest.mark.parametrize(
    "url",
    [
        "http://tenant.sharepoint.com/sites/NexonReconciliationAutomation",
        "https://example.com/sites/NexonReconciliationAutomation",
        "https://user@tenant.sharepoint.com/sites/NexonReconciliationAutomation",
        "https://tenant.sharepoint.com:444/sites/NexonReconciliationAutomation",
        "https://tenant.sharepoint.com/sites/NexonReconciliationAutomation?q=1",
        "https://tenant.sharepoint.com/sites/NexonReconciliationAutomation#x",
        "https://tenant.sharepoint.com:bad/sites/NexonReconciliationAutomation",
    ],
)
def test_binding_rejects_unsafe_urls(url: str) -> None:
    with pytest.raises(RuntimeError, match="site_url"):
        binding_module.validate_binding(valid_binding(site_url=url))


def test_site_record_shapes_and_selection() -> None:
    expected = target.select_site([site_record()])
    assert expected["hostname"] == "tenant.sharepoint.com"
    assert target.select_site({"value": [site_record()]}) == expected
    assert target.select_site({"sites": [site_record()]}) == expected
    assert target.select_site(site_record()) == expected
    assert target.select_site(
        {
            "items": [
                {
                    "display_name": "Nexon Reconciliation Automation",
                    "site_id": "tenant.sharepoint.com,site,web",
                    "web_url": (
                        "https://tenant.sharepoint.com/sites/"
                        "NexonReconciliationAutomation/"
                    ),
                }
            ]
        }
    )["hostname"] == "tenant.sharepoint.com"

    with pytest.raises(RuntimeError, match="multiple site-list containers"):
        target.select_site({"value": [], "sites": []})
    with pytest.raises(RuntimeError, match="expected a site list"):
        target.select_site({})
    with pytest.raises(RuntimeError, match="object or list"):
        target.select_site("bad")
    with pytest.raises(RuntimeError, match="every site result"):
        target.select_site([1])
    with pytest.raises(RuntimeError, match="site_not_found"):
        target.select_site([site_record(displayName="Wrong")])
    with pytest.raises(RuntimeError, match="site_not_found"):
        target.select_site([site_record(webUrl="https://tenant.sharepoint.com/sites/Wrong")])
    with pytest.raises(RuntimeError, match="site_match_ambiguous"):
        target.select_site(
            [
                site_record(),
                site_record(
                    id="tenant.sharepoint.com,site-2,web-2",
                    webUrl=(
                        "https://other.sharepoint.com/sites/"
                        "NexonReconciliationAutomation"
                    ),
                ),
            ]
        )
    with pytest.raises(RuntimeError, match="site_match_ambiguous"):
        target.select_site([site_record(), site_record()])

    aaic = target.select_site(
        [
            site_record(
                id="ignored-native-id",
                webUrl=(
                    "https://appliedaiconsulting0.sharepoint.com/sites/"
                    "NexonReconciliationAutomation"
                ),
            )
        ]
    )
    assert aaic["hostname"] == "appliedaiconsulting0.sharepoint.com"


def test_resolve_target_success_and_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    discovery = tmp_path / "sites.json"
    output = tmp_path / "binding.json"
    discovery.write_text(json.dumps({"value": [site_record()]}), encoding="utf-8")
    calls: list[tuple[str, Path | None]] = []
    monkeypatch.setattr(
        connector,
        "configure_runtime",
        lambda *, auth_mode, binding_path: calls.append((auth_mode, binding_path)),
    )

    graph_payloads = {
        "/sites/tenant.sharepoint.com:/sites/NexonReconciliationAutomation": site_record(),
        "/sites/tenant.sharepoint.com%2Csite%2Cweb/drive": {
            "id": "drive-id",
            "name": "Documents",
            "driveType": "documentLibrary",
            "webUrl": (
                "https://tenant.sharepoint.com/sites/"
                "NexonReconciliationAutomation/Shared%20Documents"
            ),
        },
    }
    monkeypatch.setattr(
        connector,
        "_graph_json",
        lambda method, path: graph_payloads[path],
    )
    resolved = target.resolve_target(
        discovery_path=discovery,
        output_path=output,
        auth_mode="auth_proxy",
    )
    assert calls == [("auth_proxy", None)]
    assert resolved == binding_module.load_binding(output)
    assert len(resolved["discovery_sha256"]) == 64

    discovery.write_bytes(b"\xff")
    with pytest.raises(RuntimeError, match="discovery_invalid"):
        target.resolve_target(
            discovery_path=discovery,
            output_path=output,
            auth_mode="auth_proxy",
        )
    discovery.write_text(json.dumps({"value": [site_record()]}), encoding="utf-8")

    monkeypatch.setattr(
        connector,
        "_graph_json",
        Mock(side_effect=RuntimeError("denied")),
    )
    with pytest.raises(RuntimeError, match="profile_site_mismatch"):
        target.resolve_target(
            discovery_path=discovery,
            output_path=output,
            auth_mode="auth_proxy",
        )

    variants = [
        (
            {
                **graph_payloads,
                "/sites/tenant.sharepoint.com:/sites/NexonReconciliationAutomation": {
                    "webUrl": site_record()["webUrl"]
                },
            },
            "profile_site_mismatch",
        ),
        (
            {
                **graph_payloads,
                "/sites/tenant.sharepoint.com:/sites/NexonReconciliationAutomation": site_record(
                    webUrl="https://tenant.sharepoint.com/sites/Other"
                ),
            },
            "different site identity",
        ),
        (
            {
                **graph_payloads,
                "/sites/tenant.sharepoint.com%2Csite%2Cweb/drive": {
                    **graph_payloads["/sites/tenant.sharepoint.com%2Csite%2Cweb/drive"],
                    "driveType": "other",
                },
            },
            "default_drive_not_found",
        ),
    ]
    for payloads, message in variants:
        monkeypatch.setattr(
            connector,
            "_graph_json",
            lambda method, path, payloads=payloads: payloads[path],
        )
        with pytest.raises(RuntimeError, match=message):
            target.resolve_target(
                discovery_path=discovery,
                output_path=output,
                auth_mode="auth_proxy",
            )


def test_connector_profile_mode_uses_binding_without_bearer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "binding.json"
    path.write_text(json.dumps(valid_binding()), encoding="utf-8")
    connector.configure_runtime(auth_mode="auth_proxy", binding_path=path)
    assert connector._drive_id() == "drive-id"
    requests = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return b"ok"

    monkeypatch.setattr(
        connector,
        "urlopen",
        lambda request, timeout: requests.append((request, timeout)) or Response(),
    )
    assert (
        connector._graph_request(
            "GET", f"/sites/{valid_binding()['site_id']}"
        )
        == b"ok"
    )
    assert requests[0][0].get_header("Authorization") is None

    with pytest.raises(RuntimeError, match="auth_mode_invalid"):
        connector.configure_runtime(auth_mode="other", binding_path=None)
    connector.configure_runtime(auth_mode="auth_proxy", binding_path=None)


def test_source_name_item_pin_and_path_guards(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for value in ("", "../invoice.zip", "folder/invoice.zip", r"folder\invoice.zip"):
        with pytest.raises(RuntimeError, match="source_name_invalid"):
            connector._safe_basename(value)
    with pytest.raises(RuntimeError, match="path_invalid"):
        connector._safe_drive_path("/folder/%2e%2e/outside")

    upload = tmp_path / "upload"
    (upload / "AAPT").mkdir(parents=True)
    monkeypatch.setattr(
        connector,
        "sharepoint_roots",
        lambda _config: (upload, tmp_path / "result"),
    )
    monkeypatch.setattr(connector, "ensure_provider", lambda *_args: None)
    monkeypatch.setattr(connector, "_get_item", lambda _path: {"id": "changed"})
    args = type(
        "Args",
        (),
        {
            "provider": "AAPT",
            "source_name": "invoice.zip",
            "source_item_id": "selected",
            "destination": tmp_path / "staged.zip",
            "mode": "graph",
            "output": tmp_path / "receipt.json",
        },
    )()
    with pytest.raises(RuntimeError, match="source_identity_changed"):
        connector.download_upload(args, {"providers": {"AAPT": {}}})


def test_download_receipt_and_live_publication_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binding_path = tmp_path / "binding.json"
    binding_path.write_text(json.dumps(valid_binding()), encoding="utf-8")
    source = tmp_path / "invoice.zip"
    source.write_bytes(b"invoice-bytes")
    source_url = (
        "https://tenant.sharepoint.com/sites/"
        "NexonReconciliationAutomation/Shared%20Documents/"
        "recon-upload-space/AAPT/invoice.zip"
    )
    receipt = {
        "status": "downloaded",
        "provider": "AAPT",
        "source_name": source.name,
        "destination": str(source.resolve()),
        "source_item_id": "item-1",
        "source_web_url": source_url,
        "site_id": valid_binding()["site_id"],
        "drive_id": valid_binding()["drive_id"],
        "binding_sha256": sha256_file(binding_path),
        "byte_count": source.stat().st_size,
        "downloaded_sha256": sha256_file(source),
    }
    receipt_path = tmp_path / "download.json"
    write_json(receipt_path, receipt)
    metadata = {
        "id": "item-1",
        "webUrl": source_url,
        "parentReference": {"driveId": "drive-id"},
    }
    monkeypatch.setattr(connector, "_graph_json", lambda *_args: metadata)
    monkeypatch.setattr(
        connector, "_graph_request", lambda *_args: source.read_bytes()
    )
    assert run_recon._verify_download_receipt(
        receipt_path=receipt_path,
        binding_path=binding_path,
        source_file=source,
        provider="AAPT",
        auth_mode="auth_proxy",
    )["source_item_id"] == "item-1"

    bad = dict(receipt)
    bad["provider"] = "Optus"
    write_json(receipt_path, bad)
    with pytest.raises(RuntimeError, match="provenance does not match"):
        run_recon._verify_download_receipt(
            receipt_path=receipt_path,
            binding_path=binding_path,
            source_file=source,
            provider="AAPT",
            auth_mode="auth_proxy",
        )
    write_json(receipt_path, receipt)

    monkeypatch.setattr(
        connector,
        "_graph_json",
        lambda *_args: {**metadata, "id": "different"},
    )
    with pytest.raises(RuntimeError, match="item identity/path"):
        run_recon._verify_download_receipt(
            receipt_path=receipt_path,
            binding_path=binding_path,
            source_file=source,
            provider="AAPT",
            auth_mode="auth_proxy",
        )

    monkeypatch.setattr(connector, "_graph_json", lambda *_args: metadata)
    monkeypatch.setattr(connector, "_graph_request", lambda *_args: b"different")
    with pytest.raises(RuntimeError, match="staged bytes"):
        run_recon._verify_download_receipt(
            receipt_path=receipt_path,
            binding_path=binding_path,
            source_file=source,
            provider="AAPT",
            auth_mode="auth_proxy",
        )

    published = {
        "item_id": "item-1",
        "sharepoint_url": source_url,
        "sha256": hashlib.sha256(b"published").hexdigest(),
    }
    monkeypatch.setattr(connector, "_graph_json", lambda *_args: metadata)
    monkeypatch.setattr(connector, "_graph_request", lambda *_args: b"published")
    run_recon._verify_published_item(
        item=published,
        expected_url=source_url,
        expected_sha256=published["sha256"],
        binding=valid_binding(),
    )
    monkeypatch.setattr(
        connector,
        "_graph_json",
        lambda *_args: {**metadata, "webUrl": "https://tenant.sharepoint.com/wrong"},
    )
    with pytest.raises(RuntimeError, match="identity/path"):
        run_recon._verify_published_item(
            item=published,
            expected_url=source_url,
            expected_sha256=published["sha256"],
            binding=valid_binding(),
        )
    monkeypatch.setattr(connector, "_graph_json", lambda *_args: metadata)
    monkeypatch.setattr(connector, "_graph_request", lambda *_args: b"wrong")
    with pytest.raises(RuntimeError, match="content checksum"):
        run_recon._verify_published_item(
            item=published,
            expected_url=source_url,
            expected_sha256=published["sha256"],
            binding=valid_binding(),
        )


def test_profile_preflight_and_graph_binding_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "binding.json"
    path.write_text(json.dumps(valid_binding()), encoding="utf-8")
    capabilities = preflight_check.capability_manifest(
        {"features": {}},
        local_check=False,
        sharepoint_auth_mode="auth_proxy",
        sharepoint_binding=path,
        profile_validated=True,
    )
    assert capabilities["capabilities"]["binary_source_staging"] is True

    monkeypatch.setattr(
        sys,
        "argv",
        [
                "sharepoint_connector.py",
                "--mode",
                "graph",
                "download-upload",
                "--provider",
                "AAPT",
                "--source-name",
                "invoice.zip",
                "--source-item-id",
                "item-1",
                "--destination",
                str(tmp_path / "invoice.zip"),
                "--output",
                str(tmp_path / "out.json"),
        ],
    )
    with pytest.raises(RuntimeError, match="binding_required"):
        connector.main()


def test_run_frozen_binding_identity_and_tamper_gate(tmp_path: Path) -> None:
    run_root = tmp_path / "AAPT" / "2026" / "07" / "aapt_20260723_120000_ABCDE"
    manifest = run_root / "manifest"
    manifest.mkdir(parents=True)
    with pytest.raises(RuntimeError, match="binding_missing"):
        run_recon._publication_target(run_root)

    source = tmp_path / "input-binding.json"
    source.write_text(json.dumps(valid_binding()), encoding="utf-8")
    frozen = run_recon._freeze_sharepoint_binding(run_root, source)
    frozen_path = manifest / "sharepoint_target_binding.json"
    write_json(
        manifest / "run_manifest.json",
        {"sharepoint_binding_sha256": sha256_file(frozen_path)},
    )
    assert frozen["site_id"].endswith(",site,web")
    assert run_recon._publication_target(run_root) == (
        "tenant.sharepoint.com",
        "/sites/NexonReconciliationAutomation/Shared Documents",
    )

    frozen_path.write_text(json.dumps({**frozen, "drive_name": "Changed"}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="binding_changed"):
        run_recon._publication_target(run_root)


def test_resolver_main_and_wrapper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    discovery = tmp_path / "sites.json"
    output = tmp_path / "binding.json"
    discovery.write_text(json.dumps({"value": [site_record()]}), encoding="utf-8")
    monkeypatch.setattr(
        target,
        "resolve_target",
        lambda **_kwargs: valid_binding(),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "resolve_sharepoint_target.py",
            "--sites-file",
            str(discovery),
            "--output",
            str(output),
        ],
    )
    assert target.main() == 0

    monkeypatch.setattr(target, "main", lambda: 7)
    with pytest.raises(SystemExit) as raised:
        runpy.run_path(str(SCRIPTS / "resolve_sharepoint_target.py"), run_name="__main__")
    assert raised.value.code == 7

    graph_payloads = {
        "/sites/tenant.sharepoint.com:/sites/NexonReconciliationAutomation": site_record(),
        "/sites/tenant.sharepoint.com%2Csite%2Cweb/drive": {
            "id": "drive-id",
            "name": "Documents",
            "driveType": "documentLibrary",
            "webUrl": valid_binding()["drive_web_url"],
        },
    }
    monkeypatch.setattr(
        connector,
        "_graph_json",
        lambda method, path: graph_payloads[path],
    )
    with pytest.warns(RuntimeWarning, match="found in sys.modules"):
        with pytest.raises(SystemExit) as raised:
            runpy.run_module("recon_core.sharepoint_target", run_name="__main__")
    assert raised.value.code == 0

    namespace = runpy.run_path(
        str(SCRIPTS / "resolve_sharepoint_target.py"),
        run_name="wrapper_import",
    )
    assert "main" in namespace
