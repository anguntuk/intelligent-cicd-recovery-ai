from pathlib import Path

from cicd_recovery.remediation import run_remediation


def test_requires_explicit_approval(tmp_path: Path):
    code, report = run_remediation(tmp_path, approved=False)
    assert code == 2
    assert report["status"] == "approval_required"


def test_rejects_wrong_project_root(tmp_path: Path):
    code, report = run_remediation(tmp_path, approved=True)
    assert code == 2
    assert report["status"] == "invalid_project"
