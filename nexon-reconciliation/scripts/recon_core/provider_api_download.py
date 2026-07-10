from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from .common import DEFAULT_CONFIG_PATH, ensure_provider, load_config, sha256_file, write_json


def _env(*names: str) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return None


def _http_json(url: str, payload: dict, headers: dict[str, str] | None = None) -> dict:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"content-type": "application/json", **(headers or {})},
        method="POST",
    )
    try:
        with urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Provider API auth failed with HTTP {exc.code}: {body[:500]}") from exc


def _http_download(url: str, token: str, destination: Path) -> None:
    request = Request(url, headers={"authorization": f"Bearer {token}"}, method="GET")
    try:
        with urlopen(request, timeout=180) as response:
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("wb") as handle:
                handle.write(response.read())
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Provider API download failed with HTTP {exc.code}: {body[:500]}") from exc


def _equinix_download(args: argparse.Namespace, provider_config: dict) -> Path:
    if not args.account_id or not args.invoice_id:
        raise RuntimeError(
            "provider_api_listing_unavailable: Equinix API download currently requires --account-id and --invoice-id. "
            "Billing-period/date discovery is a separate adapter gate."
        )

    document_id = args.document_id or "DETAILED_PDF_EN"
    base_url = _env("NEXON_RECON_PROVIDER_API_BASE_URL_EQUINIX") or "https://api.equinix.com"
    token_url = _env("NEXON_RECON_PROVIDER_API_TOKEN_URL_EQUINIX") or f"{base_url}/oauth2/v1/token"
    client_id = _env(
        "NEXON_RECON_PROVIDER_API_CLIENT_ID_EQUINIX",
        "NEXON_RECON_PROVIDER_API_CLIENT_ID_SECRET_EQUINIX",
    )
    client_secret = _env(
        "NEXON_RECON_PROVIDER_API_CLIENT_SECRET_EQUINIX",
        "NEXON_RECON_PROVIDER_API_CLIENT_SECRET_NAME_EQUINIX",
        "NEXON_RECON_PROVIDER_API_TOKEN_SECRET_EQUINIX",
    )
    if not client_id or not client_secret:
        raise RuntimeError("provider_api_credentials_missing: Equinix client id/secret are not available in the runtime profile.")

    token_payload = _http_json(
        token_url,
        {"grant_type": "client_credentials", "client_id": client_id, "client_secret": client_secret},
    )
    access_token = token_payload.get("access_token")
    if not access_token:
        raise RuntimeError("provider_api_auth_failed: Equinix token response did not include access_token.")

    output_dir = args.output_dir or Path(".")
    output_name = args.output_name or f"equinix_{args.account_id}_{args.invoice_id}_{document_id}.pdf"
    destination = output_dir / output_name
    url = f"{base_url}/v1/finance/accounts/{args.account_id}/{args.invoice_id}?documentId={document_id}"
    _http_download(url, access_token, destination)
    if args.manifest:
        write_json(
            args.manifest,
            {
                "provider": "Equinix",
                "adapter": "equinix",
                "account_id": args.account_id,
                "invoice_id": args.invoice_id,
                "document_id": document_id,
                "output_file": str(destination),
                "output_sha256": sha256_file(destination),
            },
        )
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description="Provider API invoice download adapter.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--billing-period")
    parser.add_argument("--account-id")
    parser.add_argument("--invoice-id")
    parser.add_argument("--document-id")
    parser.add_argument("--output-name")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args()

    config = load_config(args.config)
    provider_config = ensure_provider(config, args.provider)
    if config.get("features", {}).get("provider_api_enabled") is not True or provider_config["provider_api_adapter_enabled"] is not True:
        raise RuntimeError("provider_api_not_available: Provider API intake is not enabled for this provider.")
    if args.provider == "Equinix":
        destination = _equinix_download(args, provider_config)
        print(destination)
        return 0
    raise NotImplementedError("integration_unavailable: Provider API download requires an approved provider adapter.")


if __name__ == "__main__":
    raise SystemExit(main())
