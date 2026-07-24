from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


PROVIDERS = {"AAPT", "Telstra", "Optus", "Vocus", "Megaport", "Equinix"}
PROVIDER_CONFIG_KEYS = {provider.lower(): provider for provider in PROVIDERS}
RUN_ID_RE = re.compile(r"^(AAPT|Telstra|Optus|Vocus|Megaport|Equinix)_\d{8}_\d{6}_[A-F0-9]{5}$")
DEFAULT_CONFIG_PATH = Path("config/recon_settings.yaml")
DEFAULT_SHAREPOINT_UPLOAD_ROOT = Path("/recon-upload-space")
DEFAULT_SHAREPOINT_REFERENCE_ROOT = Path("/recon-reference-space")
DEFAULT_SHAREPOINT_RESULT_ROOT = Path("/recon-result-space")

AGENT_MATCH_STATUS_VALUES = {
    "auto_matched",
    "needs_review",
    "multi_match",
    "no_match",
    "suggested_match",
    "excluded",
    "parser_warning",
}

RECON_MATCH_STATUS_VALUES = {
    "Matched",
    "Not Matched",
    "Supplier Only",
    "Billing System Only",
    "Dispute",
    "Manual Matched",
    "Billing Initiated",
    "Service Cancelled",
}

INVESTIGATOR_MATCH_STATUS_VALUES = {
    "suggested_match",
    "multi_match",
    "no_match",
    "needs_review",
    "excluded",
    "parser_warning",
}

HUMAN_VERIFIED_STATUS_VALUES = {
    "verified",
    "rejected",
    "deferred",
    "not_reviewed",
}


def logical_sharepoint_run_path(provider: str, run_root: Path) -> str:
    return (
        f"{DEFAULT_SHAREPOINT_RESULT_ROOT.as_posix()}/{provider}/"
        f"{run_root.parent.parent.name}/{run_root.parent.name}/{run_root.name}"
    )

RAW_WORKBOOK_COLUMNS = [
    "AccountPayableReconRequestId",
    "GenericSupplierInvoiceLineItemId",
    "ServiceProviderInvoiceNumber",
    "GenericNexonBillingId",
    "BillingDate",
    "SupplierName",
    "SupplierAccountNumber",
    "NexonInfrastructure",
    "BillingCustomerName",
    "InvoiceServiceNumber",
    "BillingServiceNumber",
    "BillingSystem",
    "InvoiceDetailDescription",
    "BillingServiceDescription",
    "InomialServiceSpecification",
    "InvoiceServiceType",
    "RecurringAmount",
    "Non-RecurringAmount",
    "Adjustment",
    "Discount",
    "Usage",
    "InvoiceAmountExclGST",
    "BillingAmountExclGST",
    "LastInvoiceDate",
    "Login",
    "InvoiceID",
    "InvoiceAmount",
    "ReconMatchStatus",
    "Dispute or Not",
    "ManualMatch",
    "ManualMatch-InvoiceNumber",
    "ManualMatch-Amount",
    "Customer Billing Initiated",
    "Service Cancellation Initiated",
    "Reason",
]


APPROVED_REFINED_COLUMNS = [
    "agent_match_status",
    "agent_match_rule",
    "agent_suggested_customer_account",
    "agent_suggested_subscription_id",
    "agent_suggested_invoice_number",
    "agent_suggested_service_id",
    "agent_evidence_summary",
    "agent_review_required",
    "human_verified_status",
    "human_verified_by",
    "human_verified_at",
    "human_verified_invoice_number",
]

EXCLUDED_PHASE1_COLUMNS = {
    "agent_confidence_score",
    "agent_reason_code",
    "agent_notes",
}

RUN_SUBDIRS = [
    "source",
    "extracted",
    "normalized",
    "raw-recon-report",
    "refined-recon-report",
    "evidence",
    "logs",
    "manifest",
]

RUN_STAGES = [
    "source_staging",
    "run_creation",
    "archive_validation",
    "provider_parsing",
    "supplier_persistence",
    "billing_preparation",
    "deterministic_comparison",
    "result_persistence",
    "raw_workbook",
    "exception_investigation",
    "refined_workbook",
    "publication",
    "validation",
    "notification",
]

STAGE_STATUS_VALUES = {"pending", "running", "completed", "failed", "skipped"}
RUN_STATUS_VALUES = {"created", "running", "completed", "failed"}

EVIDENCE_SUMMARY_AUTO_MATCHED_MODES = {"short", "blank"}
DEFAULT_EVIDENCE_SUMMARY_MAX_CHARS = 160


def load_config(path: Path) -> dict[str, Any]:
    try:
        import yaml  # type: ignore
    except ImportError as exc:
        raise RuntimeError("PyYAML is required to read config/recon_settings.yaml.") from exc

    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Config must be a mapping: {path}")
    return data


def evidence_summary_policy(config: dict[str, Any]) -> dict[str, Any]:
    reports = config.get("reports", {})
    if not isinstance(reports, dict):
        reports = {}
    evidence = reports.get("evidence_summary", {})
    if not isinstance(evidence, dict):
        evidence = {}

    auto_matched = str(evidence.get("auto_matched", "short")).strip().lower()
    if auto_matched not in EVIDENCE_SUMMARY_AUTO_MATCHED_MODES:
        raise ValueError(
            "reports.evidence_summary.auto_matched must be one of: "
            f"{sorted(EVIDENCE_SUMMARY_AUTO_MATCHED_MODES)}"
        )

    try:
        max_chars = int(evidence.get("max_chars", DEFAULT_EVIDENCE_SUMMARY_MAX_CHARS))
    except (TypeError, ValueError) as exc:
        raise ValueError("reports.evidence_summary.max_chars must be an integer.") from exc
    if max_chars < 40 or max_chars > 240:
        raise ValueError("reports.evidence_summary.max_chars must stay between 40 and 240.")

    return {"auto_matched": auto_matched, "max_chars": max_chars}


