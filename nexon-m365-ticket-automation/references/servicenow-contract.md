# ServiceNow Contract

## Contents

- Required logical MCP capabilities
- Tool-output rules
- Approval evidence
- Work-note templates
- Ticket completion rules

## Required logical MCP capabilities

Final tool names and fields depend on the internal Nexon MCP. Map them explicitly before enabling the workflow.

```text
list_m365_ticket_candidates(assigned_to, incident_states, ritm_states, limit, cursor)
get_m365_ticket_context(table, id)
get_m365_ticket_activity(table, id, created_after?, cursor?)
get_ritm_approval(id)
resolve_customer_m365_tenant(table, ticket_id)
add_m365_ticket_work_note(table, id, note, correlation_id)
complete_m365_ticket(table, id, outcome, resolution_fields, correlation_id, verification_reference)
get_m365_automation_state(table, id)
set_m365_automation_state(table, id, expected_version, state_payload)
claim_m365_execution(table, id, expected_version, plan_fingerprint, approval_entry_id, attempt_id, correlation_id, lease_expires_at)
```

If a required capability is unavailable, return a safe blocked result. Do not replace it with an unapproved Table API, browser action, or arbitrary request.

## Tool-output rules

Require the MCP to:

- Return structured JSON.
- Include record IDs and safe display values needed for audit.
- Bound and paginate collections.
- Preserve immutable journal-entry identity, author, and timestamp.
- Distinguish zero, one, and multiple tenant matches.
- Reject unsupported tables, fields, and state transitions.
- Exclude credentials, OAuth tokens, cookies, and authorization headers.
- Enforce optimistic concurrency on automation state.
- Atomically acquire the execution claim before Microsoft authentication and reject a stale expected version.
- Return an explicit `automation_enabled`/onboarding value with the tenant mapping.
- Enforce bounded work-note length and reject secret-like or credential-bearing work-note input server-side.

## Approval evidence

For RITM formal approval require authoritative approval-record evidence and a current approved state.

For planned-action approval require:

- Immutable activity-entry ID.
- Designated approver identity.
- Timestamp after the current planned-action note.
- Content explicitly approving the exact current plan.
- Evidence that plan-relevant data did not change afterward.
- Evidence that the approval has not already been consumed.

Neither an earlier generic approval nor a Teams reply satisfies the planned-action approval gate.

## Planned-action note

```text
<HIGH-RISK HEADER WHEN APPLICABLE>

Planned M365 action
Ticket: <number>
Customer: <safe customer identifier>
Tenant: <safe tenant identifier>
Target: <safe target identifier>
Operation: <exact operation>
Intended end state: <exact state>
Risk: <standard/high>
Expected effect: <safe description>
Approval required: explicit approval of this specific planned action by a designated approver.
Correlation ID: <id>
```

Use `HIGH RISK M365 ACTION - <OPERATION>` as the first line for high-risk work.

## Blocked or missing-information note

```text
M365 automation stopped without making a Microsoft change.
Stage: <stage>
Reason: <safe reason code and explanation>
Required next action: <human action>
Correlation ID: <id>
```

## Verified-success note

```text
M365 action completed and independently verified.
Operation: <operation>
Target: <safe target identifier>
Tenant: <safe tenant identifier>
Observed end state: <safe evidence>
Verified at: <timestamp>
Correlation ID: <id>
```

## Failure note

```text
M365 automation failed or verification was inconclusive. The ticket remains open.
Stage: <stage>
Safe failure reason: <reason>
Microsoft change attempted: <yes/no/unknown>
Recommended human action: <next step>
Correlation ID: <id>
```

## Ticket completion rules

- Resolve or complete only after objective end-state verification.
- Apply the agreed Incident or RITM state mapping and required fields.
- Write verified evidence before completion.
- Leave the ticket open on execution failure, verification mismatch, ambiguity, or missing evidence.
- Persist completed automation state so a later schedule cannot repeat the write.
- Preserve `write_attempted=yes` or `unknown` and require verification-only recovery when a write outcome is uncertain.
