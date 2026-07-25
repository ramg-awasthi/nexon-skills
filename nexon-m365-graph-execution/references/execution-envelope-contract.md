# Execution Envelope Contract

## Current contract boundary

The MCP surface keeps five lifecycle tools for capabilities, tenant verification,
preflight, generic fail-closed compatibility, and independent verification:

- `m365_graph_get_capabilities`
- `m365_graph_verify_tenant`
- `m365_graph_preflight_operation`
- `m365_graph_execute_operation`
- `m365_graph_verify_operation`

`m365_graph_preflight_operation` transports two strict schema variants through a typed `mode`: `planning` and `execution_binding`. Planning and execution-binding payloads are distinct contracts even though they use the same public MCP tool during this phase.

`m365_graph_execute_operation` is a permanently fail-closed compatibility
scaffold. It is not a remediation route. The only remediation tools are:

- `m365_graph_create_user`
- `m365_graph_disable_user`
- `m365_graph_enable_user`
- `m365_graph_reset_password`
- `m365_graph_assign_group`
- `m365_graph_assign_license`
- `m365_graph_remove_license`
- `m365_graph_grant_admin_role`

Every explicit tool is server-disabled by default. There is no duplicate
generic write route.

## Global schema rules

- Every request uses `schema_version: "1.0"`.
- Operation names are canonical lowercase underscore names, for example `assign_group`; hyphenated names are invalid.
- Unknown fields are rejected.
- Raw ServiceNow prose, approval prose, credentials, commands, arbitrary Graph URLs, Graph methods, Graph query strings, Graph request bodies, Graph scopes, shell, Python, and caller-extensible operation names are rejected.
- `request_id`, `correlation_id`, `attempt_id`, ticket IDs, approval IDs, claim IDs, and state versions are bounded identifiers.
- Target selectors are typed. Existing-target operations require `resolved_object_id`; create-target operations must not pre-bind `resolved_object_id`.
- `write_attempted` may be only `no`, `yes`, or `unknown`.

## Tenant verification request

```json
{
  "schema_version": "1.0",
  "request_id": "req-001",
  "correlation_id": "corr-001",
  "cmdb_tenant_id": "11111111-1111-1111-1111-111111111111"
}
```

Tenant verification proves that the CMDB tenant is onboarded, the app-only token belongs to that exact tenant, and `/organization` returns exactly the same tenant ID. A successful result includes a bounded tenant assertion for later lifecycle calls. The assertion is not a Microsoft token.

## Planning preflight request

Planning preflight is read-only. It validates bounded operation shape and produces a plan fingerprint for the ServiceNow planned-action work note. It creates no write authority.

```json
{
  "schema_version": "1.0",
  "mode": "planning",
  "request_id": "req-002",
  "correlation_id": "corr-001",
  "ticket": {
    "table": "incident",
    "sys_id": "0123456789abcdef0123456789abcdef",
    "number": "INC0010001",
    "state_version": "state-7"
  },
  "customer": {
    "id": "cust-001",
    "name": "Example Customer",
    "cmdb_record_id": "cmdb-001",
    "automation_enabled": true
  },
  "cmdb_tenant_id": "11111111-1111-1111-1111-111111111111",
  "tenant_assertion": {
    "assertion": "server-signed-tenant-assertion"
  },
  "operation": "assign_group",
  "operation_schema_version": "1.0",
  "target": {
    "object_type": "user",
    "identifier_type": "object_id",
    "identifier": "22222222-2222-2222-2222-222222222222",
    "resolved_object_id": "22222222-2222-2222-2222-222222222222"
  },
  "parameters": {
    "group_id": "33333333-3333-3333-3333-333333333333"
  },
  "intended_state": {
    "member_of_group_id": "33333333-3333-3333-3333-333333333333"
  },
  "risk": "standard"
}
```

## Execution-binding preflight request

Execution-binding preflight is also read-only. It binds the current plan, immutable ServiceNow approval activity, signed ServiceNow execution claim, tenant assertion, operation, target, parameters, intended state, attempt, and state version. It returns a server-signed execution binding for the deterministic write boundary.

The signed execution binding includes the fresh tenant assertion's `jti`,
SHA-256 hash, issued-at time, and expiry. Explicit execute revalidates all four
values so an older or different tenant assertion cannot be substituted.