def require_audit(config: dict[str, Any]) -> None:
    billing = config.get("billing", {})
    if not isinstance(billing, dict) or billing.get("audit_required") is not True:
        raise RuntimeError("billing.audit_required must be true.")


def positive_limit(config: dict[str, Any], key: str, default: int) -> int:
    limits = config.get("limits", {})
    if not isinstance(limits, dict):
        limits = {}
    try:
        value = int(limits.get(key, default))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"limits.{key} must be an integer.") from exc
    if value <= 0:
        raise ValueError(f"limits.{key} must be greater than zero.")
    return value


def normalize_evidence_summary(summary: str, max_chars: int) -> str:
    text = " ".join(str(summary).split())
    if len(text) <= max_chars:
        return text
    if max_chars <= 3:
        return text[:max_chars]
    return f"{text[: max_chars - 3].rstrip()}..."


def provider_slug(provider: str) -> str:
    if provider not in PROVIDERS:
        raise ValueError(f"Unknown provider: {provider}")
    return re.sub(r"[^A-Za-z0-9]", "", provider)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def generate_run_id(provider: str, source_identity: str, created_at: datetime | None = None) -> str:
    slug = provider_slug(provider)
    tz = ZoneInfo("Australia/Sydney")
    now = created_at.astimezone(tz) if created_at else datetime.now(tz)
    timestamp = now.strftime("%Y%m%d_%H%M%S")
    seed = f"{slug}|{source_identity}|{timestamp}"
    hash5 = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:5].upper()
    return f"{slug}_{timestamp}_{hash5}"


def resolve_run_id_collision(
    provider: str,
    source_identity: str,
    run_parent: Path,
    created_at: datetime | None = None,
) -> tuple[str, dict[str, Any]]:
    slug = provider_slug(provider)
    tz = ZoneInfo("Australia/Sydney")
    now = created_at.astimezone(tz) if created_at else datetime.now(tz)
    timestamp = now.strftime("%Y%m%d_%H%M%S")
    seed = f"{slug}|{source_identity}|{timestamp}"
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest().upper()

    attempts: list[dict[str, Any]] = []
    for offset in range(0, len(digest) - 4):
        hash5 = digest[offset : offset + 5]
        run_id = f"{slug}_{timestamp}_{hash5}"
        exists = (run_parent / run_id).exists()
        attempts.append({"hash_offset": offset, "hash5": hash5, "run_id": run_id, "exists": exists})
        if not exists:
            return run_id, {
                "collision_checked": True,
                "collision_detected": offset > 0,
                "hash_algorithm": "sha256",
                "hash_offset": offset,
                "attempts": attempts,
            }

    raise RuntimeError("Unable to resolve run ID collision with SHA-256 five-character windows.")


def validate_run_id(run_id: str) -> bool:
    match = RUN_ID_RE.match(run_id)
    if not match:
        return False
    try:
        datetime.strptime(run_id.split("_", 1)[1].rsplit("_", 1)[0], "%Y%m%d_%H%M%S")
    except ValueError:
        return False
    return True


def ensure_provider(config: dict[str, Any], provider: str) -> dict[str, Any]:
    if provider not in PROVIDERS:
        raise ValueError(f"Provider is not supported: {provider}")
    return {
        "provider": provider,
        "provider_api_adapter_enabled": provider_api_adapter_enabled(config, provider),
    }


def provider_api_adapter_enabled(config: dict[str, Any], provider: str) -> bool:
    if provider not in PROVIDERS:
        raise ValueError(f"Provider is not supported: {provider}")
    adapters = config.get("provider_api_adapters", {})
    if not isinstance(adapters, dict):
        return False
    return adapters.get(provider.lower()) is True


def sharepoint_roots(_config: dict[str, Any] | None = None) -> tuple[Path, Path]:
    return DEFAULT_SHAREPOINT_UPLOAD_ROOT, DEFAULT_SHAREPOINT_RESULT_ROOT


def ensure_db_update_disabled(config: dict[str, Any]) -> None:
    if config.get("features", {}).get("db_update_enabled") is not False:
        raise RuntimeError("db_update_enabled must remain false unless explicit approval gates are implemented.")


def create_run_layout(run_root: Path) -> None:
    for name in RUN_SUBDIRS:
        (run_root / name).mkdir(parents=True, exist_ok=True)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_csv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def config_arg() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    return parser


@dataclass(frozen=True)
class RunPaths:
    root: Path
    source: Path
    extracted: Path
    normalized: Path
    raw_report: Path
    refined_report: Path
    evidence: Path
    logs: Path
    manifest: Path

    @classmethod
    def from_root(cls, root: Path) -> "RunPaths":
        return cls(
            root=root,
            source=root / "source",
            extracted=root / "extracted",
            normalized=root / "normalized",
            raw_report=root / "raw-recon-report",
            refined_report=root / "refined-recon-report",
            evidence=root / "evidence",
            logs=root / "logs",
            manifest=root / "manifest",
        )
