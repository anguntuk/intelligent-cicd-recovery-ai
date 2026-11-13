# Unit-test remediation: first local milestone

## Files in this patch

- `src/cicd_recovery/remediation.py` — exact-match, allowlisted local recipe executor.
- `config/rules.yaml` — fixes the `unit_test_failure.remediation` YAML nesting and identifies the recipe.
- `fixtures/logs/unit_test_failure.log` — aligns the sample fixture with the reproducible frontend title mismatch.
- `tests/test_remediation_executor.py` — basic executor safety tests.

## Apply

Copy the patch contents into the matching paths in the repository. Do not overwrite the rest of the project.

From the repository root, activate the existing virtual environment and run:

```bash
python -m pytest
python -m cicd_recovery.cli diagnose-fixture --file fixtures/logs/unit_test_failure.log
```

Then, after reviewing the exact recipe and approving this local test run:

```bash
python -m cicd_recovery.remediation --project-root . --approve
```

The recipe checks that all three target files exactly match the known baseline. If any file differs, it exits without modifying anything. It runs only `npm test` and `npm run build` with `shell=False`. If validation fails, it restores all three original files and writes an audit report under `reports/`.

## Not included yet

This local milestone does not implement GitHub Environment approvals, isolated Git worktrees, durable cross-run attempt storage, branch/SHA guards, commits, pushes, or CI retries. Those are separate workflow stages and must not be inferred from a successful local validation.
