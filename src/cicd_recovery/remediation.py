"""Approved, deterministic local remediation for the unit_test_failure demo case.

This module is deliberately narrow: it recognizes one exact baseline and applies one
reviewed recipe. It does not execute arbitrary YAML commands, use a shell, access GitHub,
commit, push, or trigger a workflow retry.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

RECIPE_ID = "frontend_title_mismatch_v1"
EXPECTED_FILES = {
    "frontend/src/index.html": """<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>CI/CD Recovery Demo</title>
  </head>
  <body>
    <main>
      <h1>CI/CD Recovery Demo Frontend</h1>
      <p>This tiny static page gives the Node.js pipeline a reproducible build target.</p>
    </main>
  </body>
</html>
""",
    "frontend/test/app.test.js": """const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

test('frontend entry page exists and has a title', () => {
  const page = fs.readFileSync(path.join(__dirname, '..', 'src', 'index.html'), 'utf8');
  assert.match(page, /<title>CI\\/CD Recovery Demo<\\/title>/);
});
""",
    "frontend/test/known-issue-demo.test.js": """const test = require('node:test');
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
""",
}

FIXED_FILES = {
    "frontend/src/index.html": EXPECTED_FILES["frontend/src/index.html"].replace(
        "<title>CI/CD Recovery Demo</title>", "<title>CI/CD Recovery Dashboard</title>"
    ),
    "frontend/test/app.test.js": EXPECTED_FILES["frontend/test/app.test.js"].replace(
        "Recovery Demo", "Recovery Dashboard"
    ),
    "frontend/test/known-issue-demo.test.js": """const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

test('frontend title matches the approved product title', () => {
  const page = fs.readFileSync(path.join(__dirname, '..', 'src', 'index.html'), 'utf8');
  assert.match(page, /<title>CI\\/CD Recovery Dashboard<\\/title>/);
});
""",
}

VALIDATIONS = (("npm test", ["npm", "test"]), ("npm run build", ["npm", "run", "build"]))


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _write_audit(root: Path, audit: dict[str, Any]) -> Path:
    reports = root / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = reports / f"remediation-{RECIPE_ID}-{stamp}.json"
    path.write_text(json.dumps(audit, indent=2) + "\n", encoding="utf-8")
    return path


def run_remediation(project_root: Path, *, approved: bool) -> tuple[int, dict[str, Any]]:
    root = project_root.resolve()
    if not approved:
        return 2, {"status": "approval_required", "message": "Pass --approve after reviewing the proposed recipe."}

    if not (root / "pyproject.toml").is_file() or not (root / "frontend/package.json").is_file():
        return 2, {"status": "invalid_project", "message": f"Not a project root: {root}"}

    originals: dict[str, str] = {}
    for rel, expected in EXPECTED_FILES.items():
        path = root / rel
        if not path.is_file():
            return 2, {"status": "precondition_failed", "message": f"Required file missing: {rel}"}
        current = path.read_text(encoding="utf-8")
        if current.rstrip("\n") != expected.rstrip("\n"):
            return 2, {
                "status": "precondition_failed",
                "message": f"Exact baseline mismatch in {rel}; no files were changed. Recipe is fail-closed.",
                "file": rel,
                "expected_sha256": _sha(expected),
                "actual_sha256": _sha(current),
            }
        originals[rel] = current

    audit: dict[str, Any] = {
        "schema_version": 1,
        "recipe_id": RECIPE_ID,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "approval": {"type": "explicit_local_cli_flag", "received": True},
        "changed_files": list(FIXED_FILES),
        "before_sha256": {rel: _sha(value) for rel, value in originals.items()},
        "validation": [],
        "commit_performed": False,
        "push_performed": False,
        "ci_retry_triggered": False,
    }

    try:
        for rel, fixed in FIXED_FILES.items():
            (root / rel).write_text(fixed, encoding="utf-8")

        frontend = root / "frontend"
        for label, argv in VALIDATIONS:
            try:
                result = subprocess.run(
                    argv, cwd=frontend, text=True, capture_output=True, check=False,
                    timeout=600, shell=False,
                )
                item = {
                    "command": label,
                    "exit_code": result.returncode,
                    "stdout": result.stdout[-20000:],
                    "stderr": result.stderr[-20000:],
                    "passed": result.returncode == 0,
                }
            except (OSError, subprocess.TimeoutExpired) as exc:
                item = {"command": label, "passed": False, "error": str(exc)}
            audit["validation"].append(item)
            if not item["passed"]:
                raise RuntimeError(f"Validation failed: {label}")

        audit["status"] = "validated_pending_commit_approval"
        audit["message"] = "Recipe applied and all validations passed. No commit, push, or CI retry was performed."
        audit["after_sha256"] = {rel: _sha((root / rel).read_text(encoding="utf-8")) for rel in FIXED_FILES}
        return_code = 0
    except Exception as exc:
        for rel, original in originals.items():
            (root / rel).write_text(original, encoding="utf-8")
        audit["status"] = "validation_failed_rolled_back" if audit["validation"] else "execution_failed_rolled_back"
        audit["message"] = str(exc)
        audit["rollback_performed"] = True
        return_code = 1

    audit["finished_at"] = datetime.now(timezone.utc).isoformat()
    audit_path = _write_audit(root, audit)
    audit["audit_report"] = str(audit_path)
    return return_code, audit


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the exact-match unit-test remediation recipe locally.")
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--approve", action="store_true", help="Explicitly approve this local recipe execution.")
    args = parser.parse_args()
    code, report = run_remediation(args.project_root, approved=args.approve)
    print(json.dumps(report, indent=2))
    if code == 0:
        print("\nNext gate: review the validated diff, then implement a separate GitHub commit-approval workflow.")
    elif report.get("status", "").endswith("rolled_back"):
        print("\nRemediation stopped. Original files were restored; no commit or retry was performed.", file=sys.stderr)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
