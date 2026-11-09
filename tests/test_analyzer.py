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
