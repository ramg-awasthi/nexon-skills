from __future__ import annotations

import base64
import copy
import hashlib
import io
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, padding, rsa


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from recon_core import fetch_intake_artifact as fetcher  # noqa: E402
from recon_core import intake_download_crypto as client_crypto  # noqa: E402
from recon_core.common import read_json, write_json  # noqa: E402


HOST = "nexon-recon-sharepoint-dev.netbird.aaic.cc"
ENVIRONMENT = "dev"
TICKET = "T" * 48


def _urlsafe(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def config() -> dict:
    return {
        "sharepoint_intake": {
            "environment": ENVIRONMENT,
            "gateway_host": HOST,
        },
        "limits": {"max_single_file_mb": 1},
    }


def material(root: Path, content: bytes = b"invoice") -> tuple[Path, dict, ed25519.Ed25519PrivateKey]:
    private_key_path = root / "download-private.pem"
    request_path = root / "download-request.json"
    request = client_crypto.create_key_request(private_key_path, request_path)
    public_key = serialization.load_pem_public_key(
        request["recipient_public_key"].encode()
    )
    encrypted = public_key.encrypt(
        TICKET.encode(),
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=b"nexon-sharepoint-download-v1",
        ),
    )
    signing_key = ed25519.Ed25519PrivateKey.generate()
    signing_public = signing_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    prepared = {
        "schema_version": "1.0",
        "kind": "prepared_download",
        "result": {
            "status": "prepared",
            "environment": ENVIRONMENT,
            "provider": "AAPT",
            "space": "reference",
            "source_name": "invoice.zip",
            "expected_size": len(content),
            "expected_sha256": hashlib.sha256(content).hexdigest(),
            "expires_at": (
                datetime.now(timezone.utc) + timedelta(minutes=2)
            ).isoformat(),
            "download_endpoint": f"https://{HOST}/download",
            "encrypted_ticket": _urlsafe(encrypted),
            "recipient_key_sha256": request["recipient_key_sha256"],
            "attestation_public_key": _urlsafe(signing_public),
            "index": {
                "index_id": "index-1",
                "index_sha256": "a" * 64,
                "relative_path": "AAPT/invoice.zip",
            },
        },
    }
    return private_key_path, prepared, signing_key


def signed_headers(
    prepared: dict,
    signing_key: ed25519.Ed25519PrivateKey,
) -> dict[str, str]:
    result = prepared["result"]
    payload = {
        "contract_version": 1,
        "environment": result["environment"],
        "provider": result["provider"],
        "space": result["space"],
        "source_name": result["source_name"],
        "relative_path": result["index"]["relative_path"],
        "byte_count": result["expected_size"],
        "sha256": result["expected_sha256"],
        "index_sha256": result["index"]["index_sha256"],
        "ticket_sha256": hashlib.sha256(TICKET.encode()).hexdigest(),
        "served_at": datetime.now(timezone.utc).isoformat(),
    }
    encoded = _urlsafe(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    )
    return {
        "X-Content-SHA256": result["expected_sha256"],
        "X-Recon-Attestation": encoded,
        "X-Recon-Attestation-Signature": _urlsafe(
            signing_key.sign(encoded.encode())
        ),
    }


class FakeResponse:
    def __init__(
        self,
        content: bytes,
        headers: dict[str, str],
        *,
        status: int = 200,
        content_length: object | None = None,
        endpoint: str | None = None,
    ) -> None:
        self.status = status
        self.headers = dict(headers)
        self.headers["Content-Length"] = (
            str(len(content)) if content_length is None else content_length
        )
        self._content = io.BytesIO(content)
        self._endpoint = endpoint or f"https://{HOST}/download"

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        return self._content.read(size)

    def geturl(self) -> str:
        return self._endpoint


class FakeOpener:
    def __init__(self, response: FakeResponse | BaseException) -> None:
        self.response = response
        self.request = None
        self.timeout = None

    def open(self, request, timeout):
        self.request = request
        self.timeout = timeout
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response


