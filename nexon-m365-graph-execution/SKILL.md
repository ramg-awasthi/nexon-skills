---
name: nexon-m365-graph-execution
description: Use the approved Nexon M365 Graph MCP for bounded tenant verification, operation preflight, gated execution, and operation verification. Remediation is disabled by default and never autonomous.
---

# Nexon M365 Graph Execution

Process one bounded Microsoft `tenant_verify`, `preflight`, `execute_explicit`, or `verify_operation` request. Do not interpret ServiceNow approval or update ServiceNow records. Never call Graph directly. The generic execute tool is permanently fail closed; a remediation attempt may use only the matching explicit operation MCP tool.

## Load the required references

- Read [operation-contract.md](references/operation-contract.md) for the MCP lifecycle boundary and blocked operation rules.
- Treat [operation-registry.json](references/operation-registry.json) as agent-readable business scope only. The MCP server's immutable registry is authoritative for execution.

These links define the agent-readable source contracts. Microsoft credentials, token acquisition, tenant allowlisting, Graph calls, and auditing are owned by the MCP service.

## Enforce the execution boundary

1. For `tenant_verify`, accept only the strict tenant-verification request envelope with the authoritative `cmdb_tenant_id`.
2. For operation modes, accept only the bounded fields required by the MCP tool contract. For `preflight`, require either `mode=planning` or `mode=execution_binding`; do not blend the two payloads.
3. Reject raw ticket text, approval prose, credentials, commands, URLs, raw Graph request bodies, multiple targets, multiple operations, and unknown fields.
4. Call `m365_graph_get_capabilities`.
5. Require service `nexon-m365-graph-mcp`, the expected environment, `autonomous_remediation=false`, `generic_execute_production_allowed=false`, `future_write_tool_model=explicit_per_operation_tools`, all five lifecycle tools, and all eight registry-declared explicit operation tools.
6. Call `m365_graph_verify_tenant` with the same bounded request ID and CMDB tenant GUID.
7. Require `status=verified`, `onboarded=true`, `tenant_matched=true`, and matching CMDB, token-tenant, and organization GUIDs.
8. For planning preflight, call `m365_graph_preflight_operation` with `mode=planning` and return only the bounded plan fingerprint and safe result.
9. For execution-binding preflight, call `m365_graph_preflight_operation` with `mode=execution_binding` and return only the bounded execution binding and safe result.
10. For `execute_explicit`, require the exact execution binding, immutable approval evidence, signed atomic claim identity, fresh tenant-assertion binding, environment write gate, environment enabled-operation entry, tenant allowlist entry, and implemented/enabled immutable registry entry. Call only the registry-matched explicit operation tool. Never call `m365_graph_execute_operation` for remediation.
11. For `verify_operation`, call `m365_graph_verify_operation` and return only bounded verification evidence.

Do not use direct Microsoft identity or Graph REST, the Fleet computer, an Access profile, a workspace credential, PowerShell, Python, or shell as a fallback.

## Enforce mode restrictions

- `tenant_verify`: read-only capability and tenant verification only.
- `preflight`: read-only lifecycle binding only; `planning` and `execution_binding` are separate strict schemas.
- `execute_explicit`: one registry-matched explicit operation tool, server-disabled by default, never autonomous.
- `verify_operation`: read-only independent end-state verification.

## Fail closed

Return the MCP's stable fail-closed code when the write gate is disabled, the tenant does not allow the operation, the server registry does not mark the operation implemented/enabled, or operation-specific rules remain unresolved. Do not downgrade these blocked states into success.

Never modify the registry, skills, subagent instructions, tools, MCP connections, credentials, or memory during an operational run.
