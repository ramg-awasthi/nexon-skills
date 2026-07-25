#!/usr/bin/env python3
"""Fail-closed validator for Nexon M365 Graph lifecycle envelopes.

Read one JSON object from stdin, validate it against the generated operation
registry beside this script, write one compact JSON result to stdout, and exit
with 0 for VALID or 2 for every rejected result.
"""

from __future__ import annotations

import json
import re
import sys
import uuid
from pathlib import Path
from typing import Any


REGISTRY_PATH = Path(__file__).resolve().parent.parent / "references" / "operation-registry.json"
SCHEMA_VERSION = "1.0"
MODES = {
    "planning",
    "execution_binding",
    "execute",
    "execute_explicit",
    "verify_only",
    "verify_explicit",
}
TICKET_TABLES = {"incident", "sc_req_item"}
WRITE_ATTEMPTED = {"no", "yes", "unknown"}
REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._:@/#-]{0,255}$")
SAFE_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
HEX64 = re.compile(r"^[a-f0-9]{64}$")
OPERATION = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
SYS_ID = re.compile(r"^[0-9a-fA-F]{32}$")
PROHIBITED_NAME = re.compile(
    r"(password|passwd|secret|token|authorization|private[_-]?key|certificate|"
    r"client[_-]?secret|credential|cookie|graph[_-]?(url|body|query|scope)|"
    r"shell|command)",
    re.IGNORECASE,
)
PROHIBITED_VALUE = re.compile(
    r"(^Bearer\s+|-----BEGIN [A-Z ]*PRIVATE KEY-----|"
    r"^[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}$)",
    re.IGNORECASE,
)


def validation_result(valid: bool, safe_reason_code: str, errors: list[str]) -> dict[str, Any]:
    return {"valid": valid, "safe_reason_code": safe_reason_code, "errors": errors}


def is_plain_object(value: Any) -> bool:
    return isinstance(value, dict)


