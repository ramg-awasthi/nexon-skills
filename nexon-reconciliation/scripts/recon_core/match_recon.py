from __future__ import annotations

import argparse
from pathlib import Path

from .common import (
    DEFAULT_CONFIG_PATH,
    evidence_summary_policy,
    load_config,
    normalize_evidence_summary,
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
    *,
    auto_matched_evidence: str = "short",
    evidence_max_chars: int = 160,
) -> dict:
    if not candidates:
        status = "no_match"
        rule = "no_candidate"
        evidence_summary = "No billing candidate was returned for this invoice line."
        suggested_fields = {}
    elif len(candidates) == 1 and has_exact_match_evidence(candidates[0]):
        status = "auto_matched"
        rule = "deterministic_exact_candidate_v1"
        evidence_summary = "Matched on service_id, provider, billing_period."
        candidate = candidates[0]
        suggested_fields = {
            "agent_suggested_customer_account": _candidate_value(candidate, "customer_account", "customer"),
            "agent_suggested_subscription_id": _candidate_value(candidate, "subscription_id"),
            "agent_suggested_invoice_number": _candidate_value(candidate, "invoice_number"),
            "agent_suggested_service_id": _candidate_value(candidate, "service_id", "service_id_normalized"),
        }
    elif len(candidates) == 1:
        status = "needs_review"
        rule = "candidate_evidence_incomplete"
        evidence_summary = "One billing candidate was returned, but deterministic exact-match evidence was incomplete."
        suggested_fields = {}
    else:
        status = "multi_match"
        rule = "multiple_candidates"
        evidence_summary = f"{len(candidates)} billing candidates were returned; manual or exception review is required."
        suggested_fields = {}
    if status == "auto_matched" and auto_matched_evidence == "blank":
        evidence_summary = ""
    else:
        evidence_summary = normalize_evidence_summary(evidence_summary, evidence_max_chars)
    return {
        **line,
        "agent_match_status": status,
        "agent_match_rule": rule,
        "agent_evidence_summary": evidence_summary,
        "agent_review_required": status != "auto_matched",
        "candidate_count": len(candidates),
        **suggested_fields,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Deterministic match engine.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--normalized", type=Path, required=True)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    policy = evidence_summary_policy(load_config(args.config))
    normalized = read_json(args.normalized)
    candidate_payload = read_json(args.candidates)
    lines = normalized.get("lines", [])
    candidates_by_line = candidate_payload.get("candidates_by_line", {})

    rows = []
    for line in lines:
        line_id = str(line.get("line_id", ""))
        rows.append(
            classify_line(
                line,
                candidates_by_line.get(line_id, []),
                auto_matched_evidence=policy["auto_matched"],
                evidence_max_chars=policy["max_chars"],
            )
        )

    write_json(args.output, {"rows": rows})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
