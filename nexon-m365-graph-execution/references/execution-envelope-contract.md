# Execution Envelope Contract

## Request

Accept one object with this shape:

```json
{
  "mode": "execute",
  "ticket_sys_id": "0123456789abcdef0123456789abcdef",
  "correlation_id": "run-correlation-id",
  "attempt_id": "attempt-id",
  "tenant_id": "11111111-1111-1111-1111-111111111111",
  "target_type": "user",
  "target_id": "22222222-2222-2222-2222-222222222222",
  "operation": "disable-user",
  "parameters": {},
  "intended_state": { "account_enabled": false },
  "risk": "high",
  "plan_fingerprint": "versioned-sha256-plan-fingerprint",
  "approval_entry_id": "immutable-approval-entry-id",
  "claim_id": "servicenow-claim-id",
  "claim_version": 7
}
```

Reject unknown top-level fields, raw ServiceNow prose, approval prose, credentials, commands, arbitrary Graph URLs or bodies, multiple targets, and multiple operations.

`execute` requires non-empty `attempt_id`, `plan_fingerprint`, `approval_entry_id`, `claim_id`, and a non-negative integer `claim_version`. `preflight` prohibits writes and may use null claim fields. `verify_only` prohibits writes and requires the original attempt and claim identifiers.

The immutable operation contract determines `target_lifecycle`:

- `existing`: `execute` and `verify_only` require one resolved `target_id` GUID.
- `create`: `target_id` may be null before the write. The controlled resolver must prove that the exact proposed identifier does not already exist. A successful result must return the created object GUID. Verification-only recovery must locate exactly one object using the operation contract's immutable create identifier; zero or multiple matches are inconclusive.

## Result

Return one normalized object:

```json
{
  "mode": "execute",
  "status": "verified_success",
  "write_attempted": "yes",
  "tenant_assertion": "matched",
  "ticket_sys_id": "0123456789abcdef0123456789abcdef",
  "tenant_id": "11111111-1111-1111-1111-111111111111",
  "operation": "disable-user",
  "target_id": "22222222-2222-2222-2222-222222222222",
  "starting_state": { "account_enabled": true },
  "risk": "high",
  "risk_evidence": {},
  "verification": {
    "matched": true,
    "observed_state": { "account_enabled": false },
    "checked_at": "2026-07-16T12:00:00Z"
  },
  "safe_error": null,
  "plan_fingerprint": "versioned-sha256-plan-fingerprint",
  "approval_entry_id": "immutable-approval-entry-id",
  "claim_id": "servicenow-claim-id",
  "claim_version": 7,
  "attempt_id": "attempt-id",
  "correlation_id": "request-correlation-id"
}
```

Allowed status values are `preflight_complete`, `verified_success`, `failed`, and `inconclusive`. For `preflight_complete` and `verified_success`, `safe_error` is null. For `failed` or `inconclusive`, `safe_error` contains only a stable safe reason code and bounded safe message. Verification fields may be null only when no verification was attempted; `verified_success` requires `verification.matched=true`, a non-empty observed state, and a timestamp.

The supervisor must compare the returned ticket, mode, tenant, operation, target binding, plan fingerprint, approval entry, claim ID/version, attempt ID, and correlation ID with its request before updating ServiceNow. For a create operation, the returned target GUID becomes part of the verified ticket evidence.

Never return passwords, tokens, certificate material, authorization headers, raw authentication exceptions, arbitrary command output, or unrestricted Graph bodies.