```json
{
  "schema_version": "1.0",
  "mode": "execution_binding",
  "request_id": "req-003",
  "correlation_id": "corr-001",
  "attempt_id": "attempt-001",
  "ticket": {
    "table": "incident",
    "sys_id": "0123456789abcdef0123456789abcdef",
    "number": "INC0010001",
    "state_version": "state-8"
  },
  "customer": {
    "id": "cust-001",
    "name": "Example Customer",
    "cmdb_record_id": "cmdb-001",
    "automation_enabled": true
  },
  "cmdb_tenant_id": "11111111-1111-1111-1111-111111111111",
  "tenant_assertion": {
    "assertion": "server-signed-tenant-assertion"
  },
  "operation": "assign_group",
  "operation_schema_version": "1.0",
  "target": {
    "object_type": "user",
    "identifier_type": "object_id",
    "identifier": "22222222-2222-2222-2222-222222222222",
    "resolved_object_id": "22222222-2222-2222-2222-222222222222"
  },
  "parameters": {
    "group_id": "33333333-3333-3333-3333-333333333333"
  },
  "intended_state": {
    "member_of_group_id": "33333333-3333-3333-3333-333333333333"
  },
  "risk": "standard",
  "plan": {
    "work_note_id": "planned-note-001",
    "fingerprint": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "operation_schema_version": "1.0"
  },
  "approval": {
    "activity_id": "approval-activity-001",
    "approver_id": "approver-001",
    "approver_source": "servicenow",
    "approved_at": "2026-07-25T12:00:00Z",
    "plan_fingerprint": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "approval_policy_version": "approval-policy-1"
  },
  "claim": {
    "assertion": "servicenow-signed-execution-claim"
  }
}
```

Caller-provided claim booleans are never authority. The claim must be issued by the approved ServiceNow authority, signed with a trusted key, bounded to the exact ticket/plan/approval/attempt/state version, leased, and consumable once at the write boundary.

## Generic execute compatibility request

The generic execute request exists only to validate lifecycle binding and fail
closed. It must never become a route for a Microsoft write.

```json
{
  "schema_version": "1.0",
  "mode": "execute",
  "request_id": "req-004",
  "correlation_id": "corr-001",
  "attempt_id": "attempt-001",
  "ticket": {
    "table": "incident",
    "sys_id": "0123456789abcdef0123456789abcdef",
    "number": "INC0010001",
    "state_version": "state-8"
  },
  "customer": {
    "id": "cust-001",
    "name": "Example Customer",
    "cmdb_record_id": "cmdb-001",
    "automation_enabled": true
  },
  "cmdb_tenant_id": "11111111-1111-1111-1111-111111111111",
  "tenant_assertion": {
    "assertion": "server-signed-tenant-assertion"
  },
  "operation": "assign_group",
  "operation_schema_version": "1.0",
  "target": {
    "object_type": "user",
    "identifier_type": "object_id",
    "identifier": "22222222-2222-2222-2222-222222222222",
    "resolved_object_id": "22222222-2222-2222-2222-222222222222"
  },
  "parameters": {
    "group_id": "33333333-3333-3333-3333-333333333333"
  },
  "intended_state": {
    "member_of_group_id": "33333333-3333-3333-3333-333333333333"
  },
  "risk": "standard",
  "plan": {
    "work_note_id": "planned-note-001",
    "fingerprint": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "operation_schema_version": "1.0"
  },
  "approval": {
    "activity_id": "approval-activity-001",
    "approver_id": "approver-001",
    "approver_source": "servicenow",
    "approved_at": "2026-07-25T12:00:00Z",
    "plan_fingerprint": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "approval_policy_version": "approval-policy-1"
  },
  "claim": {
    "assertion": "servicenow-signed-execution-claim"
  },
  "execution_binding": {
    "request_id": "req-003",
    "fingerprint": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    "assertion": "server-signed-execution-binding"
  }
}
```

## Explicit operation request

The matching explicit tool accepts the same bound execution fields with
`"mode": "execute_explicit"` and its strict operation-specific `parameters` and
`intended_state` schemas. Unknown fields are rejected. The server validates the
tenant assertion, tenant allowlist, immutable registry, environment operation
list, write gate, plan, immutable approval activity, signed claim, claim
identity/version, and execution binding. It then reads the starting state.

If the intended state already exists, the claim authority consumes the bounded
attempt through its no-op path and Graph receives no write. Otherwise,
mandatory pre-write audit persistence and atomic claim consumption plus the
ServiceNow `write_started` marker must succeed before exactly one Graph write
is dispatched. A timeout after dispatch is `inconclusive`; the write is never
retried and recovery is verification-only.

Dispatch state is explicit. Schema validation, operation command preparation,
and all other failures before the transport call return `write_attempted=no`.
Immediately on entering the transport boundary, the outcome is `unknown` until
a response is confirmed. A confirmed Graph response returns
`write_attempted=yes`; an exception for which transmission cannot be proven
returns `write_attempted=unknown` and verification-only recovery.

## Explicit verification request

Verification is read-only and may be used for ordinary post-write verification or unknown-outcome recovery. It must never retry or perform a corrective write.

