from __future__ import annotations

import argparse
import base64
import hashlib
import os
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from .common import write_json


def _encode_urlsafe(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def decode_urlsafe(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + ("=" * (-len(value) % 4)))


def create_key_request(private_key_path: Path, request_path: Path) -> dict[str, str]:
    if (
        private_key_path == request_path
        or private_key_path.exists()
        or request_path.exists()
    ):
        raise RuntimeError(
            "intake_key_destination_exists: key request paths must be new."
        )
    key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    private_bytes = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public_bytes = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    private_key_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(
        private_key_path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(private_bytes)
        handle.flush()
        os.fsync(handle.fileno())
    request = {
        "contract_version": 1,
        "recipient_public_key": public_bytes.decode(),
        "recipient_key_sha256": hashlib.sha256(
            key.public_key().public_bytes(
                serialization.Encoding.DER,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            )
        ).hexdigest(),
    }
    write_json(request_path, request)
    return request


def decrypt_ticket(private_key_path: Path, encrypted_ticket: str) -> tuple[str, str]:
    try:
        key = serialization.load_pem_private_key(
            private_key_path.read_bytes(),
            password=None,
        )
    except (OSError, ValueError, TypeError) as exc:
        raise RuntimeError("intake_private_key_invalid: private key could not be loaded.") from exc
    if not isinstance(key, rsa.RSAPrivateKey) or key.key_size != 3072:
        raise RuntimeError("intake_private_key_invalid: expected a 3072-bit RSA key.")
    fingerprint = hashlib.sha256(
        key.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    ).hexdigest()
    try:
        decrypted = key.decrypt(
            decode_urlsafe(encrypted_ticket),
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=b"nexon-sharepoint-download-v1",
            ),
        )
        ticket = decrypted.decode()
    except (ValueError, UnicodeDecodeError) as exc:
        raise RuntimeError(
            "intake_ticket_decryption_failed: preparation does not match the ephemeral key."
        ) from exc
    return ticket, fingerprint


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create one ephemeral key request for a SharePoint intake download."
    )
    parser.add_argument("--private-key", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    request = create_key_request(
        args.private_key.resolve(),
        args.output.resolve(),
    )
    print(
        {
            "status": "created",
            "contract_version": request["contract_version"],
            "recipient_key_sha256": request["recipient_key_sha256"],
            "request_path": str(args.output.resolve()),
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
