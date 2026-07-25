# Workflow Contract

## Contents

- Ticket state model
- Triage result
- Planned-action and approval binding
- Microsoft lifecycle handoff
- Execution result
- Gate checklist
- Atomic execution claim
- Scheduled-run summary

## Ticket state model

| State | Meaning | Microsoft write allowed |
|---|---|---|
| `discovered` | Candidate retrieved | No |
| `awaiting_ritm_approval` | RITM formal approval missing | No |
| `awaiting_action_approval` | Current plan not explicitly approved | No |
| `ready_to_claim` | All planning and approval gates pass; no claim yet | No |
| `claimed` | ServiceNow issued the bounded leased claim | No |
| `execution_bound` | Read-only execution preflight bound claim, approval, plan, target, operation, and fresh tenant assertion | No |
| `write_started` | Claim consumed once and ServiceNow write-start marker persisted | At most one |
| `verification_pending` | Independent ending-state read is required | No additional write |
| `verified_success` | Observed state objectively matches intended state | No |
| `failed` | Safe failure before a write or a confirmed failed outcome | No |
| `inconclusive` | Write outcome or verification cannot be proven; recovery is verification-only | No |
| `closed` | Verified evidence recorded and ticket completed | No |

## Triage result

Produce one bounded object:

```json
{
  "ticket": {
    "table": "incident",
    "sys_id": "0123456789abcdef0123456789abcdef",
    "number": "INC0010001",
    "state_version": "state-7"
  },
  "is_m365_related": true,
  "is_single_object_operation": true,
  "customer": {
    "id": "cust-001",
    "name": "Example Customer",
    "cmdb_record_id": "cmdb-001",
    "automation_enabled": true
  },
  "cmdb_tenant_id": "11111111-1111-1111-1111-111111111111",
  "target": {
    "object_type": "user",
    "identifier_type": "object_id",
    "identifier": "22222222-2222-2222-2222-222222222222",
    "resolved_object_id": "22222222-2222-2222-2222-222222222222"
  },
  "operation": "assign_group",
  "operation_schema_version": "1.0",
  "parameters": {
    "group_id": "33333333-3333-3333-3333-333333333333"
  },
  "intended_state": {
    "member_of_group_id": "33333333-3333-3333-3333-333333333333"
  },
  "risk": "standard",
  "missing_fields": [],
  "ambiguities": [],
  "evidence": []
}
```

Require one ticket, customer, tenant, target, and operation. Block execution for a non-empty `missing_fields` or `ambiguities` array. Do not accept tenant authority from ticket prose; CMDB resolution is authoritative and must include `automation_enabled=true`.

## Planned-action and approval binding

The planned-action work note must include the exact bounded action, target, tenant, risk, and generated plan fingerprint. HIGH RISK actions must be unmistakably flagged. Secrets and passwords are never written.

The explicit approval evidence must be immutable ServiceNow activity data:

```json
{
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
  }
}
```

Approval must be from the designated approver source, after the planned-action note, bound to the same plan fingerprint, and not previously consumed. Teams and email never approve or advance execution.

## Microsoft lifecycle handoff

The supervisor sends only strict structured payloads to the fixed `m365-execution` subagent. Keep these aligned with the Graph-execution skill's execution-envelope contract.

Planning preflight is read-only and cannot produce write authority:

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

Execution-binding preflight is read-only and binds the final ServiceNow claim and approval:

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

The generic execute scaffold is permanently fail-closed and never a remediation route. Remediation uses only the exact registry-matched explicit per-operation MCP tool. There is no duplicate execution route.

Do not add raw ticket prose, approval prose, credentials, arbitrary URLs, commands, arrays of targets, arrays of operations, or unknown fields.

## Execution result

Accept only a normalized safe MCP envelope:

```json
{
  "schema_version": "1.0",
  "kind": "operation_verification",
  "result": {
    "status": "verified_success",
    "write_attempted": "yes",
    "request_id": "req-005",
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
    "attempt_id": "attempt-001",
    "claim_id": "claim-001",
    "claim_state_version": "state-9",
    "plan_fingerprint": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "approval_activity_id": "approval-activity-001",
    "execution_binding_fingerprint": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    "execution_binding_jti": "execution-binding-jti",
    "tenant_assertion_jti": "fresh-tenant-assertion-jti",
    "execution_tenant_assertion_jti": "historical-tenant-assertion-jti",
    "verified": true,
    "observed_state": {
      "group_id": "33333333-3333-3333-3333-333333333333",
      "user_id": "22222222-2222-2222-2222-222222222222",
      "direct_member": true,
      "role_assignable": false,
      "hidden_membership": false
    },
    "safe_error": null,
    "future_production_tool": "m365_graph_assign_group"
  }
}
```

