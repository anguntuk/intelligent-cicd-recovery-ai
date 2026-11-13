# Remediation policy preview (current implementation stage)

This increment adds typed remediation metadata to rules and a deterministic policy evaluator.
It does **not** apply patches, run validation commands, request approvals, create commits, push,
or retry CI.

## Current behavior

1. The rules loader parses and validates the optional `remediation` mapping.
2. The analyzer attaches a remediation-policy preview to each matched diagnosis.
3. Medium/high risk always requires human approval, even if a rule sets `approval_required: false`.
4. The terminal, GitHub Actions Summary, and JSON report show the decision, risk, approval gates,
   proposed validation commands, and `execution_status: not_executed`.
5. No write-capable executor is wired into this release.

## Rule field meanings

- `retry_candidate: true`: remediation may be considered; it is not permission to retry.
- `remediation.enabled`: enables policy evaluation for the rule.
- `risk`: `low`, `medium`, or `high`.
- `mode`: `assisted` or `recommend_only`.
- `approval_required`: explicit approval requirement; medium/high risk is escalated regardless.
- `max_attempts`: policy limit from 1 to 3. Persistent cross-run tracking is not implemented yet.
- `validation`: metadata describing reviewed validation commands. These are not executed yet.
- `target_branch`: must be `originating_feature_branch`.
- `commit_only_after_validation`: must be true for enabled remediation.

Validation commands are restricted to a code allowlist in `rules.py`. Do not add arbitrary shell
commands to rules. Add a reviewed command identifier only after security review.

## Future approval stages for high-risk remediation

1. `approve_remediation_proposal`
2. `approve_validated_changes_and_rule_promotion`

These are policy metadata only. GitHub Environment approval, isolated validation, and a publisher
job using a narrowly scoped GitHub App token are separate future implementation work.

## Current limitation

The policy evaluator receives `attempts_used=0` unless a caller supplies another count. A durable
incident/attempt store must be implemented before enforcing attempt limits across workflow runs.
