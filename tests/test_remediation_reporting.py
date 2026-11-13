from pathlib import Path

from cicd_recovery.analyzer import analyze_job
from cicd_recovery.rules import load_rules

ROOT = Path(__file__).resolve().parents[1]


def test_analysis_includes_high_risk_approval_plan_but_executes_nothing() -> None:
    rules = load_rules(ROOT / "config/rules.yaml")
    result = analyze_job(
        job={"id": 22, "name": "Node.js frontend CI", "conclusion": "failure",
             "steps": [{"name": "Run tests", "conclusion": "failure"}]},
        raw_log="not ok 1 - title assertion\nAssertionError: expected title to equal dashboard",
        rules=rules,
    )
    diagnosis = result["diagnosis"]
    plan = diagnosis["remediation_plan"]
    assert diagnosis["category"] == "unit_test_failure"
    assert diagnosis["matched_rule_ids"] == ["unit_test_failure"]
    assert plan["decision"] == "approval_required"
    assert plan["requires_approval"] is True
    assert plan["execution_status"] == "not_executed"
    assert plan["executor_available"] is False


def test_multiline_recommendation_is_preserved() -> None:
    rules = load_rules(ROOT / "config/rules.yaml")
    result = analyze_job(
        job={"id": 23, "name": "tests", "conclusion": "failure", "steps": []},
        raw_log="AssertionError: expected title to equal dashboard",
        rules=rules,
    )
    recommendation = result["diagnosis"]["recommendation"]
    assert "\n" in recommendation
    assert "1. Open the failing test file" in recommendation
    assert "5. Run npm test and npm run build" in recommendation


def test_github_summary_contains_multiline_steps_and_policy_preview(tmp_path, monkeypatch) -> None:
    from cicd_recovery.cli import _append_github_step_summary

    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    rules = load_rules(ROOT / "config/rules.yaml")
    analysis = analyze_job(
        job={"id": 24, "name": "Node.js frontend CI", "conclusion": "failure", "steps": []},
        raw_log="AssertionError: expected title to equal dashboard",
        rules=rules,
    )
    _append_github_step_summary({
        "repository": "octo-org/demo",
        "workflow_run": {"id": 123, "name": "Frontend CI", "conclusion": "failure", "html_url": "https://example.invalid/run/123"},
        "analyses": [analysis],
    })
    rendered = summary.read_text(encoding="utf-8")
    assert "#### Recommended resolution steps" in rendered
    assert "1. Open the failing test file" in rendered
    assert "5. Run npm test and npm run build" in rendered
    assert "#### Remediation policy assessment" in rendered
    assert "approval_required" in rendered
    assert "planned only; not executed" in rendered