class FetchIntakeArtifactTests(unittest.TestCase):
    def _write(self, root: Path, payload: object, name: str = "preparation.json") -> Path:
        path = root / name
        write_json(path, payload)
        return path

    def test_success_uses_post_and_signed_sanitized_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            content = b"binary-invoice"
            private_key, prepared, signing_key = material(root, content)
            preparation_path = self._write(root, prepared)
            response = FakeResponse(
                content,
                signed_headers(prepared, signing_key),
            )
            opener = FakeOpener(response)
            destination = root / "staged" / "invoice.zip"
            output = root / "receipt.json"
            with patch.object(fetcher, "build_opener", return_value=opener):
                receipt = fetcher.fetch_artifact(
                    preparation_path=preparation_path,
                    private_key_path=private_key,
                    destination=destination,
                    config=config(),
                    output_path=output,
                )
            self.assertFalse(preparation_path.exists())
            self.assertFalse(private_key.exists())
            self.assertEqual(content, destination.read_bytes())
            self.assertEqual(receipt, read_json(output))
            self.assertEqual("POST", opener.request.get_method())
            self.assertEqual(b"", opener.request.data)
            self.assertEqual(
                TICKET, opener.request.get_header("X-recon-download-ticket")
            )
            self.assertEqual(60, opener.timeout)
            self.assertNotIn("encrypted_ticket", str(receipt))
            fetcher.verify_receipt_attestation(receipt)
            with self.assertRaisesRegex(RuntimeError, "key does not match"):
                fetcher.verify_receipt_attestation(
                    receipt, expected_public_key="wrong"
                )

    def test_contract_and_endpoint_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, base, _ = material(root)
            cases = [
                ([], "prepared_download envelope"),
                ({**base, "extra": True}, "prepared_download envelope"),
                ({**base, "schema_version": "2.0"}, "prepared_download envelope"),
                ({**base, "kind": "other"}, "prepared_download envelope"),
                ({**base, "result": []}, "result fields"),
                ({**base, "result": {**base["result"], "extra": True}}, "result fields"),
                ({**base, "result": {**base["result"], "status": "used"}}, "status=prepared"),
                ({**base, "result": {**base["result"], "environment": "prod"}}, "provider, space"),
                ({**base, "result": {**base["result"], "expected_size": "bad"}}, "integer"),
                ({**base, "result": {**base["result"], "provider": "bad"}}, "provider, space"),
                ({**base, "result": {**base["result"], "space": "bad"}}, "provider, space"),
                ({**base, "result": {**base["result"], "source_name": "../x"}}, "provider, space"),
                ({**base, "result": {**base["result"], "expected_size": 0}}, "provider, space"),
                ({**base, "result": {**base["result"], "expected_size": 2**21}}, "provider, space"),
                ({**base, "result": {**base["result"], "expected_sha256": "bad"}}, "provider, space"),
                ({**base, "result": {**base["result"], "expires_at": "bad"}}, "ISO-8601"),
                ({**base, "result": {**base["result"], "expires_at": "2026-01-01"}}, "timezone"),
                (
                    {
                        **base,
                        "result": {
                            **base["result"],
                            "expires_at": (
                                datetime.now(timezone.utc) - timedelta(seconds=1)
                            ).isoformat(),
                        },
                    },
                    "expired",
                ),
                (
                    {**base, "result": {**base["result"], "download_endpoint": "https://bad:xx/download"}},
                    "invalid port",
                ),
                (
                    {**base, "result": {**base["result"], "download_endpoint": "http://bad/download"}},
                    "outside the approved",
                ),
                (
                    {**base, "result": {**base["result"], "download_endpoint": f"https://{HOST}/other"}},
                    "outside the approved",
                ),
                ({**base, "result": {**base["result"], "encrypted_ticket": "short"}}, "encrypted"),
                ({**base, "result": {**base["result"], "recipient_key_sha256": "bad"}}, "cryptographic"),
                ({**base, "result": {**base["result"], "attestation_public_key": ""}}, "cryptographic"),
                ({**base, "result": {**base["result"], "index": []}}, "index must"),
                (
                    {**base, "result": {**base["result"], "index": {**base["result"]["index"], "extra": 1}}},
                    "index must",
                ),
                (
                    {**base, "result": {**base["result"], "index": {**base["result"]["index"], "relative_path": "../x"}}},
                    "index identity",
                ),
            ]
            for number, (payload, message) in enumerate(cases):
                path = self._write(root, payload, f"bad-{number}.json")
                with self.subTest(number=number), self.assertRaisesRegex(
                    RuntimeError, message
                ):
                    fetcher.load_preparation(path, config())
            with self.assertRaisesRegex(RuntimeError, "must be a mapping"):
                fetcher._sharepoint_environment({"sharepoint_intake": []})
            with self.assertRaisesRegex(RuntimeError, "identify one deployment"):
                fetcher._sharepoint_environment({"sharepoint_intake": {}})

    def test_failure_cleanup_redirect_and_attestation_guards(self) -> None:
        content = b"invoice"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cases = [
                (HTTPError(f"https://{HOST}/download", 302, "redirect", {}, io.BytesIO()), "HTTP 302", None),
                (URLError("down"), "could not be reached", None),
                (TimeoutError(), "could not be reached", None),
                (OSError(), "could not be reached", None),
                ("response", "non-success", lambda r: setattr(r, "status", 500)),
                ("response", "outside the approved", lambda r: setattr(r, "_endpoint", "https://bad/download")),
                ("response", "checksum header", lambda r: r.headers.update({"X-Content-SHA256": "b" * 64})),
                ("response", "signature", lambda r: r.headers.update({"X-Recon-Attestation-Signature": "bad"})),
                ("response", "Content-Length", lambda r: r.headers.update({"Content-Length": "bad"})),
                ("response", "does not match preparation", lambda r: r.headers.update({"Content-Length": "8"})),
                ("long", "exceeded", None),
                ("short", "byte count", None),
                ("changed", "checksum does not match", None),
            ]
            for number, (kind, message, mutate) in enumerate(cases):
                private_key, prepared, signing_key = material(root / str(number), content)
                preparation_path = self._write(root / str(number), prepared)
                body = (
                    content + b"x"
                    if kind == "long"
                    else content[:-1]
                    if kind == "short"
                    else b"INVOICE"
                    if kind == "changed"
                    else content
                )
                response: FakeResponse | BaseException
                if isinstance(kind, BaseException):
                    response = kind
                else:
                    response = FakeResponse(
                        body,
                        signed_headers(prepared, signing_key),
                        content_length=len(content),
                    )
                    if mutate:
                        mutate(response)
                destination = root / str(number) / "staged" / "invoice.zip"
                with self.subTest(number=number), patch.object(
                    fetcher, "build_opener", return_value=FakeOpener(response)
                ), self.assertRaisesRegex(RuntimeError, message):
                    fetcher.fetch_artifact(
                        preparation_path=preparation_path,
                        private_key_path=private_key,
                        destination=destination,
                        config=config(),
                    )
                self.assertFalse(preparation_path.exists())
                self.assertFalse(private_key.exists())
                self.assertFalse(destination.exists())
            self.assertIsNone(
                fetcher._NoRedirect().redirect_request(
                    None, None, 302, "redirect", {}, "https://bad"
                )
            )

    def test_key_and_destination_disposal_cli(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            private_key, prepared, _ = material(root)
            preparation_path = self._write(root, prepared)
            with self.assertRaisesRegex(RuntimeError, "destination filename"):
                fetcher.fetch_artifact(
                    preparation_path=preparation_path,
                    private_key_path=private_key,
                    destination=root / "wrong.zip",
                    config=config(),
                )
            self.assertFalse(private_key.exists())

            with self.assertRaisesRegex(RuntimeError, "paths must be new"):
                client_crypto.create_key_request(root / "exists", root / "exists")

            bad_key = root / "bad.pem"
            bad_key.write_text("bad")
            with self.assertRaisesRegex(RuntimeError, "could not be loaded"):
                client_crypto.decrypt_ticket(bad_key, "bad")
            bad_key.unlink()

            key_out = root / "cli-private.pem"
            request_out = root / "cli-request.json"
            with patch.object(
                sys,
                "argv",
                [
                    "create_intake_download_key.py",
                    "--private-key",
                    str(key_out),
                    "--output",
                    str(request_out),
                ],
            ):
                self.assertEqual(0, client_crypto.main())
            self.assertTrue(key_out.is_file())
            self.assertEqual(1, read_json(request_out)["contract_version"])

    def test_attestation_contract_and_receipt_guards(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            content = b"invoice"
            private_key, prepared, signing_key = material(root, content)
            headers = signed_headers(prepared, signing_key)
            payload = json.loads(
                client_crypto.decode_urlsafe(headers["X-Recon-Attestation"])
            )

            def signed(changes: dict, *, remove: str | None = None) -> tuple[str, str]:
                changed = {**payload, **changes}
                if remove is not None:
                    changed.pop(remove)
                encoded = _urlsafe(
                    json.dumps(
                        changed,
                        separators=(",", ":"),
                        sort_keys=True,
                    ).encode()
                )
                return encoded, _urlsafe(signing_key.sign(encoded.encode()))

            encoded, signature = signed({"extra": True})
            with self.assertRaisesRegex(RuntimeError, "fields do not match"):
                fetcher._verify_attestation(
                    encoded_payload=encoded,
                    encoded_signature=signature,
                    public_key=prepared["result"]["attestation_public_key"],
                    preparation=prepared["result"],
                    ticket=TICKET,
                )
            encoded, signature = signed({"provider": "Optus"})
            with self.assertRaisesRegex(RuntimeError, "identity does not match"):
                fetcher._verify_attestation(
                    encoded_payload=encoded,
                    encoded_signature=signature,
                    public_key=prepared["result"]["attestation_public_key"],
                    preparation=prepared["result"],
                    ticket=TICKET,
                )

            preparation_path = self._write(root, prepared)
            destination = root / "staged" / "invoice.zip"
            with patch.object(
                fetcher,
                "build_opener",
                return_value=FakeOpener(
                    FakeResponse(content, headers)
                ),
            ):
                receipt = fetcher.fetch_artifact(
                    preparation_path=preparation_path,
                    private_key_path=private_key,
                    destination=destination,
                    config=config(),
                )

            missing = copy.deepcopy(receipt)
            missing["attestation"] = {}
            with self.assertRaisesRegex(RuntimeError, "attestation is missing"):
                fetcher.verify_receipt_attestation(missing)

            invalid_signature = copy.deepcopy(receipt)
            invalid_signature["attestation"]["signature"] = "bad"
            with self.assertRaisesRegex(RuntimeError, "attestation is invalid"):
                fetcher.verify_receipt_attestation(invalid_signature)

            bad_fields = copy.deepcopy(receipt)
            encoded, signature = signed({"extra": True})
            bad_fields["attestation"]["payload"] = encoded
            bad_fields["attestation"]["signature"] = signature
            with self.assertRaisesRegex(RuntimeError, "fields do not match"):
                fetcher.verify_receipt_attestation(bad_fields)

            mismatch = copy.deepcopy(receipt)
            encoded, signature = signed({"provider": "Optus"})
            mismatch["attestation"]["payload"] = encoded
            mismatch["attestation"]["signature"] = signature
            with self.assertRaisesRegex(RuntimeError, "does not match receipt"):
                fetcher.verify_receipt_attestation(mismatch)

            bad_ticket = copy.deepcopy(receipt)
            encoded, signature = signed({"ticket_sha256": "bad"})
            bad_ticket["attestation"]["payload"] = encoded
            bad_ticket["attestation"]["signature"] = signature
            with self.assertRaisesRegex(RuntimeError, "ticket identity"):
                fetcher.verify_receipt_attestation(bad_ticket)

    def test_sensitive_material_guards_and_fetch_cli(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            content = b"invoice"

            private_key, prepared, signing_key = material(root / "chmod", content)
            preparation_path = self._write(root / "chmod", prepared)
            with (
                patch.object(Path, "chmod", side_effect=OSError),
                patch.object(
                    fetcher,
                    "build_opener",
                    return_value=FakeOpener(
                        FakeResponse(
                            content,
                            signed_headers(prepared, signing_key),
                        )
                    ),
                ),
            ):
                fetcher.fetch_artifact(
                    preparation_path=preparation_path,
                    private_key_path=private_key,
                    destination=root / "chmod" / "out" / "invoice.zip",
                    config=config(),
                )

            private_key, prepared, _ = material(root / "fingerprint", content)
            prepared["result"]["recipient_key_sha256"] = "f" * 64
            preparation_path = self._write(root / "fingerprint", prepared)
            with self.assertRaisesRegex(RuntimeError, "fingerprint"):
                fetcher.fetch_artifact(
                    preparation_path=preparation_path,
                    private_key_path=private_key,
                    destination=root / "fingerprint" / "invoice.zip",
                    config=config(),
                )

            private_key, prepared, _ = material(root / "dispose", content)
            preparation_path = self._write(root / "dispose", prepared)
            original_unlink = Path.unlink

            def fail_preparation(path: Path, *args, **kwargs):
                if path == preparation_path:
                    raise OSError("locked")
                return original_unlink(path, *args, **kwargs)

            with (
                patch.object(Path, "unlink", fail_preparation),
                self.assertRaisesRegex(RuntimeError, "disposal_failed"),
            ):
                fetcher.fetch_artifact(
                    preparation_path=preparation_path,
                    private_key_path=private_key,
                    destination=root / "dispose" / "invoice.zip",
                    config=config(),
                )

            wrong_key = rsa.generate_private_key(
                public_exponent=65537,
                key_size=2048,
            )
            wrong_key_path = root / "wrong-size.pem"
            wrong_key_path.write_bytes(
                wrong_key.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                )
            )
            with self.assertRaisesRegex(RuntimeError, "3072-bit"):
                client_crypto.decrypt_ticket(wrong_key_path, "bad")

            private_key, _, _ = material(root / "decrypt", content)
            with self.assertRaisesRegex(RuntimeError, "decryption_failed"):
                client_crypto.decrypt_ticket(private_key, "bad")

            with (
                patch.object(
                    fetcher,
                    "fetch_artifact",
                    return_value={
                        "status": "downloaded",
                        "provider": "AAPT",
                        "space": "reference",
                        "source_name": "invoice.zip",
                        "byte_count": 7,
                        "sha256": "a" * 64,
                    },
                ),
                patch.object(
                    fetcher,
                    "load_config",
                    return_value=config(),
                ),
                patch.object(
                    sys,
                    "argv",
                    [
                        "fetch_intake_artifact.py",
                        "--config",
                        str(root / "config.yaml"),
                        "--preparation",
                        str(root / "prepared.json"),
                        "--private-key",
                        str(root / "private.pem"),
                        "--destination",
                        str(root / "invoice.zip"),
                        "--output",
                        str(root / "receipt.json"),
                    ],
                ),
            ):
                self.assertEqual(0, fetcher.main())

            self.assertTrue(client_crypto._encode_urlsafe(b"value"))


if __name__ == "__main__":
    unittest.main()
