from pathlib import Path

from cicd_recovery.analyzer import analyze_job, build_report
from cicd_recovery.rules import load_rules

ROOT = Path(__file__).resolve().parents[1]


def test_analyzer_returns_rule_based_diagnosis_and_failed_step() -> None:
    rules = load_rules(ROOT / "config" / "rules.yaml")
    job = {
        "id": 123,
        "name": "Node.js frontend CI",
        "conclusion": "failure",
        "steps": [
            {"name": "Install dependencies", "conclusion": "success"},
            {"name": "Simulate npm registry timeout", "conclusion": "failure"},
        ],
    }
    log = "npm error code ETIMEDOUT\nnetwork request failed: connect ETIMEDOUT"
    result = analyze_job(job=job, raw_log=log, rules=rules)
    assert result["diagnosis"]["category"] == "dependency_network_transient"
    assert result["failed_steps"][0]["name"] == "Simulate npm registry timeout"
    assert result["diagnosis"]["retry_candidate_for_future_policy_review"] is True
    assert result["diagnosis"]["unknown_triage"] is None


def test_report_records_selected_attempt_and_latest_attempt() -> None:
    report = build_report(
        repository="octo-org/demo",
        run={
            "id": 123,
            "name": "Frontend CI - Failure Scenarios",
            "run_attempt": 1,
            "latest_run_attempt": 2,
        },
        jobs=[],
        analyses=[],
    )
    assert report["workflow_run"]["run_attempt"] == 1
    assert report["workflow_run"]["latest_run_attempt"] == 2


def test_unknown_failure_routes_to_manual_triage_without_approval() -> None:
    rules = load_rules(ROOT / "config" / "rules.yaml")
    fixture = ROOT / "fixtures" / "logs" / "unknown_failure.log"
    job = {
        "id": 456,
        "name": "Node.js frontend CI",
        "conclusion": "failure",
        "steps": [
            {"name": "Install dependencies", "conclusion": "success"},
            {"name": "Run tests", "conclusion": "failure"},
        ],
    }

    analysis = analyze_job(
        job=job,
        raw_log=fixture.read_text(encoding="utf-8"),
        rules=rules,
    )

    diagnosis = analysis["diagnosis"]
    triage = diagnosis["unknown_triage"]
    plan = diagnosis["remediation_plan"]

    assert diagnosis["category"] == "unknown"
    assert diagnosis["matched_rule_ids"] == []
    assert diagnosis["confidence"] == "unknown"
    assert analysis["failed_steps"][0]["name"] == "Run tests"
    assert triage["route"] == "manual_engineering_investigation"
    assert triage["status"] == "requires_investigation"
    assert triage["approval_required"] is False
    assert triage["remediation_allowed"] is False
    assert triage["commit_allowed"] is False
    assert triage["push_allowed"] is False
    assert triage["ci_retry_allowed"] is False
    assert triage["ai_enabled"] is False
    assert triage["recommended_actions"]
    assert plan["decision"] == "not_configured"
    assert plan["verified"] is False
    assert plan["eligible"] is False
    assert plan["requires_approval"] is False
    assert plan["approval_gates"] == []


def test_report_marks_unknown_failures_as_non_approval_triage() -> None:
    rules = load_rules(ROOT / "config" / "rules.yaml")
    fixture = ROOT / "fixtures" / "logs" / "unknown_failure.log"
    analysis = analyze_job(
        job={"id": 789, "name": "Unknown job", "conclusion": "failure", "steps": []},
        raw_log=fixture.read_text(encoding="utf-8"),
        rules=rules,
    )

    report = build_report(
        repository="octo-org/demo",
        run={
            "id": 789,
            "name": "Frontend CI",
            "head_branch": "feature/unknown-demo",
            "head_sha": "a" * 40,
            "conclusion": "failure",
        },
        jobs=[{"id": 789}],
        analyses=[analysis],
    )

    assert report["routing"]["unknown_failure_present"] is True
    assert report["routing"]["unknown_failure_approval_gate"] is False
    assert report["routing"]["unknown_failure_remediation_allowed"] is False
    assert report["ai_enabled"] is False


def test_manually_verified_rule_surfaces_no_approval_plan(tmp_path: Path) -> None:
    rules_text = (ROOT / "config" / "rules.yaml").read_text(encoding="utf-8")
    assert "verified: false" in rules_text
    verified_rules_path = tmp_path / "rules.yaml"
    verified_rules_path.write_text(
        rules_text.replace("verified: false", "verified: true", 1),
        encoding="utf-8",
    )
    rules = load_rules(verified_rules_path)

    result = analyze_job(
        job={"id": 321, "name": "Node.js frontend CI", "conclusion": "failure", "steps": []},
        raw_log=(
            "AssertionError: expected CI/CD Recovery Demo "
            "to equal CI/CD Recovery Dashboard"
        ),
        rules=rules,
    )
    plan = result["diagnosis"]["remediation_plan"]
    assert result["diagnosis"]["category"] == "unit_test_failure"
    assert plan["verified"] is True
    assert plan["decision"] == "verified_auto_execution"
    assert plan["eligible"] is True
    assert plan["requires_approval"] is False
    assert plan["approval_gates"] == []
