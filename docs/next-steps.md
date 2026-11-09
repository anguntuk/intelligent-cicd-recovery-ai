# Next steps after separating application and diagnostic workflows

1. Raise a PR to merge `.github/workflows/debug-frontend.yml`, the Python controller and its rules into `main`. GitHub needs the diagnostic workflow on the default branch to deliver `workflow_run` events and expose manual dispatch.
2. Push a change to `feature/<name>` and confirm the application workflow runs independently. A separate diagnostic workflow entry may appear after completion; its job is skipped when the frontend run succeeds.
3. Trigger a controlled failure on `feature/<name>` and confirm a separate **Frontend Failure Diagnostics** workflow run starts automatically.
4. Confirm a failure on `main` does not start the diagnostic job.
5. Manually run **Frontend Failure Diagnostics** from `main`, supplying a failed frontend run ID from `feature/<name>`. Confirm manual analysis works and rejects a `main` run ID.
6. Test attempts and validate that the controller refuses successful runs, unrelated workflows and non-feature branches.
7. Refine rules against actual failed-step logs; then consider persistent incident history and Copilot analysis.
