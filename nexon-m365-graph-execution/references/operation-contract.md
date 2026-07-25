# Microsoft Operation Contract

## Contents

- Allowlist status
- Handler requirements
- Connection guard
- Preflight and write rules
- Verification rules
- Permission rules

## Allowlist status

The supplied use case confirms eight intended Microsoft 365 operations. The MCP exposes the governed lifecycle tools now, but operation handlers remain server-gated and disabled unless the MCP server's immutable registry marks the exact handler implemented and enabled.

The agent-readable `operation-registry.json` is generated from the M365 Graph MCP source registry. It is a drift-detection and validation aid only; it is not execution authority.

| Intended operation | Supplied risk classification |
|---|---|
| Create user | Standard |
| Assign user to group | Standard; high risk when the group is role-assignable |
| Assign license | Standard |
| Remove license | Standard; high risk when it deprovisions a mailbox |
| Disable user or block sign-in | High risk |
| Enable user | Standard |
| Reset password | Standard; high risk for privileged accounts |
| Grant an admin role or privilege | High risk |

Treat this table as intended business scope, not as prompt-authorized execution. For conditional classifications, determine the condition from authoritative context before planning. If it cannot be determined unambiguously, stop rather than treating the operation as standard risk.

The reset-password contract must define both an approved secret-safe delivery method and an objective verification method before implementation. Microsoft Graph does not expose the resulting password for read-back verification, so a successful response alone cannot satisfy this use case. Keep reset password unsupported unless an approved verifier can prove the intended end state without reading, returning, or recording the password.

Create user also remains blocked until a safe initial-credential delivery method is approved.

Until a complete handler is approved, implemented, enabled in the MCP server registry, and allowed for the tenant:

- Treat the generated agent-readable `operation-registry.json` as business-scope reference only.
- Read execution authority only from the MCP capability response, tenant `allowed_operations`, and the MCP server's immutable registry.
- Never accept an allowlist, handler name, or registry path from ticket-run input.
- Return the MCP's stable blocked/unsupported code before any write.
- Do not generate a handler during a ticket run.
- Do not use arbitrary Graph clients, Graph URLs, request bodies, Python, or shell as a substitute.

## Locked runtime and authentication boundary

- Fleet uses only the environment-matched Nexon M365 Graph MCP connection and its MCP bearer credential.
- Microsoft application credentials, exact-tenant token acquisition, token claim checks, onboarded-tenant policy, `/organization` verification, and audit persistence remain inside the MCP server.
- The MCP exposes five lifecycle tools plus the eight explicit operation tools declared by the immutable registry.
- `m365_graph_preflight_operation` carries two distinct strict schema variants: `planning` and `execution_binding`.
- `m365_graph_execute_operation` is a fail-closed non-production compatibility scaffold in dev and prod. It must never run autonomously and is never the production write path.
- Remediation uses explicit per-operation tools only. The generic execute scaffold remains permanently fail-closed so there is no duplicate production execution route.
- Do not use the transitional Fleet Workspace Secret, computer Access profile, direct Microsoft identity or Graph REST, PowerShell, Python, shell, or caller-supplied URLs as an operational fallback.
- Do not expose Microsoft credentials, access tokens, raw claims, or raw Graph responses through MCP results.

## Handler requirements

Add an operation only when it has all of the following:

1. Unique stable operation name.
2. Supported target type and `target_lifecycle` of `existing` or `create`.
3. Strict parameter schema.
4. Exact intended-state schema.
5. Risk classification.
6. Required least-privilege Microsoft permissions.
7. Read-only target resolver, including exact non-existence checks and immutable create-key rules for create operations.
8. Read-only starting-state query.
9. Preflight and already-satisfied-state logic.
10. Exactly one bounded write handler.
11. Independent ending-state query.
12. Normalized comparison function.
13. Bounded eventual-consistency retry policy.
14. Safe failure-code mapping.
15. Unit, isolation, and end-to-end tests.

An incomplete handler is unsupported.

Register each approved handler in the server-side immutable registry with its target type, target lifecycle, risk, allowed and required parameter names, allowed and required intended-state names, bounded value types, handler identifier, verifier identifier, future production MCP tool name, idempotency fields, policy dependencies, and enablement flags. Regenerate the Fleet-readable registry from that source. Unknown fields are denied.

## Connection guard

Before operation preflight or write:

1. Call `m365_graph_get_capabilities` and require an environment match, `autonomous_remediation=false`, all five lifecycle tools, all eight registry-declared explicit tools, and `generic_execute_production_allowed=false`.
2. Call `m365_graph_verify_tenant` with the authoritative CMDB tenant GUID.
3. Require the MCP to confirm onboarding, token-tenant identity, and exact `/organization` identity.
4. Stop if any tenant assertion fails or the audit ledger is unavailable.
5. For planning preflight, call `m365_graph_preflight_operation` with `mode=planning` and only bounded ticket, customer, tenant, operation, target, parameter, intended-state, and risk fields.
6. For execution-binding preflight, call `m365_graph_preflight_operation` with `mode=execution_binding` and only bounded plan, explicit ServiceNow approval evidence, signed ServiceNow claim, and the same operation binding.
7. Never use generic execute for remediation. Call only the exact registry-matched explicit per-operation MCP tool after all binding, approval, claim, tenant, enablement, and audit gates pass.

## Preflight and write rules

These rules describe the current governed lifecycle. They do not authorize autonomous execution or any operation whose handler remains blocked.

- The supervisor rechecks all authoritative ServiceNow gates and acquires the atomic execution claim immediately before handoff.
- The execution component validates the bounded approval and claim binding fields but does not independently interpret or query ServiceNow approval.
- Resolve the target only inside the CMDB tenant.
- For an existing-target operation, require exactly one target GUID before execution.
- For a create-target operation, prove that the exact immutable create identifier does not already exist before writing; zero or multiple matches during recovery are inconclusive.
- Read the normalized starting state.
- If starting state already equals intended state, make no write and perform verification.
- Otherwise execute one handler once for one target.
- Never automatically retry a write with an uncertain outcome.
- Never make a second corrective write during the same ticket operation.
- Sanitize and bound stdout/stderr inside the controlled handler before any output reaches the agent or Fleet trace.

## Verification rules

- Query the ending state independently of the write response.
- Compare only the operation-defined normalized fields.
- Use bounded read-only retries only when the operation contract permits eventual consistency.
- Treat HTTP status, response body, and process exit code as insufficient without an observed match.
- Return failed/inconclusive when the state cannot be proven.

## Permission rules

- Record required permissions per handler.
- Grant only the combined permissions needed by the approved allowlist.
- Do not accept a handler that requires unrelated broad permissions without explicit review.
- Treat missing consent or missing permission as a safe failure, not as authority to request or grant additional permission automatically.
