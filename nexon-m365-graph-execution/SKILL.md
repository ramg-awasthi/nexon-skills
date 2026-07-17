---
name: nexon-m365-graph-execution
description: Perform the Microsoft side of one claimed Nexon M365 ticket through controlled preflight, execution, or verification-only handling. Use when the fixed m365-execution subagent must determine conditional risk, validate a bounded execution envelope, authenticate to the tenant_id supplied by the approved CMDB workflow, invoke one immutable-registry handler, verify the end state, or return sanitized evidence.
---

# Nexon M365 Graph Execution

Process one bounded Microsoft request in `preflight`, `execute`, or `verify_only` mode. Do not interpret ServiceNow approval or update ServiceNow records.

## Load the required references

- Read [execution-envelope-contract.md](references/execution-envelope-contract.md) before accepting input or returning a result.
- Read [operation-contract.md](references/operation-contract.md) before resolving a target, determining risk, authenticating, invoking a handler, or verifying state.
- Read the immutable [operation-registry.json](references/operation-registry.json) only through the deterministic validator and dispatcher.

## Enforce the execution boundary

1. Accept only the bounded structure defined by the execution-envelope contract.
2. Reject raw ticket text, approval prose, credentials, commands, arbitrary URLs, request bodies, multiple targets, multiple operations, and unknown fields.
3. Validate the envelope with `scripts/Test-NexonM365ExecutionEnvelope.ps1` before Microsoft authentication.
4. In `preflight` mode, perform read-only target, state, and conditional-risk checks. Never write.
5. In `execute` mode, require the approved plan fingerprint, approval-entry ID, attempt ID, correlation ID, and ServiceNow execution-claim binding fields.
6. Authenticate the approved multi-tenant application to the CMDB tenant GUID.
7. Confirm the connected Graph tenant and organization match the CMDB tenant before a write.
8. Resolve exactly one target and query the normalized starting state.
9. Invoke exactly one complete handler from the immutable registry.
10. Independently query and compare the ending state.
11. In `verify_only` mode, perform no write under any condition.
12. Clear the process-scoped Microsoft authentication context in a guaranteed cleanup path.
13. Return only the sanitized structured result defined by the execution-envelope contract, preserving every request-binding field.

## Enforce mode restrictions

- `preflight`: read-only; return target, state, and risk evidence.
- `execute`: allow at most one bounded write after every deterministic gate passes.
- `verify_only`: read-only recovery after an uncertain write; never retry or correct the write.

## Fail closed

The executable registry is currently empty. Return `UNSUPPORTED_OPERATION` before authentication or write until a complete operation handler is approved, registered, and tested.

Never modify the registry, scripts, skills, subagent instructions, tools, computer configuration, credentials, or memory during an operational run.
