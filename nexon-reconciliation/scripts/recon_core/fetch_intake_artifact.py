from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric import ed25519

from .common import (
    DEFAULT_CONFIG_PATH,
    PROVIDERS,
    load_config,
    positive_limit,
    read_json,
    sha256_file,
    write_json,
)
from .intake_download_crypto import decode_urlsafe, decrypt_ticket


PREPARATION_CONTRACT_VERSION = 1
DOWNLOAD_RECEIPT_CONTRACT_VERSION = 1
ALLOWED_SPACES = {"upload", "reference", "result"}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
PREPARATION_FIELDS = {
    "status",
    "environment",
    "provider",
    "space",
    "source_name",
    "expected_size",
    "expected_sha256",
    "expires_at",
    "download_endpoint",
    "encrypted_ticket",
    "recipient_key_sha256",
    "attestation_public_key",
    "index",
}
INDEX_FIELDS = {"index_id", "index_sha256", "relative_path"}
ATTESTATION_FIELDS = {
    "contract_version",
    "environment",
    "provider",
    "space",
    "source_name",
    "relative_path",
    "byte_count",
    "sha256",
    "index_sha256",
    "ticket_sha256",
    "served_at",
}


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _parse_expiry(value: object, *, now: datetime) -> str:
    raw = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RuntimeError(
            "intake_preparation_invalid: expires_at must be an ISO-8601 timestamp."
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise RuntimeError(
            "intake_preparation_invalid: expires_at must include a timezone."
        )
    if parsed <= now:
        raise RuntimeError("intake_preparation_expired: preparation receipt has expired.")
    return parsed.isoformat()


def _sharepoint_environment(config: dict[str, Any]) -> tuple[str, str]:
    intake = config.get("sharepoint_intake", {})
    if not isinstance(intake, dict):
        raise RuntimeError(
            "sharepoint_intake must be a mapping."
        )
    environment = str(intake.get("environment") or "").strip().lower()
    host = str(intake.get("gateway_host") or "").strip().lower().rstrip(".")
    if environment not in {"dev", "prod"} or not host:
        raise RuntimeError(
            "sharepoint_intake.environment and gateway_host must identify one deployment."
        )
    return environment, host


def _validate_download_endpoint(value: object, approved_host: str) -> str:
    raw = str(value or "").strip()
    parsed = urlparse(raw)
    try:
        port = parsed.port
    except ValueError as exc:
        raise RuntimeError(
            "intake_preparation_invalid: download endpoint has an invalid port."
        ) from exc
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if (
        parsed.scheme != "https"
        or hostname != approved_host
        or port not in (None, 443)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or parsed.query
        or parsed.path != "/download"
    ):
        raise RuntimeError(
            "intake_preparation_invalid: download endpoint is outside the approved HTTPS gateway."
        )
    return raw


def _validate_encrypted_ticket(value: object) -> str:
    ticket = str(value or "")
    if (
        len(ticket) < 300
        or len(ticket) > 2048
        or ticket.strip() != ticket
        or any(ord(character) < 33 or ord(character) > 126 for character in ticket)
    ):
        raise RuntimeError(
            "intake_preparation_invalid: encrypted download ticket is invalid."
        )
    return ticket


def _validate_index(value: object) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != INDEX_FIELDS:
        raise RuntimeError(
            "intake_preparation_invalid: index must contain only index_id, index_sha256, and relative_path."
        )
    index_id = str(value.get("index_id") or "").strip()
    index_sha256 = str(value.get("index_sha256") or "").strip().lower()
    relative_path = str(value.get("relative_path") or "").strip()
    relative = PurePosixPath(relative_path)
    if (
        not index_id
        or not SHA256_RE.fullmatch(index_sha256)
        or not relative_path
        or relative.is_absolute()
        or ".." in relative.parts
        or "\\" in relative_path
    ):
        raise RuntimeError("intake_preparation_invalid: index identity is invalid.")
    return {
        "index_id": index_id,
        "index_sha256": index_sha256,
        "relative_path": relative.as_posix(),
    }


def load_preparation(
    path: Path,
    config: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    envelope = read_json(path)
    if (
        not isinstance(envelope, dict)
        or set(envelope) != {"schema_version", "kind", "result"}
        or envelope.get("schema_version") != "1.0"
        or envelope.get("kind") != "prepared_download"
    ):
        raise RuntimeError(
            "intake_preparation_invalid: expected unchanged prepared_download envelope."
        )
    preparation = envelope.get("result")
    if not isinstance(preparation, dict) or set(preparation) != PREPARATION_FIELDS:
        raise RuntimeError(
            "intake_preparation_invalid: prepared_download result fields do not match the contract."
        )
    if (
        preparation.get("status") != "prepared"
    ):
        raise RuntimeError(
            "intake_preparation_invalid: expected status=prepared."
        )
    expected_environment, approved_host = _sharepoint_environment(config)
    environment = str(preparation.get("environment") or "").strip().lower()
    provider = str(preparation.get("provider") or "")
    space = str(preparation.get("space") or "").strip().lower()
    source_name = str(preparation.get("source_name") or "").strip()
    expected_sha256 = str(preparation.get("expected_sha256") or "").strip().lower()
    try:
        expected_size = int(preparation.get("expected_size"))
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            "intake_preparation_invalid: expected_size must be an integer."
        ) from exc
    max_bytes = (
        positive_limit(config, "max_single_file_mb", 250) * 1024 * 1024
    )
    if (
        environment != expected_environment
        or provider not in PROVIDERS
        or space not in ALLOWED_SPACES
        or not source_name
        or Path(source_name).name != source_name
        or source_name in {".", ".."}
        or expected_size <= 0
        or expected_size > max_bytes
        or not SHA256_RE.fullmatch(expected_sha256)
    ):
        raise RuntimeError(
            "intake_preparation_invalid: provider, space, file, size, or checksum is invalid."
        )
    recipient_key_sha256 = str(
        preparation.get("recipient_key_sha256") or ""
    ).strip().lower()
    attestation_public_key = str(
        preparation.get("attestation_public_key") or ""
    ).strip()
    if (
        not SHA256_RE.fullmatch(recipient_key_sha256)
        or len(attestation_public_key) < 40
        or len(attestation_public_key) > 128
    ):
        raise RuntimeError(
            "intake_preparation_invalid: cryptographic binding is incomplete."
        )
    current = now or datetime.now(timezone.utc)
    return {
        "status": "prepared",
        "environment": environment,
        "provider": provider,
        "space": space,
        "source_name": source_name,
        "expected_size": expected_size,
        "expected_sha256": expected_sha256,
        "expires_at": _parse_expiry(preparation.get("expires_at"), now=current),
        "download_endpoint": _validate_download_endpoint(
            preparation.get("download_endpoint"), approved_host
        ),
        "encrypted_ticket": _validate_encrypted_ticket(
            preparation.get("encrypted_ticket")
        ),
        "recipient_key_sha256": recipient_key_sha256,
        "attestation_public_key": attestation_public_key,
        "index": _validate_index(preparation.get("index")),
    }


def _verify_attestation(
    *,
    encoded_payload: str,
    encoded_signature: str,
    public_key: str,
    preparation: dict[str, Any],
    ticket: str,
) -> dict[str, Any]:
    try:
        key_bytes = decode_urlsafe(public_key)
        key = ed25519.Ed25519PublicKey.from_public_bytes(key_bytes)
        key.verify(
            decode_urlsafe(encoded_signature),
            encoded_payload.encode(),
        )
        payload = json.loads(decode_urlsafe(encoded_payload))
    except (ValueError, TypeError, json.JSONDecodeError, InvalidSignature) as exc:
        raise RuntimeError(
            "intake_attestation_invalid: gateway signature could not be verified."
        ) from exc
    if not isinstance(payload, dict) or set(payload) != ATTESTATION_FIELDS:
        raise RuntimeError(
            "intake_attestation_invalid: attestation fields do not match the contract."
        )
    expected = {
        "contract_version": 1,
        "environment": preparation["environment"],
        "provider": preparation["provider"],
        "space": preparation["space"],
        "source_name": preparation["source_name"],
        "relative_path": preparation["index"]["relative_path"],
        "byte_count": preparation["expected_size"],
        "sha256": preparation["expected_sha256"],
        "index_sha256": preparation["index"]["index_sha256"],
        "ticket_sha256": hashlib.sha256(ticket.encode()).hexdigest(),
    }
    for field, value in expected.items():
        if payload.get(field) != value:
            raise RuntimeError(
                "intake_attestation_invalid: signed download identity does not match preparation."
            )
    _parse_expiry(
        str(payload.get("served_at") or ""),
        now=datetime.fromtimestamp(0, tz=timezone.utc),
    )
    return payload


def verify_receipt_attestation(
    receipt: dict[str, Any],
    *,
    expected_public_key: str | None = None,
) -> dict[str, Any]:
    attestation = receipt.get("attestation")
    if (
        not isinstance(attestation, dict)
        or set(attestation)
        != {"public_key", "payload", "signature", "served_at"}
    ):
        raise RuntimeError(
            "download_receipt_invalid: signed attestation is missing."
        )
    public_key = str(attestation.get("public_key") or "")
    if expected_public_key is not None and public_key != expected_public_key:
        raise RuntimeError(
            "download_receipt_invalid: attestation key does not match MCP capability."
        )
    try:
        key = ed25519.Ed25519PublicKey.from_public_bytes(
            decode_urlsafe(public_key)
        )
        encoded_payload = str(attestation.get("payload") or "")
        key.verify(
            decode_urlsafe(str(attestation.get("signature") or "")),
            encoded_payload.encode(),
        )
        payload = json.loads(decode_urlsafe(encoded_payload))
    except (ValueError, TypeError, json.JSONDecodeError, InvalidSignature) as exc:
        raise RuntimeError(
            "download_receipt_invalid: signed attestation is invalid."
        ) from exc
    if not isinstance(payload, dict) or set(payload) != ATTESTATION_FIELDS:
        raise RuntimeError(
            "download_receipt_invalid: attestation fields do not match the contract."
        )
    expected = {
        "contract_version": 1,
        "environment": receipt.get("environment"),
        "provider": receipt.get("provider"),
        "space": receipt.get("space"),
        "source_name": receipt.get("source_name"),
        "relative_path": (receipt.get("index") or {}).get("relative_path"),
        "byte_count": receipt.get("byte_count"),
        "sha256": receipt.get("sha256"),
        "index_sha256": (receipt.get("index") or {}).get("index_sha256"),
        "served_at": attestation.get("served_at"),
    }
    if any(payload.get(field) != value for field, value in expected.items()):
        raise RuntimeError(
            "download_receipt_invalid: signed attestation does not match receipt."
        )
    if not SHA256_RE.fullmatch(str(payload.get("ticket_sha256") or "")):
        raise RuntimeError(
            "download_receipt_invalid: signed ticket identity is invalid."
        )
    return payload


def fetch_artifact(
    *,
    preparation_path: Path,
    private_key_path: Path,
    destination: Path,
    config: dict[str, Any],
    output_path: Path | None = None,
) -> dict[str, Any]:
    preparation_identity = ""
    ticket = ""
    try:
        try:
            preparation_path.chmod(0o600)
        except OSError:
            pass
        preparation_identity = sha256_file(preparation_path)
        preparation = load_preparation(preparation_path, config)
        ticket, recipient_key_sha256 = decrypt_ticket(
            private_key_path, preparation["encrypted_ticket"]
        )
        if recipient_key_sha256 != preparation["recipient_key_sha256"]:
            raise RuntimeError(
                "intake_private_key_invalid: key fingerprint does not match preparation."
            )
    finally:
        disposal_errors = []
        for sensitive_path in (preparation_path, private_key_path):
            try:
                sensitive_path.unlink(missing_ok=True)
            except OSError:
                disposal_errors.append(str(sensitive_path))
        if disposal_errors:
            raise RuntimeError(
                "intake_preparation_disposal_failed: sensitive download material could not be removed."
            )
    if destination.name != preparation["source_name"]:
        raise RuntimeError(
            "intake_destination_invalid: destination filename must match source_name."
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    digest = hashlib.sha256()
    byte_count = 0
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.",
            suffix=".partial",
            dir=destination.parent,
        )
        temporary_path = Path(temporary_name)
        request = Request(
            preparation["download_endpoint"],
            data=b"",
            headers={
                "Accept": "application/octet-stream",
                "X-Recon-Download-Ticket": ticket,
            },
            method="POST",
        )
        with os.fdopen(descriptor, "wb") as handle:
            try:
                response = build_opener(_NoRedirect()).open(request, timeout=60)
            except HTTPError as exc:
                raise RuntimeError(
                    f"intake_download_failed: gateway returned HTTP {exc.code}."
                ) from exc
            except (URLError, TimeoutError, OSError) as exc:
                raise RuntimeError(
                    "intake_download_failed: approved gateway could not be reached."
                ) from exc
            with response:
                if getattr(response, "status", 200) != 200:
                    raise RuntimeError(
                        "intake_download_failed: gateway returned a non-success response."
                    )
                _validate_download_endpoint(
                    response.geturl(), _sharepoint_environment(config)[1]
                )
                content_sha256 = str(
                    response.headers.get("X-Content-SHA256") or ""
                ).strip().lower()
                if content_sha256 != preparation["expected_sha256"]:
                    raise RuntimeError(
                        "intake_download_failed: checksum header does not match preparation."
                    )
                attestation_payload = str(
                    response.headers.get("X-Recon-Attestation") or ""
                ).strip()
                attestation_signature = str(
                    response.headers.get("X-Recon-Attestation-Signature") or ""
                ).strip()
                verified_attestation = _verify_attestation(
                    encoded_payload=attestation_payload,
                    encoded_signature=attestation_signature,
                    public_key=preparation["attestation_public_key"],
                    preparation=preparation,
                    ticket=ticket,
                )
                content_length = response.headers.get("Content-Length")
                try:
                    declared_size = int(content_length)
                except (TypeError, ValueError) as exc:
                    raise RuntimeError(
                        "intake_download_failed: valid Content-Length is required."
                    ) from exc
                if declared_size != preparation["expected_size"]:
                    raise RuntimeError(
                        "intake_download_failed: Content-Length does not match preparation."
                    )
                for chunk in iter(lambda: response.read(1024 * 1024), b""):
                    byte_count += len(chunk)
                    if byte_count > preparation["expected_size"]:
                        raise RuntimeError(
                            "intake_download_failed: response exceeded the prepared size."
                        )
                    handle.write(chunk)
                    digest.update(chunk)
            handle.flush()
            os.fsync(handle.fileno())
        if byte_count != preparation["expected_size"]:
            raise RuntimeError(
                "intake_download_failed: downloaded byte count does not match preparation."
            )
        downloaded_sha256 = digest.hexdigest()
        if downloaded_sha256 != preparation["expected_sha256"]:
            raise RuntimeError(
                "intake_download_failed: downloaded checksum does not match preparation."
            )
        os.replace(temporary_path, destination)
        temporary_path = None
    except Exception:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise

    receipt = {
        "contract_version": DOWNLOAD_RECEIPT_CONTRACT_VERSION,
        "status": "downloaded",
        "environment": preparation["environment"],
        "provider": preparation["provider"],
        "space": preparation["space"],
        "source_name": preparation["source_name"],
        "local_path": str(destination.resolve()),
        "byte_count": byte_count,
        "sha256": preparation["expected_sha256"],
        "index": preparation["index"],
        "preparation_receipt_sha256": preparation_identity,
        "downloaded_at": datetime.now(timezone.utc).isoformat(),
        "attestation": {
            "public_key": preparation["attestation_public_key"],
            "payload": attestation_payload,
            "signature": attestation_signature,
            "served_at": verified_attestation["served_at"],
        },
    }
    if output_path is not None:
        write_json(output_path, receipt)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fetch one MCP-prepared reconciliation artifact."
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--preparation", type=Path, required=True)
    parser.add_argument("--private-key", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    receipt = fetch_artifact(
        preparation_path=args.preparation.resolve(),
        private_key_path=args.private_key.resolve(),
        destination=args.destination.resolve(),
        config=load_config(args.config.resolve()),
        output_path=args.output.resolve(),
    )
    print(
        {
            "status": receipt["status"],
            "provider": receipt["provider"],
            "space": receipt["space"],
            "source_name": receipt["source_name"],
            "byte_count": receipt["byte_count"],
            "sha256": receipt["sha256"],
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
