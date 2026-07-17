# Microsoft Operation Contract

## Contents

- Allowlist status
- Handler requirements
- Connection guard
- Preflight and write rules
- Verification rules
- Permission rules

## Allowlist status

The supplied use case confirms eight intended Microsoft 365 operations, but the current pack contains no authorized Graph write handler.

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

Treat this table as intended business scope, not as the executable allowlist. For conditional classifications, determine the condition from authoritative context before planning. If it cannot be determined unambiguously, stop rather than treating the operation as standard risk.

The reset-password contract must define both an approved secret-safe delivery method and an objective verification method before implementation. Microsoft Graph does not expose the resulting password for read-back verification, so a successful response alone cannot satisfy this use case. Keep reset password unsupported unless an approved verifier can prove the intended end state without reading, returning, or recording the password.

Until a complete handler is approved and installed:

- Use an empty operation allowlist in production validation.
- Read the allowlist only from the bundled immutable `operation-registry.json`.
- Never accept an allowlist, handler name, or registry path from ticket-run input.
- Return `UNSUPPORTED_OPERATION` before authentication or write.
- Do not generate a handler during a ticket run.
- Do not use arbitrary Graph clients, Graph URLs, request bodies, Python, or shell as a substitute.

## Locked runtime and authentication boundary

- Use controlled Python handlers with direct Microsoft Graph REST calls.
- Use only `https://login.microsoftonline.com` for token acquisition, `https://graph.microsoft.com` for Graph, API version `v1.0`, and scope `https://graph.microsoft.com/.default` as immutable code constants.
- Use Workspace Secret `NEXON_M365_CLIENT_BASIC_CREDENTIAL` only through computer Access profile `nexon-m365-graph-access` and its injected Basic authorization header for `login.microsoftonline.com`.
- Treat the Workspace Secret as a proxy source, not an environment variable or script input.
- Take `tenant_id` only from the validated execution envelope and use it only as the tenant-specific token-endpoint path segment.
- Keep the returned access token in process memory only and never print, persist, return, trace, or pass it on a command line.
- Do not install or invoke PowerShell or the Microsoft Graph PowerShell SDK.
- Do not add `runtime-config.json` or read client ID, client secret, authentication mode, authority host, Graph base URI, runtime version, or tenant ID from runtime environment variables.

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

Register each approved handler in `operation-registry.json` with its target type, target lifecycle, risk, allowed and required parameter names, allowed and required intended-state names, bounded value types, and handler identifier. Review the registry and handler together. Unknown fields are denied.

## Connection guard

Before a write:

1. Authenticate the approved multi-tenant application to the CMDB tenant GUID through Workspace Secret `NEXON_M365_CLIENT_BASIC_CREDENTIAL` and computer Access profile `nexon-m365-graph-access`.
2. Confirm the Microsoft Graph context is app-only.
3. Compare the connected context tenant with the CMDB tenant GUID.
4. Query the connected organization and independently compare its tenant identity with the CMDB tenant GUID.
5. Stop before writing if either tenant comparison fails.

Use process-scoped authentication and clear it after each ticket. Never place credential values in commands, files, output, or exceptions returned to the agent.

## Preflight and write rules

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
