# Microsoft Operation Contract

## Contents

- Allowlist status
- Handler requirements
- Connection guard
- Preflight and write rules
- Verification rules
- Permission rules

## Allowlist status

The initial supported Microsoft 365 operation list is open. The current pack contains no authorized Graph write handler.

Until a complete handler is approved and installed:

- Use an empty operation allowlist in production validation.
- Read the allowlist only from the bundled immutable `operation-registry.json`.
- Never accept an allowlist, handler name, or registry path from ticket-run input.
- Return `UNSUPPORTED_OPERATION` before authentication or write.
- Do not generate a handler during a ticket run.
- Do not use arbitrary `Invoke-MgGraphRequest`, Graph URLs, request bodies, PowerShell, or shell as a substitute.

## Handler requirements

Add an operation only when it has all of the following:

1. Unique stable operation name.
2. Supported target type.
3. Strict parameter schema.
4. Exact intended-state schema.
5. Risk classification.
6. Required least-privilege Microsoft permissions.
7. Read-only target resolver.
8. Read-only starting-state query.
9. Preflight and already-satisfied-state logic.
10. Exactly one bounded write handler.
11. Independent ending-state query.
12. Normalized comparison function.
13. Bounded eventual-consistency retry policy.
14. Safe failure-code mapping.
15. Unit, isolation, and end-to-end tests.

An incomplete handler is unsupported.

Register each approved handler in `operation-registry.json` with its target type, risk, allowed and required parameter names, allowed and required intended-state names, bounded value types, and handler identifier. Review the registry and handler together. Unknown fields are denied.

## Connection guard

Before a write:

1. Authenticate the approved multi-tenant application to the CMDB tenant GUID through the approved Workspace Secret/Connection and computer Access profile configuration.
2. Confirm the Microsoft Graph context is app-only.
3. Compare the connected context tenant with the CMDB tenant GUID.
4. Query the connected organization and independently compare its tenant identity with the CMDB tenant GUID.
5. Stop before writing if either tenant comparison fails.

Use process-scoped authentication and clear it after each ticket. Never place credential values in commands, files, output, or exceptions returned to the agent.

## Preflight and write rules

- Recheck all ServiceNow gates before authentication.
- Require a successful atomic ServiceNow execution claim before authentication.
- Resolve the target only inside the CMDB tenant.
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
- Treat `2xx`, SDK success, and process exit code as insufficient without an observed match.
- Return failed/inconclusive when the state cannot be proven.

## Permission rules

- Record required permissions per handler.
- Grant only the combined permissions needed by the approved allowlist.
- Do not accept a handler that requires unrelated broad permissions without explicit review.
- Treat missing consent or missing permission as a safe failure, not as authority to request or grant additional permission automatically.