def is_nonempty_guid(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return uuid.UUID(value).int != 0
    except (ValueError, AttributeError):
        return False


def find_prohibited_content(value: Any, path: str = "$") -> list[str]:
    errors: list[str] = []
    if value is None:
        return errors
    if isinstance(value, list):
        for index, item in enumerate(value):
            errors.extend(find_prohibited_content(item, f"{path}[{index}]"))
        return errors
    if isinstance(value, dict):
        for name, item in value.items():
            property_path = f"{path}.{name}"
            if PROHIBITED_NAME.search(str(name)):
                errors.append(f"Prohibited property name at {property_path}.")
            errors.extend(find_prohibited_content(item, property_path))
        return errors
    if isinstance(value, str):
        if len(value) > 4096:
            errors.append(f"Oversized string at {path}.")
        elif PROHIBITED_VALUE.search(value):
            errors.append(f"Prohibited secret-like value at {path}.")
    return errors


def require_fields(value: dict[str, Any], required: set[str], allowed: set[str], name: str) -> list[str]:
    errors: list[str] = []
    for field in sorted(required - set(value)):
        errors.append(f"{name} is missing required property: {field}.")
    for field in sorted(set(value) - allowed):
        errors.append(f"{name} contains unknown property: {field}.")
    return errors


def validate_bounded_id(value: Any, name: str, errors: list[str]) -> None:
    if not isinstance(value, str) or not REQUEST_ID.fullmatch(value):
        errors.append(f"{name} must use the approved bounded character set and length.")


def validate_safe_id(value: Any, name: str, errors: list[str]) -> None:
    if not isinstance(value, str) or not SAFE_ID.fullmatch(value):
        errors.append(f"{name} must use the approved bounded character set and length.")


def validate_ticket(value: Any, errors: list[str]) -> None:
    if not is_plain_object(value):
        errors.append("ticket must be one JSON object.")
        return
    required = {"table", "sys_id", "number", "state_version"}
    errors.extend(require_fields(value, required, required, "ticket"))
    if value.get("table") not in TICKET_TABLES:
        errors.append("ticket.table must be incident or sc_req_item.")
    if not isinstance(value.get("sys_id"), str) or not SYS_ID.fullmatch(value["sys_id"]):
        errors.append("ticket.sys_id must be a 32-character ServiceNow sys_id.")
    validate_safe_id(value.get("number"), "ticket.number", errors)
    validate_safe_id(value.get("state_version"), "ticket.state_version", errors)


def validate_customer(value: Any, errors: list[str]) -> None:
    if not is_plain_object(value):
        errors.append("customer must be one JSON object.")
        return
    required = {"id", "name", "cmdb_record_id", "automation_enabled"}
    errors.extend(require_fields(value, required, required, "customer"))
    validate_safe_id(value.get("id"), "customer.id", errors)
    validate_safe_id(value.get("name"), "customer.name", errors)
    validate_safe_id(value.get("cmdb_record_id"), "customer.cmdb_record_id", errors)
    if value.get("automation_enabled") is not True:
        errors.append("customer.automation_enabled must be the JSON boolean true.")


def validate_assertion_ref(value: Any, name: str, errors: list[str]) -> None:
    if not is_plain_object(value):
        errors.append(f"{name} must be one JSON object.")
        return
    errors.extend(require_fields(value, {"assertion"}, {"assertion"}, name))
    assertion = value.get("assertion")
    if not isinstance(assertion, str) or not 32 <= len(assertion) <= 8192:
        errors.append(f"{name}.assertion must be a bounded non-empty assertion string.")


def validate_target(value: Any, operation_contract: dict[str, Any], mode: str, errors: list[str]) -> None:
    if not is_plain_object(value):
        errors.append("target must be one JSON object.")
        return
    required = {"object_type", "identifier_type", "identifier", "resolved_object_id"}
    errors.extend(require_fields(value, required, required, "target"))
    if value.get("object_type") != operation_contract.get("target_type"):
        errors.append("target.object_type does not match the registry operation.")
    if value.get("identifier_type") not in {"object_id", "user_principal_name", "immutable_create_key"}:
        errors.append("target.identifier_type is invalid.")
    validate_safe_id(value.get("identifier"), "target.identifier", errors)
    resolved = value.get("resolved_object_id")
    if resolved is not None and not is_nonempty_guid(resolved):
        errors.append("target.resolved_object_id must be null or a non-empty GUID.")
    lifecycle = operation_contract.get("target_lifecycle")
    if lifecycle == "existing" and not is_nonempty_guid(resolved):
        errors.append(f"{mode} requires a resolved target.resolved_object_id GUID.")
    if lifecycle == "create" and resolved is not None:
        errors.append(f"{mode} must not pre-bind target.resolved_object_id for create operations.")
    if lifecycle not in {"existing", "create"}:
        errors.append("Registry operation has an invalid target_lifecycle.")


def validate_bounded_map(value: Any, name: str, *, allow_empty: bool, errors: list[str]) -> None:
    if not is_plain_object(value):
        errors.append(f"{name} must be one JSON object.")
        return
    if not value and not allow_empty:
        errors.append(f"{name} must not be empty.")
    if len(value) > 24:
        errors.append(f"{name} has too many fields.")
    for key, item in value.items():
        if not isinstance(key, str) or not SAFE_KEY.fullmatch(key):
            errors.append(f"{name} contains invalid key: {key}.")
        if isinstance(item, str):
            if item != item.strip() or len(item) > 512:
                errors.append(f"{name}.{key} must be a bounded string.")
        elif not (isinstance(item, bool) or isinstance(item, int) or item is None):
            errors.append(f"{name}.{key} has an unsupported value type.")


def validate_contract_object(
    value: Any, schema: Any, name: str, errors: list[str]
) -> None:
    if not is_plain_object(value) or not is_plain_object(schema):
        errors.append(f"{name} contract is unavailable or invalid.")
        return
    properties = schema.get("properties")
    required = schema.get("required")
    if not is_plain_object(properties) or not isinstance(required, list):
        errors.append(f"{name} contract is unavailable or invalid.")
        return
    errors.extend(require_fields(value, set(required), set(properties), name))
    for key, item in value.items():
        contract = properties.get(key)
        if not is_plain_object(contract):
            continue
        expected_type = contract.get("type")
        valid_type = (
            (expected_type == "string" and isinstance(item, str))
            or (expected_type == "boolean" and type(item) is bool)
            or (expected_type == "integer" and type(item) is int)
            or (expected_type == "array" and isinstance(item, list))
        )
        if not valid_type:
            errors.append(f"{name}.{key} has an invalid value type.")
            continue
        if "const" in contract and item != contract["const"]:
            errors.append(f"{name}.{key} does not match its required constant.")
        if isinstance(item, str):
            if len(item) > int(contract.get("maxLength", 512)):
                errors.append(f"{name}.{key} is oversized.")
            pattern = contract.get("pattern")
            if isinstance(pattern, str) and re.fullmatch(pattern, item) is None:
                errors.append(f"{name}.{key} does not match its required format.")
        if isinstance(item, list):
            if len(item) > int(contract.get("maxItems", 64)):
                errors.append(f"{name}.{key} has too many items.")
            item_contract = contract.get("items")
            if not is_plain_object(item_contract) or item_contract.get("type") != "string":
                errors.append(f"{name}.{key} has an unsupported array contract.")
            elif any(not isinstance(entry, str) for entry in item):
                errors.append(f"{name}.{key} must contain only strings.")


def validate_plan(value: Any, errors: list[str]) -> str | None:
    if not is_plain_object(value):
        errors.append("plan must be one JSON object.")
        return None
    required = {"work_note_id", "fingerprint", "operation_schema_version"}
    errors.extend(require_fields(value, required, required, "plan"))
    validate_safe_id(value.get("work_note_id"), "plan.work_note_id", errors)
    fingerprint = value.get("fingerprint")
    if not isinstance(fingerprint, str) or not HEX64.fullmatch(fingerprint):
        errors.append("plan.fingerprint must be a lowercase SHA-256 hex string.")
    if value.get("operation_schema_version") != SCHEMA_VERSION:
        errors.append("plan.operation_schema_version must be 1.0.")
    return fingerprint if isinstance(fingerprint, str) else None


def validate_approval(value: Any, expected_fingerprint: str | None, errors: list[str]) -> None:
    if not is_plain_object(value):
        errors.append("approval must be one JSON object.")
        return
    required = {
        "activity_id",
        "approver_id",
        "approver_source",
        "approved_at",
        "plan_fingerprint",
        "approval_policy_version",
    }
    errors.extend(require_fields(value, required, required, "approval"))
    validate_safe_id(value.get("activity_id"), "approval.activity_id", errors)
    validate_safe_id(value.get("approver_id"), "approval.approver_id", errors)
    validate_safe_id(value.get("approval_policy_version"), "approval.approval_policy_version", errors)
    if value.get("approver_source") != "servicenow":
        errors.append("approval.approver_source must be servicenow.")
    if not isinstance(value.get("approved_at"), str) or len(value["approved_at"]) > 64:
        errors.append("approval.approved_at must be a bounded ISO-8601 timestamp.")
    if value.get("plan_fingerprint") != expected_fingerprint:
        errors.append("approval.plan_fingerprint must match plan.fingerprint.")


def validate_execution_binding(value: Any, errors: list[str]) -> None:
    if not is_plain_object(value):
        errors.append("execution_binding must be one JSON object.")
        return
    required = {"request_id", "fingerprint", "assertion"}
    errors.extend(require_fields(value, required, required, "execution_binding"))
    validate_bounded_id(value.get("request_id"), "execution_binding.request_id", errors)
    fingerprint = value.get("fingerprint")
    if not isinstance(fingerprint, str) or not HEX64.fullmatch(fingerprint):
        errors.append("execution_binding.fingerprint must be a lowercase SHA-256 hex string.")
    assertion = value.get("assertion")
    if not isinstance(assertion, str) or not 32 <= len(assertion) <= 8192:
        errors.append("execution_binding.assertion must be a bounded assertion string.")


def validate_registry(registry: Any) -> tuple[dict[str, Any] | None, list[str]]:
    errors: list[str] = []
    if not is_plain_object(registry):
        return None, ["Approved operation registry structure is invalid."]
    if registry.get("schema_version") != SCHEMA_VERSION:
        errors.append("Approved operation registry schema version must be 1.0.")
    if registry.get("future_write_tool_model") != "explicit_per_operation_tools":
        errors.append("Approved operation registry must use explicit future per-operation tools.")
    if registry.get("generic_execute_production_allowed") is not False:
        errors.append("Generic execute must not be production-allowed.")
    operations = registry.get("operations")
    if not is_plain_object(operations):
        errors.append("Approved operation registry operations must be one JSON object.")
        return None, errors
    for name, operation in operations.items():
        if not OPERATION.fullmatch(str(name)):
            errors.append(f"Registry operation name is not canonical: {name}.")
        if not is_plain_object(operation):
            errors.append(f"Registry operation is invalid: {name}.")
            continue
        if type(operation.get("implemented")) is not bool:
            errors.append(f"Registry implemented flag is invalid: {name}.")
        if operation.get("enabled_by_default") is not False:
            errors.append(f"Registry operation is enabled by default: {name}.")
        if operation.get("generic_execute_production_allowed") is not False:
            errors.append(f"Registry operation allows generic production execute: {name}.")
        if operation.get("future_production_tool") != f"m365_graph_{name}":
            errors.append(f"Registry operation is missing future explicit tool name: {name}.")
        for contract_name in ("parameter_schema", "intended_state_schema"):
            contract = operation.get(contract_name)
            if not is_plain_object(contract) or contract.get("additionalProperties") is not False:
                errors.append(f"Registry {contract_name} is invalid: {name}.")
    return operations, errors


def validate(input_json: str, registry_path: Path = REGISTRY_PATH) -> tuple[dict[str, Any], int]:
    if not 2 <= len(input_json) <= 32768:
        return validation_result(
            False,
            "INVALID_EXECUTION_ENVELOPE",
            ["Input JSON length must be between 2 and 32768 characters."],
        ), 2

    try:
        envelope = json.loads(input_json)
    except (json.JSONDecodeError, TypeError, ValueError):
        return validation_result(False, "INVALID_EXECUTION_ENVELOPE", ["Input is not valid JSON."]), 2

    try:
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return validation_result(
            False,
            "DEPENDENCY_UNAVAILABLE",
            ["Approved operation registry is unavailable or invalid."],
        ), 2

    operations, registry_errors = validate_registry(registry)
    if registry_errors:
        return validation_result(False, "INVALID_OPERATION_REGISTRY", registry_errors), 2
    if operations is None:
        return validation_result(
            False,
            "DEPENDENCY_UNAVAILABLE",
            ["Approved operation registry is unavailable or invalid."],
        ), 2

    errors: list[str] = []
    if not is_plain_object(envelope):
        return validation_result(False, "INVALID_EXECUTION_ENVELOPE", ["Envelope must be one JSON object."]), 2

    mode = envelope.get("mode")
    if mode not in MODES:
        errors.append(
            "mode must be planning, execution_binding, execute, execute_explicit, "
            "verify_only, or verify_explicit."
        )

    common = {
        "schema_version",
        "mode",
        "request_id",
        "correlation_id",
        "ticket",
        "customer",
        "cmdb_tenant_id",
        "tenant_assertion",
        "operation",
        "operation_schema_version",
        "target",
        "intended_state",
    }
    allowed = set(common)
    required = set(common)
    if mode in {"planning", "execution_binding", "execute", "execute_explicit"}:
        allowed.update({"parameters", "risk"})
        required.update({"parameters", "risk"})
    if mode in {"execution_binding", "execute", "execute_explicit"}:
        allowed.update({"attempt_id", "plan", "approval", "claim"})
        required.update({"attempt_id", "plan", "approval", "claim"})
    if mode in {"execute", "execute_explicit"}:
        allowed.add("execution_binding")
        required.add("execution_binding")
    if mode == "verify_only":
        allowed.update(
            {"attempt_id", "claim_id", "state_version", "write_attempted", "parameters"}
        )
        required.update({"attempt_id", "claim_id", "state_version", "write_attempted"})
    if mode == "verify_explicit":
        allowed.update(
            {
                "attempt_id",
                "claim_id",
                "state_version",
                "write_attempted",
                "parameters",
                "risk",
                "plan",
                "approval",
                "claim",
                "execution_binding",
                "execution_tenant_assertion",
            }
        )
        required.update(
            {
                "attempt_id",
                "claim_id",
                "state_version",
                "write_attempted",
                "parameters",
                "risk",
                "plan",
                "approval",
                "claim",
                "execution_binding",
                "execution_tenant_assertion",
            }
        )

    errors.extend(require_fields(envelope, required, allowed, "envelope"))
    if envelope.get("schema_version") != SCHEMA_VERSION:
        errors.append("schema_version must be 1.0.")
    validate_bounded_id(envelope.get("request_id"), "request_id", errors)
    validate_bounded_id(envelope.get("correlation_id"), "correlation_id", errors)
    if not is_nonempty_guid(envelope.get("cmdb_tenant_id")):
        errors.append("cmdb_tenant_id must be a non-empty GUID.")

    operation = envelope.get("operation")
    operation_contract = operations.get(operation) if isinstance(operation, str) else None
    if not isinstance(operation, str) or not OPERATION.fullmatch(operation):
        errors.append("operation must be a canonical lowercase underscore name.")
    elif operation_contract is None:
        return validation_result(
            False,
            "UNSUPPORTED_OPERATION",
            ["Operation is not in the approved generated registry."],
        ), 2

    if envelope.get("operation_schema_version") != SCHEMA_VERSION:
        errors.append("operation_schema_version must be 1.0.")

    validate_ticket(envelope.get("ticket"), errors)
    validate_customer(envelope.get("customer"), errors)
    validate_assertion_ref(envelope.get("tenant_assertion"), "tenant_assertion", errors)
    if mode == "verify_explicit":
        validate_assertion_ref(
            envelope.get("execution_tenant_assertion"),
            "execution_tenant_assertion",
            errors,
        )

    if is_plain_object(operation_contract):
        validate_target(envelope.get("target"), operation_contract, str(mode), errors)
        risk = envelope.get("risk")
        if mode in {
            "planning",
            "execution_binding",
            "execute",
            "execute_explicit",
            "verify_explicit",
        }:
            supplied_risks = operation_contract.get("supplied_risk_levels")
            if risk not in supplied_risks:
                errors.append("risk does not match the registry operation.")

    if "parameters" in envelope:
        validate_contract_object(
            envelope.get("parameters"),
            operation_contract.get("parameter_schema") if operation_contract else None,
            "parameters",
            errors,
        )
    validate_contract_object(
        envelope.get("intended_state"),
        operation_contract.get("intended_state_schema") if operation_contract else None,
        "intended_state",
        errors,
    )

    if "attempt_id" in envelope:
        validate_bounded_id(envelope.get("attempt_id"), "attempt_id", errors)
    if mode in {"execution_binding", "execute", "execute_explicit", "verify_explicit"}:
        plan_fingerprint = validate_plan(envelope.get("plan"), errors)
        validate_approval(envelope.get("approval"), plan_fingerprint, errors)
        validate_assertion_ref(envelope.get("claim"), "claim", errors)
    if mode in {"execute", "execute_explicit", "verify_explicit"}:
        validate_execution_binding(envelope.get("execution_binding"), errors)
    if mode in {"verify_only", "verify_explicit"}:
        validate_safe_id(envelope.get("claim_id"), "claim_id", errors)
        validate_safe_id(envelope.get("state_version"), "state_version", errors)
        if envelope.get("write_attempted") not in WRITE_ATTEMPTED:
            errors.append("write_attempted must be no, yes, or unknown.")

    errors.extend(find_prohibited_content(envelope))
    if errors:
        return validation_result(False, "INVALID_EXECUTION_ENVELOPE", errors), 2
    if mode == "execute":
        return validation_result(
            False,
            "GENERIC_EXECUTE_NOT_PRODUCTION_PATH",
            ["Generic execute is a fail-closed non-production scaffold."],
        ), 2
    return validation_result(True, "VALID", []), 0


def main() -> int:
    try:
        result, exit_code = validate(sys.stdin.read())
    except Exception:
        result = validation_result(
            False,
            "DEPENDENCY_UNAVAILABLE",
            ["Execution-envelope validation failed safely."],
        )
        exit_code = 2
    print(json.dumps(result, separators=(",", ":")))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
