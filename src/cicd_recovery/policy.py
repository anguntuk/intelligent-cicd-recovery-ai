"""Read-only remediation policy evaluation; no commands or GitHub writes are executed."""
from __future__ import annotations

from dataclasses import asdict, dataclass

from cicd_recovery.rules import RemediationSpec


@dataclass(frozen=True)
class RemediationDecision:
    decision: str
    eligible: bool
    risk: str | None
    requires_approval: bool
    approval_gates: tuple[str, ...]
    max_attempts: int
    attempts_used: int
    validation_commands: tuple[str, ...]
    target_branch: str | None
    commit_only_after_validation: bool
    execution_status: str
    executor_available: bool
    reason: str
    next_action: str

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["approval_gates"] = list(self.approval_gates)
        data["validation_commands"] = list(self.validation_commands)
        return data


def evaluate_remediation_policy(
    *, retry_candidate: bool, remediation: RemediationSpec | None, attempts_used: int = 0
) -> RemediationDecision:
    """Evaluate a rule's remediation metadata without applying any action.

    retry_candidate means 'eligible for policy review'; it is never execution permission.
    Attempt counts are supplied by the caller; durable cross-run tracking is not implemented.
    """
    if isinstance(attempts_used, bool) or not isinstance(attempts_used, int) or attempts_used < 0:
        raise ValueError("attempts_used must be a non-negative integer")

    common = {
        "risk": remediation.risk if remediation else None,
        "max_attempts": remediation.max_attempts if remediation else 0,
        "attempts_used": attempts_used,
        "validation_commands": remediation.validation if remediation else (),
        "target_branch": remediation.target_branch if remediation else None,
        "commit_only_after_validation": remediation.commit_only_after_validation if remediation else True,
        "execution_status": "not_executed",
        "executor_available": False,
    }

    if remediation is None:
        values = dict(
            decision="not_configured", eligible=False, requires_approval=False,
            approval_gates=(), reason="No remediation configuration is attached to the matched rule.",
            next_action="Show the rule recommendation; no remediation is planned.",
        )
    elif not remediation.enabled:
        values = dict(
            decision="disabled", eligible=False, requires_approval=False,
            approval_gates=(), reason="Remediation is disabled for this rule.",
            next_action="Provide the recommendation only.",
        )
    elif not retry_candidate:
        values = dict(
            decision="not_candidate", eligible=False, requires_approval=False,
            approval_gates=(), reason="The rule is not marked as a remediation candidate.",
            next_action="Provide the recommendation only.",
        )
    elif attempts_used >= remediation.max_attempts:
        values = dict(
            decision="attempt_limit_reached", eligible=False, requires_approval=False,
            approval_gates=(), reason="The configured remediation attempt limit has been reached.",
            next_action="Stop remediation and request engineering review.",
        )
    else:
        # Medium/high risk must require approval, even if the YAML flag was set false.
        needs_approval = remediation.approval_required or remediation.risk in {"medium", "high"}
        if needs_approval:
            values = dict(
                decision="approval_required", eligible=True, requires_approval=True,
                approval_gates=(
                    "approve_remediation_proposal",
                    "approve_validated_changes_and_rule_promotion",
                ),
                reason=(
                    f"The remediation is {remediation.risk} risk or explicitly requires approval. "
                    "Human approval is mandatory."
                ),
                next_action=(
                    "Prepare a remediation proposal for review. After approval, validate the "
                    "candidate in isolation; request separate approval before committing the "
                    "validated fix and promoting any rule changes."
                ),
            )
        else:
            values = dict(
                decision="eligible_for_future_executor", eligible=True, requires_approval=False,
                approval_gates=(),
                reason="The configured remediation is a low-risk candidate.",
                next_action=(
                    "Eligible for a future allowlisted executor, subject to validation and retry policy."
                ),
            )

    return RemediationDecision(**common, **values)
