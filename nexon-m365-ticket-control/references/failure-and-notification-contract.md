# Failure and Notification Contract

## Contents

- Safe-stop behavior
- Reason-code baseline
- Teams message contract
- Duplicate and fallback rules

## Safe-stop behavior

For an actionable blocked, failed, or unknown safety gate:

1. Perform no Microsoft write, or no additional write if one may already have been attempted.
2. Preserve the best-known `write_attempted` value as `yes`, `no`, or `unknown`.
3. Leave the ServiceNow ticket open.
4. Add a bounded, secret-free failure or blocked work note.
5. Notify the configured operational user when the approved alert-trigger matrix requires it.
6. Do not resolve the ticket until a later run objectively verifies the intended state.

For `NOT_M365`, do not add an automation work note or send a notification to an unrelated ticket. Record only the bounded scheduled-run result and the approved durable evaluation marker needed to prevent immediate rediscovery. If classification is uncertain rather than conclusively out of scope, use `AMBIGUOUS_INFORMATION` and the normal safe-stop behavior.

## Reason-code baseline

Use stable codes and safe descriptions. Extend only through review.

| Code | Meaning |
|---|---|
| `NOT_M365` | Ticket is not an M365 request |
| `NOT_SINGLE_OPERATION` | Request violates the one-operation invariant |
| `MISSING_INFORMATION` | Required input is absent |
| `AMBIGUOUS_INFORMATION` | Required input has multiple or conflicting meanings |
| `RITM_NOT_APPROVED` | Formal RITM approval gate failed |
| `ACTION_NOT_APPROVED` | Exact planned action lacks valid approval |
| `PLAN_CHANGED` | Plan-relevant data changed after approval |
| `APPROVAL_ALREADY_CONSUMED` | Approval is already bound to another completed or active claim |
| `TENANT_NOT_FOUND` | No authoritative CMDB tenant mapping exists |
| `TENANT_AMBIGUOUS` | More than one authoritative tenant candidate exists |
| `AUTOMATION_NOT_ENABLED` | Customer tenant is not enabled/onboarded for automation |
| `TENANT_MISMATCH` | Connected Microsoft tenant does not match CMDB |
| `TARGET_NOT_FOUND` | Exact target cannot be resolved in the tenant |
| `TARGET_AMBIGUOUS` | More than one target matches |
| `UNSUPPORTED_OPERATION` | No complete approved handler exists |
| `INVALID_EXECUTION_ENVELOPE` | Bounded execution request failed deterministic validation |
| `RESULT_BINDING_MISMATCH` | Execution result does not match the originating request binding |
| `CONCURRENCY_CONFLICT` | Durable automation state changed concurrently |
| `EXECUTION_FAILED` | Controlled write returned a safe failure |
| `EXECUTION_OUTCOME_UNKNOWN` | Write outcome cannot be determined safely |
| `VERIFICATION_FAILED` | Observed end state does not match intended state |
| `VERIFICATION_INCONCLUSIVE` | Ending state cannot be proven |
| `DEPENDENCY_UNAVAILABLE` | Required MCP, runtime, skill, snapshot, or service is unavailable |
| `AUTHENTICATION_CLEANUP_FAILED` | Process-scoped Microsoft authentication cleanup could not be proven |
| `UNKNOWN_TRIGGER` | The run trigger is not an approved schedule, Teams, or manual-test trigger |
| `NOTIFICATION_FAILED` | Teams notification could not be delivered |

## Teams message contract

Use the Fleet Teams integration and stored conversation reference.

Include only:

- Ticket number and link.
- Safe customer identifier.
- Failed stage.
- Safe failure summary.
- Whether a Microsoft write was attempted, when known.
- Recommended human action.
- Correlation ID.

Do not include secrets, tokens, certificate material, authorization headers, raw credential errors, or unbounded Graph output.

Teams is interactive for explanation and troubleshooting. Direct approval and information changes back to ServiceNow. Never accept Teams as the authoritative execution-approval source.

## Duplicate and fallback rules

- Once Teams notification is enabled, `EXECUTION_FAILED`, `EXECUTION_OUTCOME_UNKNOWN`, `VERIFICATION_FAILED`, and `VERIFICATION_INCONCLUSIVE` always require a failure DM to the configured operational user. The alert matrix may add other safe-stop notifications but cannot remove this baseline.

- Suppress a notification for an unchanged failure fingerprint according to the approved policy.
- Do not claim delivery unless the Teams tool confirms it.
- Preserve the ServiceNow failure note if Teams delivery fails.
- Apply only the agreed bounded retry behavior.
- Use the agreed email address and delivery mechanism as fallback when configured.
- Record safe notification channel, result, and conversation reference metadata.

The deployment configuration must supply additional alert triggers, the failure fingerprint, re-notification rule, retry count and timing, responsible-user mapping, and email delivery mechanism. Until those values are approved, do not invent them. Production Microsoft writes require the mandatory failure-DM path and fallback behavior to be configured and tested.
