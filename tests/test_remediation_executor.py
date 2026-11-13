
from pathlib import Path
import json
import subprocess

from cicd_recovery import remediation
from cicd_recovery.remediation import run_remediation


def test_requires_explicit_approval(tmp_path: Path):
    code, report = run_remediation(tmp_path, approved=False)
    assert code == 2
    assert report["status"] == "approval_required"


def test_rejects_wrong_project_root(tmp_path: Path):
    code, report = run_remediation(tmp_path, approved=True)
    assert code == 2
    assert report["status"] == "invalid_project"


APP_TEST = """const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

// harmless local comment: semantic matching must tolerate unrelated context

test('frontend entry page exists and has a title', () => {
  const page = fs.readFileSync(path.join(__dirname, '..', 'src', 'index.html'), 'utf8');
  assert.match(page, /<title>CI\\/CD Recovery Demo<\\/title>/);
});
"""

KNOWN_ISSUE_TEST = """const test = require('node:test');
const assert = require('node:assert/strict');

test('known issue demo: frontend title expectation mismatch', () => {
  const actualTitle = 'CI/CD Recovery Demo';
  const expectedTitle = 'CI/CD Recovery Dashboard';

  assert.equal(
    actualTitle,
    expectedTitle,
    'Frontend title does not match the expected product title'
  );
});
"""

HTML = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <!-- keep this unrelated comment -->
    <title>CI/CD Recovery Demo</title>
  </head>
  <body><main><h1>CI/CD Recovery Demo Frontend</h1></main></body>
