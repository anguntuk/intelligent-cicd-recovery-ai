
"""Context-aware, fail-closed remediation for the frontend title mismatch demo.

The recipe deliberately does not pin complete-file SHA-256 values. It validates
specific semantic preconditions, applies narrowly-scoped transformations, runs
an allowlisted validation suite, and rolls back every modified file on failure.
It never commits, pushes, or triggers a GitHub Actions rerun.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

RECIPE_ID = "frontend_title_mismatch_v2"
OLD_TITLE = "CI/CD Recovery Demo"
NEW_TITLE = "CI/CD Recovery Dashboard"

# Commands are fixed in code. No command string is read from YAML, logs, or user input.
VALIDATIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("npm test", ("npm", "test")),
    ("npm run build", ("npm", "run", "build")),
)

RELATIVE_FILES = (
    "frontend/src/index.html",
    "frontend/test/app.test.js",
    "frontend/test/known-issue-demo.test.js",
)

_APP_TEST_OLD_ASSERTION = re.compile(
    r"(?P<prefix>assert\.match\(\s*page\s*,\s*/<title>)"
    r"CI\\/CD Recovery Demo"
    r"(?P<suffix><\\/title>/\s*\);)",
    re.MULTILINE,
)
_APP_TEST_NEW_ASSERTION = re.compile(
    r"assert\.match\(\s*page\s*,\s*/<title>CI\\/CD Recovery Dashboard<\\/title>/\s*\);",
    re.MULTILINE,
)

_KNOWN_ISSUE_STUB = re.compile(
    r"(?m)^(?P<indent>[ \t]*)const actualTitle = ['\"]CI/CD Recovery Demo['\"];[ \t]*\r?\n"
    r"[ \t]*const expectedTitle = ['\"]CI/CD Recovery Dashboard['\"];[ \t]*\r?\n"
    r"[ \t]*\r?\n"
    r"[ \t]*assert\.equal\([ \t]*\r?\n"
    r"[ \t]+actualTitle,[ \t]*\r?\n"
    r"[ \t]+expectedTitle,[ \t]*\r?\n"
    r"[ \t]+['\"]Frontend title does not match the expected product title['\"][ \t]*\r?\n"
    r"[ \t]*\);"
)
_KNOWN_ISSUE_LIVE_ASSERTION = re.compile(
    r"const actualTitle\s*=\s*page\.match\(/<title>\(\.\*\?\)<\\/title>/\)\?\.\[1\]"
)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_file(root: Path, relative: str) -> Path:
    """Resolve a project file while rejecting symlinks and path escapes."""
    candidate = root / relative
    if candidate.is_symlink():
        raise ValueError(f"Symlink targets are not supported by this recipe: {relative}")
    resolved = candidate.resolve(strict=False)
    if not resolved.is_relative_to(root):
        raise ValueError(f"Path escapes project root: {relative}")
    if not candidate.is_file():
        raise FileNotFoundError(f"Required file is missing: {relative}")
    return candidate


def _read_utf8(path: Path, relative: str) -> str:
    try:
        return path.read_bytes().decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{relative} is not valid UTF-8; recipe is not applicable") from exc


def _transform_html(source: str) -> tuple[str, str]:
    titles = list(
        re.finditer(
            r"<title>\s*(.*?)\s*</title>",
            source,
            flags=re.IGNORECASE | re.DOTALL,
        )
    )
    if len(titles) != 1:
        raise ValueError(
            f"frontend/src/index.html must contain exactly one title element; found {len(titles)}"
        )

    current = titles[0].group(1).strip()
    if current == NEW_TITLE:
        return source, "already_correct"

    if current != OLD_TITLE:
        raise ValueError(
            "frontend/src/index.html has an unsupported title value "
            f"{current!r}; expected {OLD_TITLE!r} or {NEW_TITLE!r}"
        )

    updated, count = re.subn(
        r"(<title>\s*)CI/CD Recovery Demo(\s*</title>)",
        rf"\g<1>{NEW_TITLE}\g<2>",
        source,
        count=1,
        flags=re.IGNORECASE,
    )
    if count != 1:
        raise ValueError("Could not safely transform the HTML title")

    return updated, "updated"


def _transform_app_test(source: str) -> tuple[str, str]:
    old_matches = list(_APP_TEST_OLD_ASSERTION.finditer(source))

    if len(old_matches) == 1:
        regex_safe_title = NEW_TITLE.replace("/", r"\/")
        updated = _APP_TEST_OLD_ASSERTION.sub(
            lambda match: (
                f"{match.group('prefix')}"
                f"{regex_safe_title}"
                f"{match.group('suffix')}"
            ),
            source,
            count=1,
        )
        return updated, "updated"

    if len(old_matches) > 1:
        raise ValueError(
            "frontend/test/app.test.js has multiple matching title assertions"
        )

    if len(list(_APP_TEST_NEW_ASSERTION.finditer(source))) == 1:
        return source, "already_correct"

    raise ValueError(
        "frontend/test/app.test.js does not contain the supported title assertion; "
        "no automatic edit is safe"
    )


def _transform_known_issue_test(source: str) -> tuple[str, str]:
    old_matches = list(_KNOWN_ISSUE_STUB.finditer(source))

    if len(old_matches) == 1:
        indent = old_matches[0].group("indent")
        newline = "\r\n" if "\r\n" in source else "\n"

        replacement = (
            f"{indent}const page = fs.readFileSync(\n"
            f"{indent}  path.join(__dirname, '..', 'src', 'index.html'),\n"
            f"{indent}  'utf8'\n"
            f"{indent});\n"
            f"{indent}const actualTitle = page.match(/<title>(.*?)<\\/title>/)?.[1] ?? null;\n"
            f"{indent}const expectedTitle = '{NEW_TITLE}';\n\n"
            f"{indent}assert.equal(\n"
            f"{indent}  actualTitle,\n"
            f"{indent}  expectedTitle,\n"
            f"{indent}  'Frontend title does not match the expected product title'\n"
            f"{indent});"
        )
        replacement = replacement.replace("\n", newline)
        updated = _KNOWN_ISSUE_STUB.sub(
            lambda _match: replacement,
            source,
            count=1,
        )

        if "require('node:fs')" not in updated:
            anchor = "const assert = require('node:assert/strict');"
            if updated.count(anchor) != 1:
                raise ValueError("Cannot safely add fs import to known-issue test")
            updated = updated.replace(
                anchor,
                anchor + newline + "const fs = require('node:fs');",
                1,
            )

        if "require('node:path')" not in updated:
            fs_anchor = "const fs = require('node:fs');"
            if updated.count(fs_anchor) != 1:
                raise ValueError("Cannot safely add path import to known-issue test")
            updated = updated.replace(
                fs_anchor,
                fs_anchor + newline + "const path = require('node:path');",
                1,
            )

        return updated, "updated"

    if len(old_matches) > 1:
        raise ValueError(
            "known-issue test contains multiple hard-coded demo assertions"
        )

    if (
        _KNOWN_ISSUE_LIVE_ASSERTION.search(source)
        and f"const expectedTitle = '{NEW_TITLE}';" in source
        and "Frontend title does not match the expected product title" in source
        and "require('node:fs')" in source
        and "require('node:path')" in source
    ):
        return source, "already_correct"

    raise ValueError(
        "frontend/test/known-issue-demo.test.js is not the supported hard-coded "
        "title-mismatch test; no automatic edit is safe"
    )


def _atomic_write(path: Path, content: bytes) -> None:
    """Write one file atomically in its existing directory."""
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=path.parent,
    )
    temporary = Path(temporary_name)

    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())

        os.replace(temporary, path)

    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _write_audit(root: Path, audit: dict[str, Any]) -> Path:
    reports = root / "reports"
    reports.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = reports / f"remediation-{RECIPE_ID}-{stamp}.json"

    suffix = 1
    while destination.exists():
        destination = reports / f"remediation-{RECIPE_ID}-{stamp}-{suffix}.json"
        suffix += 1

    _atomic_write(
        destination,
        (json.dumps(audit, indent=2) + "\n").encode("utf-8"),
    )
    return destination


def _validate_diagnosis_report(
    report_path: Path,
    *,
    expected_source_sha: str | None = None,
    expected_source_branch: str | None = None,
) -> dict[str, str]:
    """Require evidence for this exact title mismatch, not just any unit-test failure."""
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read diagnosis report: {exc}") from exc

    analyses = report.get("analyses")
    if not isinstance(analyses, list) or len(analyses) != 1:
        raise ValueError(
            "Diagnosis report must contain exactly one analyzed failed job"
        )

    analysis = analyses[0]
    diagnosis = analysis.get("diagnosis", {})

    if diagnosis.get("category") != "unit_test_failure":
        raise ValueError("Diagnosis category is not unit_test_failure")

    if "unit_test_failure" not in (diagnosis.get("matched_rule_ids") or []):
        raise ValueError(
            "Diagnosis report does not identify the unit_test_failure rule"
        )

    excerpt = analysis.get("sanitized_log_excerpt")
    if not isinstance(excerpt, str):
        raise ValueError(
            "Diagnosis report does not contain a sanitized log excerpt"
        )

    if OLD_TITLE not in excerpt or NEW_TITLE not in excerpt:
        raise ValueError(
            "Failure evidence does not contain both the actual and expected title "
            f"values ({OLD_TITLE!r} and {NEW_TITLE!r}); this recipe is not applicable "
            "to a generic test failure"
        )

    run = report.get("workflow_run", {})

    if expected_source_sha and run.get("head_sha") != expected_source_sha:
        raise ValueError(
            "Diagnosis report source SHA does not match the checked-out source commit"
        )

    if expected_source_branch and run.get("head_branch") != expected_source_branch:
        raise ValueError(
            "Diagnosis report source branch does not match the checked-out source branch"
        )

    return {
        "category": "unit_test_failure",
        "signature": "frontend_title_mismatch",
        "source_sha": str(run.get("head_sha") or ""),
        "source_branch": str(run.get("head_branch") or ""),
    }


def _validate_project(root: Path) -> None:
    if not (root / "pyproject.toml").is_file():
        raise ValueError(f"Not a project root (pyproject.toml missing): {root}")

    package_json = root / "frontend/package.json"
    if not package_json.is_file():
        raise ValueError(
            "Not a supported frontend project (frontend/package.json missing)"
        )

    try:
        package = json.loads(package_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read frontend/package.json: {exc}") from exc

    scripts = package.get("scripts", {})
    if (
        not isinstance(scripts, dict)
        or not scripts.get("test")
        or not scripts.get("build")
    ):
        raise ValueError(
            "Frontend package must define both 'test' and 'build' scripts"
        )


def _prepare_changes(
    root: Path,
) -> tuple[dict[str, bytes], dict[str, bytes], dict[str, str]]:
    paths = {
        relative: _safe_file(root, relative)
        for relative in RELATIVE_FILES
    }

    original = {
        relative: paths[relative].read_bytes()
        for relative in RELATIVE_FILES
    }

    decoded = {
        relative: _read_utf8(paths[relative], relative)
        for relative in RELATIVE_FILES
    }

    html, html_status = _transform_html(decoded[RELATIVE_FILES[0]])
    app_test, app_status = _transform_app_test(decoded[RELATIVE_FILES[1]])
    known_test, known_status = _transform_known_issue_test(
        decoded[RELATIVE_FILES[2]]
    )

    transformed_text = (html, app_test, known_test)
    changes = {
        relative: text.encode("utf-8")
        for relative, text in zip(
            RELATIVE_FILES,
            transformed_text,
            strict=True,
        )
    }

    states = dict(
        zip(
            RELATIVE_FILES,
            (html_status, app_status, known_status),
            strict=True,
        )
    )

    return original, changes, states


def run_remediation(
    project_root: Path,
    *,
    approved: bool,
    diagnosis_report: Path | None = None,
    expected_source_sha: str | None = None,
    expected_source_branch: str | None = None,
) -> tuple[int, dict[str, Any]]:
    root = project_root.resolve()

    if not approved:
        return 2, {
            "status": "approval_required",
            "message": (
                "Pass --approve only after the remediation proposal has been reviewed."
            ),
        }

    # Reject an invalid project root explicitly before other checks.
    if (
        not (root / "pyproject.toml").is_file()
        or not (root / "frontend/package.json").is_file()
    ):
        return 2, {
            "schema_version": 2,
            "recipe_id": RECIPE_ID,
            "status": "invalid_project",
            "message": (
                "Project root must contain pyproject.toml "
                "and frontend/package.json"
            ),
            "files_changed": False,
            "commit_performed": False,
            "push_performed": False,
            "ci_retry_triggered": False,
        }
    
    try:
        _validate_project(root)

        if (
            os.environ.get("GITHUB_ACTIONS", "").lower() == "true"
            and diagnosis_report is None
        ):
            raise ValueError(
                "GitHub execution requires --diagnosis-report evidence"
            )

        diagnosis_context = None
        if diagnosis_report is not None:
            diagnosis_context = _validate_diagnosis_report(
                diagnosis_report,
                expected_source_sha=expected_source_sha,
                expected_source_branch=expected_source_branch,
            )

        originals, proposed, preconditions = _prepare_changes(root)

    except (OSError, ValueError) as exc:
        return 2, {
            "schema_version": 2,
            "recipe_id": RECIPE_ID,
            "status": "not_applicable",
            "message": str(exc),
            "files_changed": False,
            "commit_performed": False,
            "push_performed": False,
            "ci_retry_triggered": False,
        }

    changed = [
        relative
        for relative in RELATIVE_FILES
        if originals[relative] != proposed[relative]
    ]

    audit: dict[str, Any] = {
        "schema_version": 2,
        "recipe_id": RECIPE_ID,
        "started_at": _now(),
        "approval": {
            "type": "prior_github_environment_approval_or_explicit_local_cli_flag",
            "received": True,
        },
        "applicability": {
            "strategy": (
                "diagnosis_signature_plus_semantic_preconditions_and_"
                "bounded_transformations"
            ),
            "preconditions": preconditions,
            "whole_file_hashes_used_as_preconditions": False,
            "diagnosis": diagnosis_context,
        },
        "changed_files": changed,
        "before_sha256": {
            relative: _sha256(value)
            for relative, value in originals.items()
        },
        "validation": [],
        "commit_performed": False,
        "push_performed": False,
        "ci_retry_triggered": False,
    }

    written: list[str] = []

    try:
        for relative in changed:
            _atomic_write(root / relative, proposed[relative])
            written.append(relative)

        frontend = root / "frontend"

        for label, command in VALIDATIONS:
            try:
                result = subprocess.run(
                    list(command),
                    cwd=frontend,
                    text=True,
                    capture_output=True,
                    check=False,
                    timeout=600,
                    shell=False,
                )

                item: dict[str, Any] = {
                    "command": label,
                    "exit_code": result.returncode,
                    "stdout": result.stdout[-20000:],
                    "stderr": result.stderr[-20000:],
                    "passed": result.returncode == 0,
                }

            except subprocess.TimeoutExpired as exc:
                item = {
                    "command": label,
                    "passed": False,
                    "error": f"Command timed out after {exc.timeout} seconds",
                }

            except OSError as exc:
                item = {
                    "command": label,
                    "passed": False,
                    "error": str(exc),
                }

            audit["validation"].append(item)

            if not item["passed"]:
                raise RuntimeError(f"Validation failed: {label}")

        audit["status"] = "validated_pending_commit_approval"
        audit["message"] = (
            "Context-aware recipe applied and all validations passed. "
            "No commit, push, or CI retry was performed."
        )
        audit["after_sha256"] = {
            relative: _sha256((root / relative).read_bytes())
            for relative in RELATIVE_FILES
        }

        return_code = 0

    except Exception as exc:
        rollback_errors: list[str] = []

        for relative in reversed(written):
            try:
                _atomic_write(root / relative, originals[relative])
            except OSError as rollback_exc:
                rollback_errors.append(f"{relative}: {rollback_exc}")

        audit["status"] = (
            "validation_failed_rolled_back"
            if audit["validation"]
            else "execution_failed_rolled_back"
        )
        audit["message"] = str(exc)
        audit["rollback_performed"] = not rollback_errors
        audit["rollback_errors"] = rollback_errors
        audit["after_rollback_sha256"] = {
            relative: _sha256((root / relative).read_bytes())
            for relative in RELATIVE_FILES
            if (root / relative).is_file()
        }

        return_code = 1 if not rollback_errors else 3

    audit["finished_at"] = _now()

    try:
        audit_path = _write_audit(root, audit)
        audit["audit_report"] = str(audit_path)

    except OSError as exc:
        audit["audit_write_error"] = str(exc)

        if return_code == 0:
            return_code = 3
            audit["status"] = "validated_audit_write_failed"
            audit["message"] += (
                " Audit report could not be persisted; treat this run as failed."
            )

    return return_code, audit


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Apply a context-aware, allowlisted frontend title remediation recipe."
        )
    )

    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path.cwd(),
    )
    parser.add_argument(
        "--approve",
        action="store_true",
        help="Confirm the prior human approval for this specific recipe execution.",
    )
    parser.add_argument(
        "--diagnosis-report",
        type=Path,
        help=(
            "Diagnosis JSON artifact; required when running inside GitHub Actions."
        ),
    )
    parser.add_argument(
        "--expected-source-sha",
        help="Expected failed CI source commit SHA.",
    )
    parser.add_argument(
        "--expected-source-branch",
        help="Expected failed CI source branch.",
    )

    args = parser.parse_args()

    code, report = run_remediation(
        args.project_root,
        approved=args.approve,
        diagnosis_report=args.diagnosis_report,
        expected_source_sha=args.expected_source_sha,
        expected_source_branch=args.expected_source_branch,
    )

    print(json.dumps(report, indent=2))

    if code == 0:
        print(
            "\nNext gate: review the validated diff; "
            "commit/push/CI retry remain disabled."
        )
    elif report.get("status", "").endswith("rolled_back"):
        print(
            "\nRemediation stopped. Original files were restored; "
            "no commit or retry was performed.",
            file=sys.stderr,
        )

    return code


if __name__ == "__main__":
    raise SystemExit(main())
