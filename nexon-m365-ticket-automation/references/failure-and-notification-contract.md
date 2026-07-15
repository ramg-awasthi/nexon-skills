# Failure and Notification Contract

## Contents

- Safe-stop behavior
- Reason-code baseline
- Teams message contract
- Duplicate and fallback rules

## Safe-stop behavior

For any failed or unknown safety gate:

1. Perform no Microsoft write, or no additional write if one may already have been attempted.
2. Preserve the best-known `write_attempted` value as `yes`, `no`, or `unknown`.
3. Leave the ServiceNow ticket open.
4. Add a bounded, secret-free failure or blocked work note.
5. Notify Nia when the approved alert-trigger matrix requires it.
6. Do not resolve the ticket until a later run objectively verifies the intended state.

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
| `TENANT_NOT_FOUND` | No authoritative CMDB tenant mapping exists |
| `TENANT_AMBIGUOUS` | More than one authoritative tenant candidate exists |
| `TENANT_MISMATCH` | Connected Microsoft tenant does not match CMDB |
| `TARGET_NOT_FOUND` | Exact target cannot be resolved in the tenant |
| `TARGET_AMBIGUOUS` | More than one target matches |
| `UNSUPPORTED_OPERATION` | No complete approved handler exists |
| `CONCURRENCY_CONFLICT` | Durable automation state changed concurrently |
| `EXECUTION_FAILED` | Controlled write returned a safe failure |
| `EXECUTION_OUTCOME_UNKNOWN` | Write outcome cannot be determined safely |
| `VERIFICATION_FAILED` | Observed end state does not match intended state |
| `VERIFICATION_INCONCLUSIVE` | Ending state cannot be proven |
| `DEPENDENCY_UNAVAILABLE` | Required MCP, runtime, skill, snapshot, or service is unavailable |
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

- Suppress a notification for an unchanged failure fingerprint according to the approved policy.
- Do not claim delivery unless the Teams tool confirms it.
- Preserve the ServiceNow failure note if Teams delivery fails.
- Apply only the agreed bounded retry behavior.
- Use the agreed email address and delivery mechanism as fallback when configured.
- Record safe notification channel, result, and conversation reference metadata.

The alert-trigger matrix, failure fingerprint, re-notification rule, retry count/timing, responsible-user mapping, and email delivery mechanism remain open. Until agreed, do not invent them.
