# Separate GitHub Actions application and diagnostic workflows (no AI)

## Workflow separation

- `.github/workflows/frontend-ci.yml` is the application workflow only. It runs the Node.js frontend pipeline and does not contain or invoke the Python diagnosis controller.
- `.github/workflows/debug-frontend.yml` is the separate diagnostic workflow. It runs automatically after a failed application workflow on `feature/*`, or manually from the protected default branch with a supplied failed feature-run ID.

## Automatic trigger

The diagnostic workflow listens for `workflow_run` completion events from the workflow named `Frontend CI - Failure Scenarios`. Its branch filter is `feature/*`; the diagnostic job proceeds only when the originating run conclusion is `failure`. GitHub may create a diagnostic workflow-run entry after a successful feature run too, but the job is skipped. A run on `main` or any other branch does not trigger this automatic diagnostic workflow.

## Manual trigger

In GitHub Actions, select **Frontend Failure Diagnostics** → **Run workflow**. Choose `main` (the protected default branch) in the branch selector so the trusted diagnostic workflow definition is used. Provide the failed frontend workflow run ID from a `feature/*` branch, and optionally provide a specific run attempt. The diagnostic job is guarded so a manual dispatch from a non-default branch is skipped. The Python controller independently verifies that the target run itself came from `feature/*`, belongs to `Frontend CI - Failure Scenarios`, and contains a failed job in the selected attempt.

GitHub requires a workflow file using `workflow_dispatch` to exist on the repository's default branch to enable the manual Run workflow control. Likewise, a workflow using `workflow_run` must exist on the default branch to receive those events. Raise a PR to merge `.github/workflows/debug-frontend.yml` and the controller code into `main`; keep the diagnostic workflow committed there.

## Automatic trigger execution context and trust boundary

For `workflow_run`, GitHub runs the diagnostic workflow in the default-branch context. The diagnostic workflow explicitly checks out the repository's default branch to obtain the Python controller, rules and dependencies. It does not check out or execute source code from the originating feature branch and does not download artifacts from the application workflow. This avoids executing feature-branch code with the diagnostic workflow's token.

The diagnostic workflow has only `actions: read` and `contents: read`. The Python controller makes GitHub GET requests only. AI and recovery remain disabled.

## Data flow

1. The application workflow runs the Node.js pipeline.
2. On completion, GitHub emits a `workflow_run` event.
3. GitHub filters the event to `feature/*`; the diagnostic job additionally requires a failed conclusion.
4. The Python controller receives the originating workflow run ID and attempt from the event. For manual dispatch, it receives the supplied run ID and optional attempt.
5. The controller reads workflow metadata, failed jobs/steps and step/job logs using the GitHub REST API.
6. It redacts common secrets, applies `config/rules.yaml`, and writes a JSON report and GitHub Actions job summary.
7. The report is uploaded as a sanitized artifact.

## Current constraints

- `feature/*` means one branch segment after `feature/`, for example `feature/login`. Nested names such as `feature/team/login` are intentionally excluded by the controller's fail-closed validation.
- Manual dispatch has no native branch filter. For safety, start it from the default branch (`main`) so the trusted workflow definition runs. The job checks the selected `github.ref`, and the controller checks the supplied target run's `head_branch` and workflow name. It will refuse a target run originating from `main`.
- The diagnostic workflow file must exist on the default branch for automatic `workflow_run` and for the Actions UI manual-dispatch button.
- GitHub's `workflow_run` event cannot filter on the upstream conclusion. Successful feature runs therefore may show a diagnostic workflow run with its only job skipped; failed feature runs execute the job.
- The workflow file selected for manual dispatch should be reviewed; only authorized users should manually run diagnostics.
- This remains a no-AI, no-recovery prototype.