</html>
"""


def make_project(root: Path):
    (root / "pyproject.toml").write_text(
        "[project]\nname='fixture'\n",
        encoding="utf-8",
    )

    frontend = root / "frontend"
    (frontend / "src").mkdir(parents=True)
    (frontend / "test").mkdir(parents=True)

    (frontend / "package.json").write_text(
        json.dumps({
            "scripts": {
                "test": "node --test",
                "build": "node scripts/build.js",
            }
        }),
        encoding="utf-8",
    )

    files = {
        "frontend/src/index.html": HTML,
        "frontend/test/app.test.js": APP_TEST,
        "frontend/test/known-issue-demo.test.js": KNOWN_ISSUE_TEST,
    }

    for relative, content in files.items():
        (root / relative).write_text(content, encoding="utf-8")

    return {
        relative: (root / relative).read_bytes()
        for relative in files
    }


def successful_process(args, **kwargs):
    return subprocess.CompletedProcess(
        args=args,
        returncode=0,
        stdout="checks passed",
        stderr="",
    )


def test_context_aware_recipe_accepts_unrelated_file_edits_and_preserves_them(
    tmp_path,
    monkeypatch,
):
    originals = make_project(tmp_path)
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return successful_process(args, **kwargs)

    monkeypatch.setattr(remediation.subprocess, "run", fake_run)

    code, report = run_remediation(tmp_path, approved=True)

    assert code == 0
    assert report["status"] == "validated_pending_commit_approval"
    assert report["applicability"]["whole_file_hashes_used_as_preconditions"] is False
    assert len(calls) == 2
    assert report["changed_files"] == list(remediation.RELATIVE_FILES)

    html = (tmp_path / "frontend/src/index.html").read_text(encoding="utf-8")
    app_test = (tmp_path / "frontend/test/app.test.js").read_text(encoding="utf-8")
    known_test = (
        tmp_path / "frontend/test/known-issue-demo.test.js"
    ).read_text(encoding="utf-8")

    assert "<!-- keep this unrelated comment -->" in html
    assert "<title>CI/CD Recovery Dashboard</title>" in html
    assert "CI\\/CD Recovery Dashboard" in app_test
    assert "const actualTitle = 'CI/CD Recovery Demo'" not in known_test

    assert all(
        (tmp_path / relative).read_bytes() != originals[relative]
        for relative in remediation.RELATIVE_FILES
    )

    assert report["commit_performed"] is False
    assert report["push_performed"] is False
    assert report["ci_retry_triggered"] is False


def test_unsupported_context_fails_closed_without_modifying_files(
    tmp_path,
    monkeypatch,
):
    make_project(tmp_path)

    html_path = tmp_path / "frontend/src/index.html"
    html_path.write_text(
        HTML.replace("CI/CD Recovery Demo", "Another Product"),
        encoding="utf-8",
    )

    before = {
        relative: (tmp_path / relative).read_bytes()
        for relative in remediation.RELATIVE_FILES
    }

    monkeypatch.setattr(remediation.subprocess, "run", successful_process)

    code, report = run_remediation(tmp_path, approved=True)

    assert code == 2
    assert report["status"] == "not_applicable"

    assert {
        relative: (tmp_path / relative).read_bytes()
        for relative in remediation.RELATIVE_FILES
    } == before


def test_validation_failure_rolls_back_every_changed_file(
    tmp_path,
    monkeypatch,
):
    originals = make_project(tmp_path)
    invocation = 0

    def fail_build(args, **kwargs):
        nonlocal invocation
        invocation += 1

        if invocation == 1:
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout="tests pass",
                stderr="",
            )

        return subprocess.CompletedProcess(
            args=args,
            returncode=1,
            stdout="",
            stderr="build failed",
        )

    monkeypatch.setattr(remediation.subprocess, "run", fail_build)

    code, report = run_remediation(tmp_path, approved=True)

    assert code == 1
    assert report["status"] == "validation_failed_rolled_back"
    assert report["rollback_performed"] is True
    assert report["validation"][0]["passed"] is True
    assert report["validation"][1]["passed"] is False

    for relative, original in originals.items():
        assert (tmp_path / relative).read_bytes() == original


def test_already_correct_files_are_idempotent(tmp_path, monkeypatch):
    make_project(tmp_path)

    monkeypatch.setattr(remediation.subprocess, "run", successful_process)

    code, first = run_remediation(tmp_path, approved=True)
    assert code == 0

    changed_after_first_run = {
        relative: (tmp_path / relative).read_bytes()
        for relative in remediation.RELATIVE_FILES
    }

    code, second = run_remediation(tmp_path, approved=True)

    assert code == 0
    assert second["changed_files"] == []

    assert {
        relative: (tmp_path / relative).read_bytes()
        for relative in remediation.RELATIVE_FILES
    } == changed_after_first_run


def test_github_execution_requires_matching_diagnosis_evidence(
    tmp_path,
    monkeypatch,
):
    make_project(tmp_path)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setattr(remediation.subprocess, "run", successful_process)

    code, report = run_remediation(tmp_path, approved=True)

    assert code == 2
    assert report["status"] == "not_applicable"
    assert "requires --diagnosis-report" in report["message"]


def test_diagnosis_report_must_match_specific_title_failure(
    tmp_path,
    monkeypatch,
):
    make_project(tmp_path)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")

    diagnosis_path = tmp_path / "diagnosis.json"
    diagnosis_path.write_text(
        json.dumps({
            "workflow_run": {
                "head_sha": "a" * 40,
                "head_branch": "feature/demo",
            },
            "analyses": [{
                "diagnosis": {
                    "category": "unit_test_failure",
                    "matched_rule_ids": ["unit_test_failure"],
                },
                "sanitized_log_excerpt": "AssertionError: expected 1 to equal 2",
            }],
        }),
        encoding="utf-8",
    )

    code, report = run_remediation(
        tmp_path,
        approved=True,
        diagnosis_report=diagnosis_path,
        expected_source_sha="a" * 40,
        expected_source_branch="feature/demo",
    )

    assert code == 2
    assert report["status"] == "not_applicable"
    assert "does not contain both the actual and expected title values" in report["message"]


def test_matching_diagnosis_evidence_is_bound_to_source_revision(
    tmp_path,
    monkeypatch,
):
    make_project(tmp_path)

    diagnosis_path = tmp_path / "diagnosis.json"
    diagnosis_path.write_text(
        json.dumps({
            "workflow_run": {
                "head_sha": "b" * 40,
                "head_branch": "feature/demo",
            },
            "analyses": [{
                "diagnosis": {
                    "category": "unit_test_failure",
                    "matched_rule_ids": ["unit_test_failure"],
                },
                "sanitized_log_excerpt": (
                    "AssertionError: actual CI/CD Recovery Demo; "
                    "expected CI/CD Recovery Dashboard"
                ),
            }],
        }),
        encoding="utf-8",
    )

    monkeypatch.setattr(remediation.subprocess, "run", successful_process)

    code, report = run_remediation(
        tmp_path,
        approved=True,
        diagnosis_report=diagnosis_path,
        expected_source_sha="b" * 40,
        expected_source_branch="feature/demo",
    )

    assert code == 0
    assert report["applicability"]["diagnosis"]["source_sha"] == "b" * 40
    assert report["applicability"]["diagnosis"]["source_branch"] == "feature/demo"


def test_known_issue_transform_preserves_crlf_line_endings():
    crlf_source = KNOWN_ISSUE_TEST.replace("\n", "\r\n")

    transformed, state = remediation._transform_known_issue_test(crlf_source)

    assert state == "updated"
    assert "\r\n" in transformed
    assert "\n" not in transformed.replace("\r\n", "")
