# Pull request description

## Suggested title

Add separate GitHub Actions workflow for feature-branch CI failure diagnosis

## Summary

- Keep `frontend-ci.yml` focused on the Node.js frontend pipeline only.
- Add `debug-frontend.yml` as a separate diagnostic workflow.
- Automatically listen for completion events from `Frontend CI - Failure Scenarios` on `feature/*`; execute the diagnosis job only when the source run fails.
- Add manual dispatch from the protected default branch (`main`) using a supplied frontend run ID. The controller validates that the target run is a failed `feature/*` run from the expected frontend workflow.
- Run the Python controller on a GitHub-hosted runner; no local watcher is required.
- Fetch workflow metadata, failed job/step information and relevant logs using read-only GitHub Actions API access.
- Redact common secret patterns, apply deterministic YAML rules, write a JSON report, add a job summary, and upload the report as an artifact.
- Keep AI analysis and automatic recovery disabled.

## Security

- Diagnostic job permissions are limited to `actions: read` and `contents: read`.
- The diagnostic workflow checks out controller code from the default branch, not the triggering feature branch.
- It does not download or execute artifacts from the application workflow.
- The controller checks the source branch, workflow name, failure outcome and selected attempt before analysis.
- No workflow reruns, deployments, repository writes or source-code changes are performed.

## Validation completed

- Python unit tests: 19 passed.
- Node.js lint, source-contract type-check, unit test and build commands passed locally.
- Both workflow YAML files parsed successfully.
- The GitHub-hosted workflow and live GitHub API integration still require execution in the target repository after merge.

## Notes

- GitHub requires the diagnostic workflow file to exist on the default branch for `workflow_run` and `workflow_dispatch` to work.
- `workflow_run` can create a diagnostic workflow-run entry for successful feature-branch runs too; the diagnosis job is skipped unless the frontend run conclusion is `failure`.
- The current demo is a generic frontend sample, not yet adapted to the client application's actual Node.js framework and scripts.
