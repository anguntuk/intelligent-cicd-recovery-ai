# Intelligent CI/CD Failure Recovery

A Python-based, rule-driven CI/CD failure diagnosis and controlled remediation prototype for GitHub Actions. The current implementation uses a Node.js frontend as its demonstration application and performs deterministic recovery without an AI model.

The workflow is designed around two separate human approval gates, isolated remediation, validation, an auditable patch handoff, an approved commit/push, and an explicitly dispatched post-remediation CI run.

> **Current implementation status:** pre-merge feature-branch testing mode. The active diagnostic workflow is triggered by pushes to `feature/**` and checks out the controller from the same feature-branch commit. This mode is intended for controlled testing on trusted branches. It is not the final production trust boundary for untrusted contributor branches.

---

## Contents

1. [Goals and current scope](#goals-and-current-scope)
2. [Architecture](#architecture)
3. [End-to-end workflow](#end-to-end-workflow)
4. [Repository layout](#repository-layout)
5. [Requirements](#requirements)
6. [Local development setup](#local-development-setup)
7. [Run the local diagnosis and tests](#run-the-local-diagnosis-and-tests)
8. [Controlled frontend failure scenario](#controlled-frontend-failure-scenario)
9. [GitHub Actions configuration](#github-actions-configuration)
10. [Human approval gates and deadlines](#human-approval-gates-and-deadlines)
11. [Remediation recipe and safety controls](#remediation-recipe-and-safety-controls)
12. [Patch, audit, and artifact handling](#patch-audit-and-artifact-handling)
13. [Commit, push, and post-remediation CI](#commit-push-and-post-remediation-ci)
14. [Permissions and security model](#permissions-and-security-model)
15. [Troubleshooting](#troubleshooting)
16. [Known limitations and next work](#known-limitations-and-next-work)

---

## Goals and current scope

The project aims to:

- Identify common CI/CD failures from GitHub Actions run metadata, failed jobs, failed steps, and logs.
- Redact common credential patterns before producing diagnosis evidence.
- Classify failures using deterministic rules in `config/rules.yaml`.
- Evaluate whether a failure is eligible for a controlled remediation recipe.
- Require human approval before executing a remediation.
- Apply narrowly scoped source changes in a temporary GitHub Actions workspace.
- Validate the changes before creating any commit.
- Preserve the validated patch and audit evidence for human review.
- Require a second, independent human approval before commit and push.
- Explicitly dispatch the frontend CI workflow after the remediation push and record its result.

### What is implemented today

- Deterministic rule-based diagnosis; AI is disabled.
- A policy evaluator that identifies remediation eligibility and approval requirements.
- One deterministic executor recipe: `frontend_title_mismatch_v2`.
- Two GitHub Environment approval gates.
- A configurable approval deadline enforced by watchdog jobs.
- Semantic precondition checks, bounded transformations, atomic file writes, validation, and rollback on validation/execution failure.
- A patch and audit handoff with SHA-256 integrity checks.
- An approved commit/push and post-push `workflow_dispatch` of `Frontend CI`.

### What is not implemented

- No Copilot, Bedrock, LLM, or other AI diagnosis/recommendation path.
- No generic source-code repair for every detected category.
- No automatic retry loop or automatic second remediation after post-push CI failure.
- No automatic rollback of a commit that has already been pushed if post-push CI fails.
- No deployment to an application environment.
- No durable cross-run incident/attempt database. The policy `max_attempts` value is metadata/policy input and is not a durable global retry counter by itself.

Only the specifically supported frontend title-mismatch case is eligible for the current executor. Other detected categories receive diagnosis and recommendations but do not receive automatic source modifications.

---

## Architecture

```text
Developer pushes to feature/*
            |
            +------------------------------+
            |                              |
            v                              v
       Frontend CI                Frontend Failure Diagnostics
       (Node.js)                  (Python controller)
            |                              |
            |                         Wait for the exact
            |                         Frontend CI run/SHA
            |                              |
            |                         Read run metadata,
            |                         failed jobs and logs
            |                              |
            |                         Redact and classify
            |                         with rules.yaml
            |                              |
            |                         Is supported recipe?
            |                              |
            |                    First human approval gate
            |                              |
            |                         Deterministic recipe
            |                         in isolated workspace
            |                              |
            |                         npm test + npm run build
            |                              |
            |                         Roll back on failure
            |                              |
            |                         Generate audit + patch
            |                              |
            |                         Patch review job
            |                              |
            |                    Second human approval gate
            |                              |
            |                         Verify source SHA,
            |                         patch and file hashes
            |                              |
            |                         Commit + push using
            |                         GITHUB_TOKEN
            |                              |
            |                         Explicit workflow_dispatch
            |                              |
            +<-----------------------------+
                       Frontend CI for the exact
                       remediation commit SHA
```

The workflows are separate:

- `.github/workflows/frontend-ci.yml` runs the application CI pipeline.
- `.github/workflows/debug-frontend.yml` runs diagnosis, approval, remediation, review, commit/push, and post-push verification.

The Python controller lives under `src/cicd_recovery/`. Rules are externalized in `config/rules.yaml`; remediation logic is intentionally implemented in code rather than accepting arbitrary shell commands from rules or log content.

---

## End-to-end workflow

The current `debug-frontend.yml` is in **pre-merge test mode** and starts on `push` to `feature/**`.

### 1. Feature branch push

A push to a branch matching `feature/**` starts both workflows:

- `Frontend CI`
- `Frontend Failure Diagnostics`

The diagnostic workflow waits for the `Frontend CI` run matching the same branch and exact pushed commit SHA. It checks the workflow name, repository, branch, event, SHA, completion status, and run conclusion.

- If Frontend CI succeeds, diagnosis and remediation are skipped.
- If Frontend CI fails, the diagnostic job continues.
- Other conclusions do not qualify for this remediation flow.

### 2. Diagnosis

The diagnostic job checks out the controller at the exact feature-branch commit that triggered the workflow. It installs the Python package and runs the diagnosis CLI against the failed Frontend CI run.

The controller:

1. Retrieves workflow-run metadata.
2. Confirms the target run is from a `feature/` branch, belongs to the expected `Frontend CI` workflow, and has failed.
3. Retrieves failed job and failed-step information.
4. Downloads failed-step logs where possible and falls back to the job log if required.
5. Redacts common secret/token patterns.
6. Applies the ordered deterministic rules from `config/rules.yaml`.
7. Creates a JSON report and GitHub Actions job summary.

For the current supported recipe, the workflow additionally checks that the diagnosis contains exactly one analysis, the `unit_test_failure` category/rule, an eligible approval-required policy decision, and both title strings in the sanitized log excerpt:

- `CI/CD Recovery Demo`
- `CI/CD Recovery Dashboard`

This signature check prevents a generic unit-test failure from being passed to the title-mismatch executor.

### 3. First human approval

If the failure is supported and policy requires approval, GitHub requests approval through the `remediation-approval` environment.

The first approval authorizes applying the proposed deterministic recipe in the temporary workspace. It does **not** authorize a commit or push.

### 4. Deterministic remediation and validation

After the first approval is recorded and verified, the workflow:

1. Downloads the diagnosis report.
2. Checks out the controller at the recorded controller SHA.
3. Checks out the exact failed source commit into a separate `source` directory.
4. Verifies report metadata against repository, run ID, source SHA, and branch.
5. Installs the frontend dependencies.
6. Runs the allowlisted remediation recipe.
7. Runs `npm test` and `npm run build`.
8. Rolls back modified files if the remediation or validation fails.
9. Writes an audit report.

No commit or push happens in this job.

### 5. Patch review

When validation succeeds, the workflow produces a handoff containing:

- `remediation.patch`
- `audit.json`
- `manifest.json`
- `patch-stat.txt`

The manifest binds the patch and audit to the repository, source run ID, source branch, source SHA, controller SHA, changed-file list, and SHA-256 hashes.

The review job downloads the handoff, validates the hashes and audit metadata, and adds the proposed diff and validation outcome to its GitHub Actions job summary.

### 6. Second, independent human approval

After patch review succeeds, GitHub requests a **new approval** through `remediation-commit-approval`.

This is a separate environment gate from `remediation-approval`. The first approval cannot satisfy the second gate. The second watchdog also checks that the first approval is present in GitHub's approval history before it accepts the second approval.

The second approval authorizes committing and pushing the validated patch to the originating feature branch.

### 7. Commit and push

After the second approval is verified, the final job:

1. Downloads the exact reviewed handoff artifact.
2. Checks out the originating feature branch.
3. Confirms the branch still points to the original source SHA. If the branch has advanced, the workflow stops and a fresh diagnosis/approval cycle is required.
4. Verifies manifest and audit hashes.
5. Verifies validation evidence and the allowlisted changed-file list.
6. Applies the reviewed patch.
7. Checks the resulting file hashes against the remediation audit.
8. Stages only the audited files.
9. Creates a commit and pushes with the workflow's `GITHUB_TOKEN`.

The workflow does not force-push and does not call the workflow rerun API.

### 8. Post-remediation Frontend CI

A push made with `GITHUB_TOKEN` does not automatically trigger another `push` workflow. To verify the new commit, the final job explicitly dispatches the existing `Frontend CI` workflow with:

- `ref`: the originating feature branch;
- the `workflow_dispatch` event;
- the remediation commit SHA recorded by the commit step.

The workflow discovers the exact dispatched run. It supports both a dispatch response containing run details and an empty successful response, in which case it queries workflow runs by workflow ID, branch, event, exact commit SHA, and dispatch start time.

It then waits for that run to complete and verifies the returned workflow name, repository, branch, event, workflow ID, and commit SHA. The final status is recorded in `commit-result.json`.

Possible final statuses include:

- `pushed_ci_success`
- `pushed_ci_failure`
- `pushed_ci_dispatch_failed`

A post-push CI failure is reported. The commit remains pushed; the current workflow does not automatically revert it or start another repair.

---

## Repository layout

The primary project layout is:

```text
intelligent-cicd-recovery-ai/
├── .github/
│   └── workflows/
│       ├── frontend-ci.yml
│       └── debug-frontend.yml
├── config/
│   └── rules.yaml
├── fixtures/
│   └── logs/
│       ├── build_failure.log
│       ├── dependency_conflict.log
│       ├── lint_failure.log
│       ├── out_of_memory.log
│       ├── registry_auth.log
│       ├── registry_timeout.log
│       ├── security_gate_failure.log
│       ├── typecheck_failure.log
│       ├── unit_test_failure.log
│       └── unknown_failure.log
├── frontend/
│   ├── package.json
│   ├── package-lock.json
│   ├── scripts/
│   │   ├── build.js
│   │   ├── lint-check.js
│   │   └── typecheck-check.js
│   ├── src/
│   │   └── index.html
│   └── test/
│       ├── app.test.js
│       └── known-issue-demo.test.js
├── reports/
│   └── .gitkeep
├── src/
│   └── cicd_recovery/
│       ├── __init__.py
│       ├── analyzer.py
│       ├── cli.py
│       ├── github_api.py
│       ├── policy.py
│       ├── redaction.py
│       ├── rules.py
│       └── remediation.py
├── tests/
│   ├── test_analyzer.py
│   ├── test_github_api.py
│   ├── test_policy.py
│   ├── test_redaction.py
│   ├── test_remediation_reporting.py
│   ├── test_rules.py
│   └── test_run_scope.py
├── .env.example
├── .gitignore
├── pyproject.toml
├── README.md
└── requirements.txt
```

The project may contain additional tests or implementation files as the prototype evolves. Keep generated output such as `.venv/`, `__pycache__/`, `.pytest_cache/`, `frontend/node_modules/`, `frontend/dist/`, and generated report JSON out of Git.

---

## Requirements

### Local development

- Python 3.11 or newer (`pyproject.toml` requires Python `>=3.11`).
- Node.js 26 to match the current `Frontend CI` workflow.
- npm compatible with the installed Node.js version.
- Git.
- GitHub CLI (`gh`) if using the GitHub API commands locally.

### GitHub Actions

The current workflows use GitHub-hosted Ubuntu runners and these actions:

- `actions/checkout@v7`
- `actions/setup-python@v7`
- `actions/setup-node@v7`
- `actions/upload-artifact@v7`
- `actions/download-artifact@v8`

The Python controller workflow uses Python 3.13. The frontend CI workflow uses Node.js 26.

---

## Local development setup

From the repository root:

### 1. Create and activate a Python virtual environment

Linux/macOS:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Windows PowerShell:

```powershell
py -3 -m venv .venv
.venv\Scripts\Activate.ps1
```

### 2. Install the project and development dependencies

```bash
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

The project dependencies are declared in `pyproject.toml`. The optional `dev` group installs pytest, pytest-cov, and Ruff.

### 3. Install frontend dependencies

```bash
cd frontend
npm ci
cd ..
```

Use the committed `package-lock.json`; prefer `npm ci` over `npm install` for reproducible CI/test setup.

---

## Run the local diagnosis and tests

### Run Python unit tests

```bash
python -m pytest -v
```

### Run lint and source-contract checks

The demo frontend exposes these scripts:

```bash
cd frontend
npm run lint
npm run typecheck
npm test
npm run build
cd ..
```

The current GitHub `Frontend CI` workflow executes `npm test` and `npm run build`. The lint and typecheck scripts are available for local checks, but they are not currently steps in that workflow.

### Diagnose a local log fixture

No GitHub token is required for local fixture diagnosis:

```bash
python -m cicd_recovery.cli diagnose-fixture \
  --file fixtures/logs/unit_test_failure.log \
  --output reports/local-unit-test-diagnosis.json
```

Other fixture files are available under `fixtures/logs/`.

A fixture diagnosis is a policy preview only. It does not edit files, run validation commands, request approvals, commit, push, or trigger CI.

### List GitHub Actions runs (optional)

For local GitHub API access, set a `GH_TOKEN` environment variable. Use a fine-grained token scoped to the target repository with the minimum read permission required for Actions/run metadata and logs. Do not hard-code or commit the token.

Linux/macOS:

```bash
export GH_TOKEN="YOUR_TOKEN"
export GITHUB_API_VERSION="2026-03-10"
python -m cicd_recovery.cli list-runs --repo OWNER/REPOSITORY --limit 10
```

Windows PowerShell:

```powershell
$env:GH_TOKEN = "YOUR_TOKEN"
$env:GITHUB_API_VERSION = "2026-03-10"
python -m cicd_recovery.cli list-runs --repo OWNER/REPOSITORY --limit 10
```

### Diagnose a GitHub Actions run (optional)

```bash
python -m cicd_recovery.cli diagnose \
  --repo OWNER/REPOSITORY \
  --run-id RUN_ID \
  --require-feature-branch \
  --require-failure \
  --expected-workflow-name "Frontend CI" \
  --rules config/rules.yaml \
  --output-dir reports
```

The GitHub diagnosis command is read-only. It downloads workflow metadata and logs, applies redaction and rules, and writes a JSON report. It does not execute the remediation recipe.

### Run the deterministic remediation locally (use a disposable checkout)

The executor modifies the three supported frontend files. Run it only in a disposable branch/worktree or after ensuring the working tree is clean and backed up.

```bash
python -m cicd_recovery.remediation \
  --project-root . \
  --approve
```

`--approve` is an explicit local acknowledgement; it is not a substitute for the GitHub Environment approval controls in the hosted workflow. In GitHub Actions, the executor also requires a diagnosis report and expected source SHA/branch arguments.

The remediation executor runs `npm test` and `npm run build` in `frontend/`. Each validation subprocess currently has a 600-second safety timeout. This is an executor-level command safeguard, not a GitHub Actions job timeout.

---

## Controlled frontend failure scenario

The supported demonstration failure is a mismatch between the frontend HTML title and the expected product title.

The original failing state is:

- `frontend/src/index.html` has the title `CI/CD Recovery Demo`.
- `frontend/test/app.test.js` contains an assertion for the old title.
- `frontend/test/known-issue-demo.test.js` deliberately expects `CI/CD Recovery Dashboard` while its test stub reports the old title, so the test fails.

Run locally to confirm the failure:

```bash
cd frontend
npm test
```

The known-issue test should fail with an assertion mismatch. Do not delete, skip, or weaken the assertion to create a passing test.

### What `frontend_title_mismatch_v2` changes

The recipe makes these bounded changes:

1. `frontend/src/index.html`: changes the HTML title to `CI/CD Recovery Dashboard`.
2. `frontend/test/app.test.js`: updates the title assertion to expect `CI/CD Recovery Dashboard`.
3. `frontend/test/known-issue-demo.test.js`: replaces the hard-coded pseudo-actual title with a value read from the actual HTML file, while keeping the expected dashboard title and equality assertion.

After the recipe runs, the expected validation commands are:

```bash
cd frontend
npm test
npm run build
```

To repeat an end-to-end hosted test, restore the three files to the known failing state on a trusted `feature/**` branch, commit and push them, then observe the workflows. Avoid changing the diagnostic rules or remediation controller in the same test commit unless the test is specifically intended to validate those changes.

---

## GitHub Actions configuration

### `frontend-ci.yml`

The current Frontend CI workflow is named `Frontend CI` and has these triggers:

```yaml
on:
  push:
    branches:
      - main
      - "feature/**"
  pull_request:
    branches:
      - main
  workflow_dispatch:
```

It runs in `frontend/` and currently performs:

1. Checkout repository.
2. Setup Node.js 26.
3. `npm ci --no-audit --no-fund`.
4. `npm test`.
5. `npm run build`.
6. Upload `frontend/dist/` as the `frontend-dist` artifact.

The `workflow_dispatch` trigger must be present in the `Frontend CI` workflow on the repository's default branch for the post-remediation dispatch step to work. The dispatcher selects the originating feature branch as the `ref`, and the resulting run is verified against the exact remediation commit SHA.

### `debug-frontend.yml`

Current pre-merge test-mode trigger:

```yaml
on:
  push:
    branches:
      - "feature/**"
```

The diagnostic workflow waits for the Frontend CI run corresponding to the exact push SHA. It uses the current feature-branch commit for the controller checkout. After all approval gates and patch checks, it commits and pushes only to the originating feature branch.

### Repository environments

Create and configure both environments before testing the full flow:

1. `remediation-approval`
2. `remediation-commit-approval`

For **each** environment:

- Configure required reviewers.
- Enable Prevent self-review where available.
- Disable administrator bypass where supported by the repository plan/policy.
- Ensure the environment name matches the YAML exactly.

Use separate reviewer groups for the two gates where possible. Merely referencing an environment name in YAML is not sufficient; an environment without required reviewers does not provide the intended human approval gate. The watchdogs fail closed if the environment job starts but the expected GitHub approval-history record is missing.

### Approval timeout variable

The workflow reads this optional repository Actions variable:

```text
APPROVAL_TIMEOUT_MINUTES
```

- If unset, the workflow defaults to `30` minutes.
- Valid values are whole numbers from `1` through `350`.
- The first and second approval windows are separate. The second window starts after patch review succeeds; it does not reuse the first approval's timestamp.
- A watchdog monitors each environment approval record and cancels the workflow when the respective deadline expires or the approval is rejected.
- If an environment job starts without a matching approval-history record, the watchdog treats the gate as misconfigured and cancels the workflow after its 60-second detection window.

The workflow does not set explicit `timeout-minutes` values on the diagnosis, remediation, review, commit, or post-push CI jobs. GitHub's own hosted-runner and workflow platform limits still apply. The watchdog runners poll every 15 seconds and need `actions: write` permission to cancel an expired workflow run.

### Permissions

The workflow uses job-level permissions rather than granting write access to every job:

| Job purpose | Permissions |
|---|---|
| Diagnosis and run/log inspection | `contents: read`, `actions: read` |
| Approval jobs and clock jobs | `contents: read` |
| Approval watchdogs | `actions: write` to cancel the workflow on rejection/expiry/misconfiguration |
| Remediation and patch review | Read-only contents/actions access |
| Final commit, push, and workflow dispatch | `contents: write`, `actions: write` |

The `Frontend CI` application workflow has `contents: read` and does not need a GitHub App private key.

No `APP_CLIENT_ID`, `APP_PRIVATE_KEY`, or `APP_BOT_LOGIN` values are required in the current GITHUB_TOKEN-based implementation.

---

## Human approval gates and deadlines

There are two independent approval events in the workflow.

| Gate | Environment | What it authorizes | Deadline |
|---|---|---|---|
| First approval | `remediation-approval` | Apply the supported remediation in the temporary workspace and run validation | `APPROVAL_TIMEOUT_MINUTES` (default 30) |
| Second approval | `remediation-commit-approval` | Commit and push the already validated, reviewed patch | `APPROVAL_TIMEOUT_MINUTES` (default 30), measured separately |

The second approval is not automatically granted because the first approval passed. Before accepting the second environment's approval, its watchdog checks the GitHub approval history for both environments and requires the first environment's state to be `approved` and the second environment's state to be independently `approved`.

If either approval is rejected or expires:

- The watchdog requests cancellation of the workflow run.
- Remediation does not proceed past a rejected/expired first gate.
- Commit and push do not proceed past a rejected/expired second gate.
- The workflow does not use an approval from one environment as approval for the other.

Approval is enforced by GitHub Environment protection rules; the watchdog provides a separate deadline and audit check. Keep the required-reviewer configuration in place even though the workflow also checks the approval-history API.

---

## Remediation recipe and safety controls

### Current recipe: `frontend_title_mismatch_v2`

This recipe is deliberately narrow. It is not a generic repair engine.

It checks:

- The required project structure exists.
- The target files exist and are regular files (symlink/path-escape targets are rejected).
- The HTML contains exactly one title element.
- The title is either the supported old title or already-correct new title.
- The app test has exactly the supported old assertion or the exact new assertion.
- The known-issue test has exactly the supported hard-coded mismatch stub or the exact dynamic assertion installed by this recipe.

It does not use whole-file SHA-256 hashes as semantic preconditions. It records before/after SHA-256 hashes for audit and patch-integrity verification.

### Validation commands

The executor uses a fixed allowlist in Python:

```text
npm test
npm run build
```

Commands are invoked as argument arrays with `shell=False`; commands are not taken from the YAML rules, log text, or user input. The executor uses atomic file writes. If a transformation, validation, or execution step fails, it attempts to restore every modified file and records rollback details in the audit report.

### Risk and policy

The `unit_test_failure` rule currently has:

```yaml
remediation:
  enabled: true
  risk: high
  mode: assisted
  approval_required: true
  max_attempts: 1

validation:
  - npm test
  - npm run build

target_branch: originating_feature_branch
commit_only_after_validation: true
```

High-risk remediation requires approval. `retry_candidate: true` is only a policy signal for future consideration; it is not permission to automatically rerun a job. No automatic retry is currently performed.

### Audit status examples

- `validated_pending_commit_approval`: transformations and validation passed; awaiting patch review and the second approval.
- `validation_failed_rolled_back`: validation failed and rollback was performed.
- `execution_failed_rolled_back`: execution failed and rollback was performed.
- `not_applicable`: semantic preconditions or diagnosis evidence did not match the supported recipe.
- `validated_audit_write_failed`: validations passed but the audit could not be persisted; the workflow treats this as a failure.

Never treat a successful diagnosis as proof that a remediation is safe. The executor performs its own evidence and semantic-precondition checks.

---

## Patch, audit, and artifact handling

### Patch manifest

The handoff manifest records:

- Repository name.
- Original Frontend CI run ID.
- Source branch and source SHA.
- Controller SHA.
- Recipe ID.
- Changed-file allowlist.
- SHA-256 of `remediation.patch`.
- SHA-256 of `audit.json`.

### Integrity checks

Before commit, the workflow verifies:

- The manifest refers to the current repository, source run, branch, source SHA, and controller SHA.
- The patch and audit SHA-256 values match the manifest.
- The audit reports `validated_pending_commit_approval`.
- Both required validation commands passed.
- The changed files match the allowlist and patch.
- The feature branch still points to the original source SHA.
- The patch applies cleanly and resulting file hashes match the remediation audit.
- Only audited files are staged.

If the source branch advances while awaiting review, the final job refuses to commit the stale patch. A new failure diagnosis and approval cycle is required.

### Artifacts

| Artifact name | Contents | Retention |
|---|---|---:|
| `frontend-diagnosis-<run-id>` | Sanitized diagnosis JSON | 7 days |
| `remediation-execution-evidence-<run-id>` | Remediation audit and execution log | 14 days |
| `validated-remediation-<run-id>` | Patch, audit, manifest, and patch statistics | 14 days |
| `remediation-commit-result-<run-id>` | Commit information and post-push Frontend CI result | 14 days |

Generated local report JSON files under `reports/` are ignored by Git. Keep artifacts as the workflow evidence for a run; do not treat temporary runner files as durable storage.

---

## Commit, push, and post-remediation CI

### Authentication

The current workflow uses GitHub's built-in `GITHUB_TOKEN`. No GitHub App is required.

The final job requires:

```yaml
permissions:
  contents: write
  actions: write
```

- `contents: write` allows the approved feature-branch commit and push.
- `actions: write` allows the workflow to dispatch Frontend CI.

The token is available only to the job that needs those permissions. Do not broaden the workflow-level permissions to write access.

### Why an explicit CI dispatch is used

GitHub suppresses most workflow runs caused by events created with `GITHUB_TOKEN`, including `push`-triggered workflow runs. The workflow therefore dispatches `Frontend CI` explicitly after a successful push.

### Dispatch requirements

- The `Frontend CI` workflow must be active and present on the repository's default branch so it can be dispatched through the workflow API.
- It must include the `workflow_dispatch` trigger.
- The dispatch request sets `ref` to the originating feature branch.
- The final job verifies the resulting run against the exact remediation commit SHA and workflow ID.

The dispatcher discovers the active workflow named `Frontend CI`. It fails closed if zero or multiple active workflows with that name are found.

### Post-push result behavior

- If the dispatched Frontend CI concludes `success`, the final status becomes `pushed_ci_success`.
- If it completes with another conclusion, the status becomes `pushed_ci_failure` and the final job fails for visibility. The commit remains pushed.
- If dispatch cannot be completed or the run cannot be identified, the commit remains pushed and the dispatch failure is recorded.
- The workflow does not automatically revert the commit, create another remediation, or dispatch an unbounded retry.

---

## Permissions and security model

### Current pre-merge pilot boundary

The current diagnostic workflow checks out the controller from the feature-branch commit that triggered it. The final job can write to the originating feature branch after the two approvals.

This is useful for validating changes before merging the project into the default branch, but it means the workflow definition and controller are part of the feature-branch trust boundary. Restrict write access to the pilot branch and use only trusted contributors. Do not run this write-enabled pilot against untrusted fork branches or code from untrusted contributors.

For a production rollout, move privileged orchestration to a reviewed, protected workflow on the default branch and keep the trusted controller separate from untrusted application source. Re-evaluate artifact provenance and token permissions as part of that transition.

### Other security controls

- Use least-privilege job-level `GITHUB_TOKEN` permissions.
- Do not expose tokens or private keys in logs.
- Do not store credentials in source files or commit `.env` files.
- Do not execute downloaded artifacts from the original frontend CI run.
- Check out the exact source SHA for diagnosis/remediation and verify it again before commit.
- Apply a fixed recipe and allowlisted file list; do not accept arbitrary shell commands from rules or logs.
- Preserve the audit and patch hashes across job boundaries.
- Do not bypass branch protection or use force-push.
- Keep both GitHub Environment reviewer configurations active.

### Dependency installation consideration

The remediation job currently installs frontend dependencies with `npm ci --ignore-scripts --no-audit --no-fund`, while the regular Frontend CI uses `npm ci --no-audit --no-fund`. `--ignore-scripts` reduces package lifecycle-script execution during remediation, but it may not be compatible with projects whose dependencies require install scripts. Review this setting before adapting the workflow to a real application.

---

## Troubleshooting

| Symptom | Likely cause | What to check |
|---|---|---|
| Diagnostic workflow cannot find Frontend CI | CI run has not started yet, wrong workflow name, wrong ref, or different SHA | Confirm the workflow is named exactly `Frontend CI`, is triggered on the same feature push, and its run uses the same SHA/branch. |
| Frontend CI succeeds but no remediation runs | Expected behavior | Diagnosis/remediation proceeds only for a failed matching run. |
| No remediation proposal is approved for a failure | Failure is unsupported or diagnosis signature does not match | Inspect the diagnosis JSON, matched rule IDs, category, sanitized log excerpt, and policy decision. |
| Approval environment does not pause | Environment has no required reviewers or its name does not match YAML | Check `remediation-approval` or `remediation-commit-approval` under Settings → Environments. |
| Approval gate is cancelled after about a minute | Environment job started without a corresponding approval-history record | Configure required reviewers and verify the correct environment name and GitHub plan/policy support. |
| Approval expires | No approved review record arrived before the configured deadline | Check `APPROVAL_TIMEOUT_MINUTES`; if unset, the default is 30 minutes. |
| First approval passed but second approval is not requested | Patch review failed or the second environment job is not configured | Inspect `remediation-review` and the `remediation-commit-approval` environment. |
| Second approval does not permit commit | The second environment approval record is missing/rejected, or the first approval record is not approved | Inspect both environment approvals in the workflow run's approval history. |
| Patch hash or audit validation fails | Artifact mismatch, changed artifact, or incomplete handoff | Do not bypass checks. Re-run the remediation cycle and inspect the patch, manifest, and audit artifacts. |
| Feature branch advanced before commit | New commit was pushed after diagnosis | The stale patch is intentionally rejected. Start a new diagnosis/approval cycle from the latest branch state. |
| Commit succeeds but push is rejected | Branch protection or repository policy blocks the workflow token | Review branch protection/rulesets and confirm the workflow token is allowed to push. Do not bypass protections. |
| Frontend CI dispatch cannot find workflow | Workflow missing/inactive on default branch, `workflow_dispatch` absent, or duplicate active workflows named `Frontend CI` | Check the default-branch workflow file, trigger, name, and workflow ID. |
| Dispatch returns an empty body | The API accepted the dispatch with no response body | The workflow falls back to querying workflow runs by workflow ID, event, branch, SHA, and creation time. |
| Post-push CI fails | The remediation commit was pushed but CI found another failure | Inspect the recorded CI run and artifact. The current implementation does not automatically roll back or repair again. |

---

## Known limitations and next work

1. **Expand rule coverage carefully.** Validate rules against real failed-step logs and measure false positives/false negatives before enabling new recovery recipes.
2. **Add recipes one at a time.** Each recipe needs semantic preconditions, a strict file/command allowlist, validation, rollback, audit evidence, and approval requirements.
3. **Evaluate attempt tracking.** Add durable incident/run history before enforcing retry or remediation-attempt limits across separate workflow runs.
4. **Test safety cases.** Include unsupported failures, validation failures and rollback, missing/misconfigured approval environments, approval rejection/expiry, branch advancement, patch-integrity mismatch, push rejection, dispatch failure, and post-push CI failure.
5. **Prepare the production trust model.** Move privileged workflow orchestration to the protected default branch after review; do not make a feature-branch-controlled workflow the final production authority for write-capable actions.
6. **Consider AI only after deterministic behavior is measured.** Any future AI component should recommend or explain actions without bypassing rule eligibility, semantic preconditions, approvals, allowlists, validation, rollback, or audit controls.

The prototype should be evaluated on repeatability, diagnosis accuracy, remediation success rate, rollback reliability, approval enforcement, audit completeness, and post-push CI outcomes. No patentability or novelty conclusion is implied by this README.
