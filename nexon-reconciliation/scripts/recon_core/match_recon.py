from __future__ import annotations

import argparse
from pathlib import Path

from .common import (
    DEFAULT_CONFIG_PATH,
    load_config,
    read_json,
    write_json,
)


def has_exact_match_evidence(candidate: dict) -> bool:
    return (
        candidate.get("service_id_match") is True
        and candidate.get("provider_match") is True
        and candidate.get("billing_period_match") is True
        and candidate.get("conflicting_candidate") is not True
        and candidate.get("one_to_many") is not True
    )


def _candidate_value(candidate: dict, *keys: str) -> object:
    for key in keys:
        value = candidate.get(key)
        if value not in (None, ""):
            return value
    return ""


def classify_line(
    line: dict,
    candidates: list[dict],
) -> dict:
    if not candidates:
        status = "Supplier Only"
        rule = "supplier_without_billing_candidate_v1"
        evidence_summary = "No billing candidate was returned for this invoice line."
        candidate = {}
    elif len(candidates) == 1 and has_exact_match_evidence(candidates[0]):
        status = "Matched"
        rule = "deterministic_exact_candidate_v1"
        evidence_summary = "Matched on service_id, provider, billing_period."
        candidate = candidates[0]
    elif len(candidates) == 1:
        status = "Not Matched"
        rule = "single_candidate_evidence_incomplete_v1"
        evidence_summary = "One billing candidate was returned, but deterministic exact-match evidence was incomplete."
        candidate = {}
    else:
        status = "Not Matched"
        rule = "multiple_candidates_v1"
        evidence_summary = f"{len(candidates)} billing candidates were returned; manual or exception review is required."
        candidate = {}
    return {
        "line_id": line.get("line_id", ""),
        "invoice_identity": line.get("invoice_identity", ""),
        "request_key": line.get("request_key", ""),
        "run_id": line.get("run_id", ""),
        "AccountPayableReconRequestId": line.get("account_payable_recon_request_id", ""),
        "GenericSupplierInvoiceLineItemId": line.get("generic_supplier_invoice_line_item_id", ""),
        "ServiceProviderInvoiceNumber": line.get("invoice_number", ""),
        "GenericNexonBillingId": _candidate_value(candidate, "generic_nexon_billing_id", "candidate_id"),
        "BillingDate": _candidate_value(candidate, "billing_date", "transaction_date"),
        "SupplierName": line.get("provider", ""),
        "SupplierAccountNumber": line.get("provider_account", ""),
        "NexonInfrastructure": line.get("infrastructure_cost", ""),
        "BillingCustomerName": _candidate_value(candidate, "customer_name", "customer_account", "customer"),
        "InvoiceServiceNumber": line.get("service_id_normalized") or line.get("service_id_raw", ""),
        "BillingServiceNumber": _candidate_value(candidate, "service_number", "service_id"),
        "BillingSystem": _candidate_value(candidate, "billing_system", "billing_system_name"),
        "InvoiceDetailDescription": line.get("detail_description", ""),
        "BillingServiceDescription": _candidate_value(candidate, "service_description"),
        "InomialServiceSpecification": _candidate_value(candidate, "service_spec_name", "service_specification"),
        "InvoiceServiceType": line.get("service_type", ""),
        "RecurringAmount": _candidate_value(candidate, "recurring_amount_excl_gst", "recurring_amount"),
        "Non-RecurringAmount": _candidate_value(candidate, "non_recurring_amount"),
        "Adjustment": _candidate_value(candidate, "adjustment"),
        "Discount": _candidate_value(candidate, "discount"),
        "Usage": _candidate_value(candidate, "usage_amount_excl_gst", "usage"),
        "InvoiceAmountExclGST": line.get("supplier_amount") or line.get("amount", ""),
        "BillingAmountExclGST": _candidate_value(candidate, "amount_excl_gst", "customer_invoice_amount"),
        "LastInvoiceDate": _candidate_value(candidate, "last_invoice_date", "service_last_invoice_date"),
        "Login": _candidate_value(candidate, "login", "service_login"),
        "InvoiceID": _candidate_value(candidate, "customer_invoice_number", "invoice_number"),
        "InvoiceAmount": _candidate_value(candidate, "last_invoice_amount", "service_last_invoice_amount"),
        "ReconMatchStatus": status,
        "Dispute or Not": "",
        "ManualMatch": "",
        "ManualMatch-InvoiceNumber": "",
        "ManualMatch-Amount": "",
        "Customer Billing Initiated": "",
        "Service Cancellation Initiated": "",
        "Reason": "",
        "deterministic_match_rule": rule,
        "deterministic_evidence_summary": evidence_summary,
        "deterministic_candidate_count": len(candidates),
        "candidate_count": len(candidates),
        "candidate_snapshot": candidate,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Deterministic match engine.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--normalized", type=Path, required=True)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    load_config(args.config)
    normalized = read_json(args.normalized)
    candidate_payload = read_json(args.candidates)
    lines = normalized.get("lines", [])
    candidates_by_line = candidate_payload.get("candidates_by_line", {})

    rows = []
    for line in lines:
        line_id = str(line.get("line_id", ""))
        rows.append(classify_line(line, candidates_by_line.get(line_id, [])))

    write_json(args.output, {"rows": rows})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