```json
{
  "schema_version": "1.0",
  "mode": "verify_explicit",
  "request_id": "req-005",
  "correlation_id": "corr-001",
  "attempt_id": "attempt-001",
  "ticket": {
    "table": "incident",
    "sys_id": "0123456789abcdef0123456789abcdef",
    "number": "INC0010001",
    "state_version": "state-8"
  },
  "customer": {
    "id": "cust-001",
    "name": "Example Customer",
    "cmdb_record_id": "cmdb-001",
    "automation_enabled": true
  },
  "cmdb_tenant_id": "11111111-1111-1111-1111-111111111111",
  "tenant_assertion": {
    "assertion": "fresh-server-signed-tenant-assertion"
  },
  "execution_tenant_assertion": {
    "assertion": "historical-tenant-assertion-bound-at-execution"
  },
  "operation": "assign_group",
  "operation_schema_version": "1.0",
  "target": {
    "object_type": "user",
    "identifier_type": "object_id",
    "identifier": "22222222-2222-2222-2222-222222222222",
    "resolved_object_id": "22222222-2222-2222-2222-222222222222"
  },
  "parameters": {
    "group_id": "33333333-3333-3333-3333-333333333333",
    "group_is_role_assignable": false,
    "group_has_hidden_membership": false,
    "side_effect_evidence_id": "evidence-group-001"
  },
  "intended_state": {
    "direct_member": true
  },
  "risk": "standard",
  "plan": {
    "work_note_id": "planned-note-001",
    "fingerprint": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "operation_schema_version": "1.0"
  },
  "approval": {
    "activity_id": "approval-activity-001",
    "approver_id": "approver-001",
    "approver_source": "servicenow",
    "approved_at": "2026-07-25T12:00:00Z",
    "plan_fingerprint": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "approval_policy_version": "approval-policy-1"
  },
  "claim": {
    "assertion": "servicenow-signed-execution-claim"
  },
  "execution_binding": {
    "request_id": "req-003",
    "fingerprint": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    "assertion": "server-signed-execution-binding"
  },
  "claim_id": "claim-001",
  "state_version": "state-9",
  "write_attempted": "unknown"
}
```

Before reading Graph, explicit verification validates a newly issued,
currently valid exact-tenant assertion. It also verifies the signatures and
historical binding of the original tenant assertion, ServiceNow claim, plan,
approval, and execution binding. Expiry of those historical write artifacts
does not authorize a new write and does not prevent recovery after the
authoritative claim service proves the claim was consumed or `write_started`,
matches the attempt/current state version, and permits verification-only
recovery. A caller-supplied claim ID or `write_attempted` value is never
sufficient authority.

## Result envelope

Every tool returns a top-level envelope:

```json
{
  "schema_version": "1.0",
  "kind": "operation_preflight",
  "result": {
    "status": "execution_preflight_complete",
    "schema_version": "1.0",
    "request_id": "req-003",
    "correlation_id": "corr-001",
    "environment": "dev",
    "ticket_sys_id": "0123456789abcdef0123456789abcdef",
    "ticket_number": "INC0010001",
    "ticket_table": "incident",
    "ticket_state_version": "state-8",
    "customer_id": "cust-001",
    "customer_cmdb_record_id": "cmdb-001",
    "automation_enabled": true,
    "cmdb_tenant_id": "11111111-1111-1111-1111-111111111111",
    "tenant_matched": true,
    "operation": "assign_group",
    "operation_schema_version": "1.0",
    "risk": "standard",
    "target_type": "user",
    "target_identifier_type": "object_id",
    "target_identifier_hash": "sha256-hash",
    "target_resolved_object_id": "22222222-2222-2222-2222-222222222222",
    "write_attempted": "no",
    "write_ready": false,
    "plan_fingerprint": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "execution_binding": {
      "fingerprint": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
      "assertion": "server-signed-execution-binding"
    },
    "observed_state": null,
    "safe_error": "operation_handler_not_implemented",
    "planning_only": false,
    "claim_id": "claim-001",
    "claim_state_version": "state-8",
    "claim_attempt_id": "attempt-001",
    "future_production_tool": "m365_graph_assign_group"
  }
}
```

Allowed lifecycle/result statuses are `planning_preflight_complete`,
`execution_preflight_complete`, `execution_bound`, `write_started`,
`verification_pending`, `verified_success`, `failed`, and `inconclusive`.
`verified_success` requires a non-empty observed state from an independent
Graph read. HTTP 2xx, a process exit code, or a write response body is not
sufficient verification.

The supervisor must compare request ID, correlation ID, ticket table/sys_id/number/state version, customer ID, CMDB tenant, operation, operation schema version, target binding, risk, plan fingerprint, approval activity, claim ID/state version, attempt ID, execution binding fingerprint, and tenant assertion before any ServiceNow update.

Never return passwords, tokens, certificate material, authorization headers, raw JWT claims, raw authentication exceptions, arbitrary command output, or unrestricted Graph bodies.
