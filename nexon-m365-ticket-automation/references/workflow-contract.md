# Workflow Contract

## Contents

- Ticket state model
- Triage result
- Execution envelope
- Execution result
- Gate checklist
- Scheduled-run summary

## Ticket state model

| State | Meaning | Microsoft write allowed |
|---|---|---|
| `discovered` | Candidate retrieved | No |
| `not_m365` | Not relevant or not automatable | No |
| `awaiting_ritm_approval` | RITM formal approval missing | No |
| `needs_information` | Required value missing or ambiguous | No |
| `awaiting_action_approval` | Current plan not explicitly approved | No |
| `ready_to_execute` | Gates passed but final recheck pending | No |
| `executing` | One controlled handler running | At most one |
| `verifying` | Ending state being independently queried | No additional write |
| `verified` | Observed state matches intended state | No |
| `failed_open` | Execution or verification failed | No |
| `resolved` | Verified evidence recorded and ticket completed | No |

## Triage result

Produce one object with this shape:

```json
{
  "ticket": {
    "table": "incident-or-sc_req_item",
    "sys_id": "service-now-sys-id",
    "number": "ticket-number"
  },
  "is_m365_related": true,
  "is_single_object_operation": true,
  "customer_reference": "authoritative-ticket-reference",
  "target": {
    "type": "user",
    "identifier_type": "object-id-or-exact-upn",
    "identifier": "target-identifier"
  },
  "operation": "supported-operation-name",
  "parameters": {},
  "intended_state": {},
  "risk": "standard-or-high",
  "missing_fields": [],
  "ambiguities": [],
  "evidence": []
}
```

Require one customer, target, and operation. Block execution for a non-empty `missing_fields` or `ambiguities` array.

## Execution envelope

Pass only this bounded structure to the controlled operation layer:

```json
{
  "ticket_sys_id": "service-now-sys-id",
  "correlation_id": "run-correlation-id",
  "tenant_id": "tenant-guid",
  "target_type": "user",
  "target_id": "m365-object-guid",
  "operation": "supported-operation-name",
  "parameters": {},
  "intended_state": {},
  "risk": "standard-or-high"
}
```

Do not add raw ticket prose, approval prose, credentials, arbitrary URLs, commands, arrays of targets, arrays of operations, or unknown top-level fields.

## Execution result

Accept only a normalized safe result:

```json
{
  "status": "verified-success-or-failed",
  "write_attempted": false,
  "tenant_assertion": "matched-or-not-matched-or-not-run",
  "operation": "supported-operation-name",
  "target_id": "m365-object-guid",
  "starting_state": {},
  "verification": {
    "matched": false,
    "observed_state": {},
    "checked_at": "ISO-8601 timestamp"
  },
  "safe_error": {
    "code": "SAFE_REASON_CODE",
    "message": "safe bounded explanation"
  },
  "correlation_id": "run-correlation-id"
}
```

Never return raw authentication exceptions, tokens, authorization headers, certificates, or unbounded Graph bodies.

## Gate checklist

Require all gates immediately before execution:

- Ticket remains open, in scope, and assigned to Nia.
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
- An atomic compare-and-set transition successfully claims this attempt immediately before Microsoft authentication.
- Connected Graph tenant matches the CMDB tenant before writing.

If any gate is false or unknown, do not invoke a write handler.

## Atomic execution claim

Bind the compare-and-set claim to:

```json
{
  "status": "executing",
  "ticket_sys_id": "service-now-sys-id",
  "plan_fingerprint": "stable-current-plan-fingerprint",
  "approval_entry_id": "immutable-approval-entry-id",
  "attempt_id": "unique-attempt-id",
  "correlation_id": "run-correlation-id",
  "lease_acquired_at": "ISO-8601 timestamp",
  "lease_expires_at": "ISO-8601 timestamp",
  "write_attempted": "no"
}
```

Require the expected current version. A compare-and-set failure returns `CONCURRENCY_CONFLICT` before Graph authentication. Preserve the attempt claim with `write_attempted=yes` or `unknown` when an outcome is uncertain; later processing is verification-only and cannot repeat the write automatically.

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