Allowed lifecycle/result statuses are `planning_preflight_complete`,
`execution_preflight_complete`, `execution_bound`, `write_started`,
`verification_pending`, `verified_success`, `failed`, and `inconclusive`.
Compare the returned ticket, tenant, operation, target binding, plan
fingerprint, approval activity, claim ID/state version, attempt ID, correlation
ID, and fresh tenant-assertion binding before any ServiceNow update. Require
`status=verified_success`, `verified=true`, and a non-empty observed state
before successful closeout.

For later verification-only recovery, obtain a fresh exact-tenant assertion
and retain the historical tenant assertion, signed claim, and execution
binding from the consumed attempt. The M365 Graph MCP verifies their
signatures and immutable bindings while the authoritative ServiceNow claim
service proves the expired write lease was consumed/`write_started`. Expired
write authority may support read-only recovery but can never authorize another
write.

Never accept raw authentication exceptions, tokens, authorization headers, certificates, passwords, private keys, raw JWT claims, or unbounded Graph bodies.

## Gate checklist

Require all gates immediately before execution:

- Ticket remains open, in scope, and assigned to the configured operational user represented by the Nia placeholder during UAT.
- Ticket still maps to one customer, target, operation, and tenant.
- RITM formal approval is current when applicable.
- Current planned action exists.
- Designated approver explicitly approved that exact plan after it was posted.
- No plan-relevant value changed after approval.
- Approval is not already consumed.
- CMDB returns exactly one active tenant GUID for the ticket customer.
- The tenant mapping explicitly confirms that the customer is enabled/onboarded for automation.
- Target resolves exactly once inside that tenant.
- Operation is allowlisted and parameters match its schema.
- A signed atomic ServiceNow claim exists, has not expired, has not been consumed, and matches the exact plan/approval/attempt/state version.
- Connected Graph tenant matches the CMDB tenant before writing.
- Future production execution is through the explicit per-operation MCP tool for the operation.

If any gate is false or unknown, do not invoke a write handler.

## Atomic execution claim

The ServiceNow MCP must issue a server-authoritative signed claim, not a caller-provided boolean:

```json
{
  "type": "servicenow_execution_claim",
  "environment": "dev",
  "ticket_table": "incident",
  "ticket_sys_id": "0123456789abcdef0123456789abcdef",
  "ticket_number": "INC0010001",
  "ticket_state_version": "state-8",
  "customer_id": "cust-001",
  "cmdb_tenant_id": "11111111-1111-1111-1111-111111111111",
  "operation": "assign_group",
  "operation_schema_version": "1.0",
  "plan_fingerprint": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "plan_work_note_id": "planned-note-001",
  "approval_activity_id": "approval-activity-001",
  "approver_id": "approver-001",
  "approval_policy_version": "approval-policy-1",
  "attempt_id": "attempt-001",
  "claim_id": "claim-001",
  "state_version": "state-8",
  "claim_lifecycle_version": "1.0",
  "lease_acquired_at": "2026-07-25T12:01:00Z",
  "lease_expires_at": "2026-07-25T12:06:00Z",
  "write_attempted": "no"
}
```

Require the expected current record version before the claim is issued. A
compare-and-set failure returns `CONCURRENCY_CONFLICT` before Graph
authentication. At the deterministic write boundary, the authority must
atomically consume the claim once, verify its lease/version, and persist
`write_started` before dispatch. A crash after this marker or an uncertain
write preserves the attempt with `write_attempted=yes` or `unknown`; later
processing is verification-only and cannot repeat the write automatically.

## Scheduled-run summary

Return a bounded conversational summary containing only:

```json
{
  "candidates_seen": 0,
  "tickets_advanced": 0,
  "tickets_waiting": 0,
  "verified_successes": 0,
  "safe_stops": 0,
  "failures": 0,
  "items": [
    {
      "ticket_number": "safe-ticket-number",
      "result_state": "safe-state",
      "safe_reason_code": "SAFE_REASON_CODE",
      "correlation_id": "run-correlation-id"
    }
  ],
  "next_cursor": "opaque-or-null"
}
```

Bound `items` to the candidate-page limit. Do not include raw descriptions, journal text, customer data beyond approved safe identifiers, or tool/Graph bodies.
