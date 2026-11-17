from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "debug-frontend.yml"


def _jobs() -> dict:
    document = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return document["jobs"]


def test_unknown_failure_has_dedicated_triage_job_without_environment() -> None:
    jobs = _jobs()
    triage = jobs["unknown-failure-triage"]

    assert "environment" not in triage
    assert "needs.diagnose.outputs.unknown_failure == 'true'" in triage["if"]
    assert triage["permissions"] == {"contents": "read", "actions": "read"}


def test_unknown_failure_is_excluded_from_both_approval_gates() -> None:
    jobs = _jobs()
    for name in ("remediation-approval", "commit-approval"):
        condition = jobs[name]["if"]
        assert "needs.diagnose.outputs.unknown_failure != 'true'" in condition


def test_unknown_failure_is_excluded_from_all_automatic_mutation_jobs() -> None:
    jobs = _jobs()
    protected_jobs = (
        "first-approval-clock",
        "first-approval-watchdog",
        "remediation-execution",
        "remediation-review",
        "second-approval-clock",
        "second-approval-watchdog",
        "commit-push",
    )
    for name in protected_jobs:
        assert "needs.diagnose.outputs.unknown_failure != 'true'" in jobs[name]["if"]


def test_diagnosis_exposes_unknown_failure_output_and_triage_upload() -> None:
    jobs = _jobs()
    outputs = jobs["diagnose"]["outputs"]
    assert outputs["unknown_failure"] == "${{ steps.approval.outputs.unknown_failure }}"

    triage_steps = jobs["unknown-failure-triage"]["steps"]
    names = [step.get("name", "") for step in triage_steps]
    assert "Create manual unknown-failure triage record" in names
    assert "Upload unknown-failure triage package" in names
