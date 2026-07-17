#!/usr/bin/env python3
"""Fail-closed validator for one Nexon M365 execution envelope.

Read one JSON object from stdin, validate it against the immutable operation
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
TOP_LEVEL_PROPERTIES = (
    "mode",
    "ticket_sys_id",
    "correlation_id",
    "attempt_id",
    "tenant_id",
    "target_type",
    "target_id",
    "operation",
    "parameters",
    "intended_state",
    "risk",
    "plan_fingerprint",
    "approval_entry_id",
    "claim_id",
    "claim_version",
)
PROHIBITED_NAME = re.compile(
    r"(password|passwd|secret|token|authorization|private[_-]?key|certificate|"
    r"client[_-]?secret|credential|cookie)",
    re.IGNORECASE,
)
PROHIBITED_VALUE = re.compile(
    r"(^Bearer\s+|-----BEGIN [A-Z ]*PRIVATE KEY-----|"
    r"^[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}$)",
    re.IGNORECASE,
)
BOUNDED_ID = re.compile(r"^[A-Za-z0-9._:-]{1,256}$")
CORRELATION_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
SYS_ID = re.compile(r"^[0-9a-fA-F]{32}$")
SLUG = re.compile(r"^[a-z][a-z0-9-]{0,63}$")


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


def add_schema_errors(name: str, value: Any, schema: Any, errors: list[str]) -> None:
    if not is_plain_object(value):
        errors.append(f"{name} must be one JSON object.")
        return
    if not is_plain_object(schema):
        errors.append(f"Operation registry schema for {name} is invalid.")
        return

    property_specs = schema.get("properties") if is_plain_object(schema.get("properties")) else {}
    allowed_names = set(property_specs)
    required_names = schema.get("required") if isinstance(schema.get("required"), list) else []

    for required_name in required_names:
        if str(required_name) not in value:
            errors.append(f"{name} is missing required property: {required_name}.")

    for value_name, property_value in value.items():
        if value_name not in allowed_names:
            errors.append(f"{name} contains unknown property: {value_name}.")
            continue

        spec = property_specs[value_name]
        property_type = str(spec.get("type", "")) if is_plain_object(spec) else ""
        if property_type == "string":
            if not isinstance(property_value, str) or not property_value.strip():
                errors.append(f"{name}.{value_name} must be a non-empty string.")
            elif spec.get("max_length") is not None and len(property_value) > int(spec["max_length"]):
                errors.append(f"{name}.{value_name} exceeds its maximum length.")
        elif property_type == "boolean":
            if not isinstance(property_value, bool):
                errors.append(f"{name}.{value_name} must be a boolean.")
        elif property_type == "guid":
            if not is_nonempty_guid(property_value):
                errors.append(f"{name}.{value_name} must be a non-empty GUID.")
        elif property_type == "integer":
            if not isinstance(property_value, int) or isinstance(property_value, bool):
                errors.append(f"{name}.{value_name} must be an integer.")
        else:
            errors.append(f"Operation registry contains unsupported type for {name}.{value_name}.")

        enum_values = spec.get("enum") if is_plain_object(spec) else None
        if enum_values is not None and property_value not in enum_values:
            errors.append(f"{name}.{value_name} is not an allowed value.")


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

    errors: list[str] = []
    if not is_plain_object(envelope):
        errors.append("Envelope must be one JSON object.")
    else:
        actual_names = set(envelope)
        for name in TOP_LEVEL_PROPERTIES:
            if name not in actual_names:
                errors.append(f"Missing required property: {name}.")
        for name in envelope:
            if name not in TOP_LEVEL_PROPERTIES:
                errors.append(f"Unknown top-level property: {name}.")

        mode = envelope.get("mode")
        if "mode" in envelope and (not isinstance(mode, str) or mode not in {"preflight", "execute", "verify_only"}):
            errors.append("mode must be preflight, execute, or verify_only.")

        ticket_sys_id = envelope.get("ticket_sys_id")
        if "ticket_sys_id" in envelope and (
            not isinstance(ticket_sys_id, str) or not SYS_ID.fullmatch(ticket_sys_id)
        ):
            errors.append("ticket_sys_id must be a 32-character ServiceNow sys_id.")

        correlation_id = envelope.get("correlation_id")
        if "correlation_id" in envelope and (
            not isinstance(correlation_id, str)
            or not correlation_id.strip()
            or not CORRELATION_ID.fullmatch(correlation_id)
        ):
            errors.append("correlation_id must use the approved bounded character set and length.")

        if "tenant_id" in envelope and not is_nonempty_guid(envelope.get("tenant_id")):
            errors.append("tenant_id must be a non-empty GUID.")
        if "target_id" in envelope and envelope.get("target_id") is not None and not is_nonempty_guid(envelope["target_id"]):
            errors.append("target_id must be null or a resolved non-empty GUID.")

        target_type = envelope.get("target_type")
        if "target_type" in envelope and (
            not isinstance(target_type, str) or not SLUG.fullmatch(target_type)
        ):
            errors.append("target_type is invalid.")
        operation = envelope.get("operation")
        if "operation" in envelope and (
            not isinstance(operation, str) or not SLUG.fullmatch(operation)
        ):
            errors.append("operation is invalid.")

        risk = envelope.get("risk")
        if "risk" in envelope and (
            not isinstance(risk, str) or risk not in {"standard", "high", "pending-preflight"}
        ):
            errors.append("risk must be standard, high, or pending-preflight.")

        for bounded_id in ("attempt_id", "plan_fingerprint", "approval_entry_id", "claim_id"):
            value = envelope.get(bounded_id)
            if bounded_id in envelope and value is not None and (
                not isinstance(value, str) or not BOUNDED_ID.fullmatch(value)
            ):
                errors.append(f"{bounded_id} must be null or use the approved bounded character set and length.")

        claim_version = envelope.get("claim_version")
        if "claim_version" in envelope and claim_version is not None and (
            not isinstance(claim_version, int) or isinstance(claim_version, bool) or claim_version < 0
        ):
            errors.append("claim_version must be null or a non-negative integer.")

        if "parameters" in envelope and not is_plain_object(envelope.get("parameters")):
            errors.append("parameters must be one JSON object.")
        if "intended_state" in envelope and not is_plain_object(envelope.get("intended_state")):
            errors.append("intended_state must be one JSON object.")

        if mode in {"execute", "verify_only"}:
            if risk not in {"standard", "high"}:
                errors.append(f"{mode} requires a final standard or high risk classification.")
            for required_id in ("attempt_id", "plan_fingerprint", "approval_entry_id", "claim_id"):
                value = envelope.get(required_id)
                if not isinstance(value, str) or not value.strip():
                    errors.append(f"{mode} requires {required_id}.")
            if claim_version is None:
                errors.append(f"{mode} requires a non-negative integer claim_version.")

        errors.extend(find_prohibited_content(envelope))

    operation_contract: Any = None
    if not errors:
        if (
            not is_plain_object(registry)
            or registry.get("schema_version") != 1
            or isinstance(registry.get("schema_version"), bool)
            or not is_plain_object(registry.get("operations"))
        ):
            errors.append("Approved operation registry structure is invalid.")
        else:
            operation_contract = registry["operations"].get(envelope["operation"])
            if operation_contract is None:
                return validation_result(
                    False,
                    "UNSUPPORTED_OPERATION",
                    ["Operation is not in the approved immutable registry."],
                ), 2

    if not errors:
        if not is_plain_object(operation_contract):
            errors.append("Approved operation contract is invalid.")
        else:
            if operation_contract.get("target_type") != envelope["target_type"]:
                errors.append("target_type does not match the approved operation contract.")

            target_lifecycle = operation_contract.get("target_lifecycle")
            if target_lifecycle not in {"existing", "create"}:
                errors.append("Approved operation contract has an invalid target_lifecycle.")
            elif envelope["mode"] in {"execute", "verify_only"} and target_lifecycle == "existing" and not is_nonempty_guid(envelope["target_id"]):
                errors.append(f"{envelope['mode']} requires a resolved target_id GUID for an existing-target operation.")
            elif envelope["mode"] == "execute" and target_lifecycle == "create" and envelope["target_id"] is not None:
                errors.append("execute requires a null target_id before a create-target operation.")

            if envelope["risk"] != "pending-preflight" and operation_contract.get("risk") != envelope["risk"]:
                errors.append("risk does not match the approved operation contract.")
            if not isinstance(operation_contract.get("handler"), str) or not operation_contract["handler"].strip():
                errors.append("Approved operation contract has no handler identifier.")

            add_schema_errors("parameters", envelope["parameters"], operation_contract.get("parameters"), errors)
            add_schema_errors("intended_state", envelope["intended_state"], operation_contract.get("intended_state"), errors)
            if not envelope["intended_state"]:
                errors.append("intended_state must not be empty.")

    if errors:
        return validation_result(False, "INVALID_EXECUTION_ENVELOPE", errors), 2
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
