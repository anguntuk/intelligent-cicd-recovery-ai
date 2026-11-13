"""CLI for local fixtures and GitHub-hosted deterministic CI/CD diagnosis."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from cicd_recovery.analyzer import analyze_job, build_report
from cicd_recovery.github_api import GitHubActionsClient, GitHubApiError
from cicd_recovery.rules import load_rules


def find_project_root(start: Path | None = None) -> Path:
    current = (start or Path.cwd()).resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / "pyproject.toml").is_file() and (candidate / "config" / "rules.yaml").is_file():
            return candidate
    return current


PROJECT_ROOT = find_project_root()
DEFAULT_RULES = PROJECT_ROOT / "config" / "rules.yaml"
DEFAULT_FIXTURES = PROJECT_ROOT / "fixtures" / "logs"


def _safe_filename(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)


def _write_json(report: dict[str, Any], output_dir: Path, filename: str) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / filename
    output_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return output_path


def _print_recommendation(recommendation: str) -> None:
    print("Recommended resolution steps:")
    for line in recommendation.strip().splitlines():
        if line.strip():
            print(f"  {line.rstrip()}")


def _print_remediation_plan(plan: dict[str, Any]) -> None:
    print("\nRemediation policy assessment:")
    for label, key in (
        ("Decision", "decision"), ("Eligible", "eligible"), ("Risk", "risk"),
        ("Approval required", "requires_approval"), ("Execution status", "execution_status"),
        ("Executor available", "executor_available"), ("Reason", "reason"),
        ("Next action", "next_action"),
    ):
        print(f"  {label:<20}: {plan.get(key)}")
    print(f"  Attempts             : {plan.get('attempts_used')}/{plan.get('max_attempts')}")
    if plan.get("validation_commands"):
        print("  Validation commands (planned only; NOT executed):")
        for command in plan["validation_commands"]:
            print(f"    - {command}")
    if plan.get("approval_gates"):
        print("  Approval gates:")
        for gate in plan["approval_gates"]:
            print(f"    - {gate}")


def _print_report_summary(report: dict[str, Any]) -> None:
    run = report.get("workflow_run", {})
    print("\n=== Intelligent CI/CD Failure Recovery: Diagnosis ===")
    print(f"Repository : {report.get('repository', 'local fixture')}")
    if run:
        print(f"Run ID     : {run.get('id')}\nWorkflow   : {run.get('name')}\nConclusion : {run.get('conclusion')}")
    print(f"AI enabled : {report.get('ai_enabled')}\nRecovery   : {report.get('recovery_enabled')}")
    analyses = report.get("analyses", [])
    if not analyses:
        print("\nNo failed jobs were found to analyze.")
    for analysis in analyses:
        diagnosis = analysis["diagnosis"]
        print("\n----------------------------------------")
        print(f"Job        : {analysis.get('job_name')} (ID: {analysis.get('job_id')})")
        print(f"Conclusion : {analysis.get('job_conclusion')}")
        step_names = ", ".join(s.get("name", "(unnamed)") for s in analysis.get("failed_steps", []))
        print(f"Failed step: {step_names or 'not reported'}")
        print(f"Category   : {diagnosis.get('category')}\nDiagnosis  : {diagnosis.get('title')}")
        print("Rule IDs   : " + (", ".join(diagnosis.get("matched_rule_ids", [])) or "none (unknown)"))
        print(f"Remediation candidate: {diagnosis.get('retry_candidate_for_future_policy_review', False)}")
        _print_recommendation(diagnosis.get("recommendation", "Review logs manually."))
        _print_remediation_plan(diagnosis.get("remediation_plan", {}))
        for group in diagnosis.get("evidence", []):
            for line in group.get("lines", []):
                print(f"Evidence   : {line}")


def command_diagnose_fixture(args: argparse.Namespace) -> int:
    log_path = Path(args.file)
    if not log_path.is_file():
        print(f"Fixture file not found: {log_path}", file=sys.stderr)
        return 2
    try:
        rules = load_rules(args.rules)
        analysis = analyze_job(
            job={"id": None, "name": "Local fixture", "conclusion": "failure",
                 "steps": [{"name": log_path.stem, "conclusion": "failure"}]},
            raw_log=log_path.read_text(encoding="utf-8", errors="replace"),
            rules=rules,
        )
    except (OSError, ValueError) as exc:
        print(f"Diagnosis configuration/input error: {exc}", file=sys.stderr)
        return 2
    report = {
        "schema_version": 2, "mode": "local_fixture_diagnosis_and_policy_preview",
        "ai_enabled": False, "recovery_enabled": False, "fixture": log_path.name,
        "analyses": [analysis],
        "limitations": ["Remediation is policy-preview only.",
                        "No validation command is executed and no source files are modified."],
    }
    _print_report_summary(report)
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"\nReport written to: {output_path}")
    else:
        print("\nJSON report:\n" + json.dumps(report, indent=2, ensure_ascii=False))
    print("\nNo remediation, approval request, retry, or GitHub write was performed.")
    return 0


def command_list_runs(args: argparse.Namespace) -> int:
    token = os.getenv("GH_TOKEN", "")
    if not token:
        print("GH_TOKEN is not set. Use a fine-grained token with Actions: read.", file=sys.stderr)
        return 2
    try:
        with GitHubActionsClient(token, api_version=os.getenv("GITHUB_API_VERSION", "2026-03-10")) as client:
            runs = client.list_runs(args.repo, args.limit)
    except (GitHubApiError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    print(f"Recent workflow runs for {args.repo}:")
    for run in runs:
        print(f"- run_id={run.get('id')} attempt={run.get('run_attempt')} status={run.get('status')} conclusion={run.get('conclusion')} workflow={run.get('name')} url={run.get('html_url')}")
    if not runs:
        print("No workflow runs found.")
    return 0


def _append_github_step_summary(report: dict[str, Any]) -> None:
    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    run = report.get("workflow_run", {})
    lines = [
        "## Intelligent CI/CD Failure Recovery", "",
        f"- **Repository:** {report.get('repository', 'unknown')}",
        f"- **Workflow:** {run.get('name', 'unknown')}",
        f"- **Run:** [{run.get('id', 'unknown')}]({run.get('html_url', '')})",
        f"- **Conclusion:** {run.get('conclusion', 'unknown')}",
        "- **AI:** disabled", "- **Recovery execution:** disabled", "",
    ]
    for analysis in report.get("analyses", []):
        diagnosis = analysis.get("diagnosis", {})
        names = ", ".join(s.get("name", "(unnamed)") for s in analysis.get("failed_steps", [])) or "not reported"
        lines.extend([
            f"### {analysis.get('job_name', 'Unknown job')}", "",
            f"- **Failed step(s):** {names}",
            f"- **Log scope:** {analysis.get('log_scope', 'full job log')}",
            f"- **Category:** `{diagnosis.get('category', 'unknown')}`",
            "- **Matched rules:** " + (", ".join(diagnosis.get("matched_rule_ids", [])) or "none"), "",
            "#### Recommended resolution steps", "",
        ])
        recommendation = str(diagnosis.get("recommendation", "Review logs manually.")).strip()
        lines.extend(recommendation.splitlines() if recommendation else ["Review logs manually."])
        lines.append("")

        plan = diagnosis.get("remediation_plan", {})
        if plan:
            lines.extend([
                "#### Remediation policy assessment", "",
                f"- **Decision:** `{plan.get('decision', 'unknown')}`",
                f"- **Risk:** {plan.get('risk') or 'not configured'}",
                f"- **Approval required:** {plan.get('requires_approval', False)}",
                f"- **Execution status:** {plan.get('execution_status', 'not_executed')}",
                f"- **Executor available:** {plan.get('executor_available', False)}",
                f"- **Policy reason:** {plan.get('reason', '')}",
                f"- **Next action:** {plan.get('next_action', '')}", "",
            ])
            commands = plan.get("validation_commands") or []
            if commands:
                lines.extend(["**Validation commands (planned only; not executed):**", ""])
                lines.extend(f"- `{command}`" for command in commands)
                lines.append("")
            gates = plan.get("approval_gates") or []
            if gates:
                lines.extend(["**Approval gates:**", ""])
                lines.extend(f"- `{gate}`" for gate in gates)
                lines.append("")

        if diagnosis.get("evidence"):
            lines.extend(["#### Supporting log evidence", ""])
        for group in diagnosis.get("evidence", []):
            for evidence_line in group.get("lines", []):
                lines.append(f"> `{str(evidence_line).replace('`', chr(39))}`")
        lines.append("")
    with open(summary_path, "a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def _validate_target_run(run: dict[str, Any], *, require_feature_branch: bool,
                         require_failure: bool, expected_workflow_name: str | None = None) -> None:
    if expected_workflow_name and run.get("name") != expected_workflow_name:
        raise ValueError(f"Refusing to analyze this run: expected workflow {expected_workflow_name!r}, received {run.get('name')!r}.")
    if require_feature_branch:
        branch = run.get("head_branch")
        if not isinstance(branch, str) or not branch.startswith("feature/") or len(branch) <= len("feature/"):
            raise ValueError(f"Refusing to analyze this run: source branch must match feature/*; received {branch!r}.")
    if require_failure and run.get("conclusion") != "failure":
        raise ValueError(f"Refusing to analyze this run: source workflow must have conclusion 'failure'; received {run.get('conclusion')!r}.")


def command_diagnose_github(args: argparse.Namespace) -> int:
    token = os.getenv("GH_TOKEN", "")
    if not token:
        print("GH_TOKEN is not set. Use a fine-grained token with Actions: read.", file=sys.stderr)
        return 2
    try:
        rules = load_rules(args.rules)
    except (OSError, ValueError) as exc:
        print(f"Rules configuration error: {exc}", file=sys.stderr)
        return 2

    try:
        with GitHubActionsClient(token, api_version=os.getenv("GITHUB_API_VERSION", "2026-03-10")) as client:
            run = client.get_run(args.repo, args.run_id)
            latest_attempt = int(run.get("run_attempt", 1))
            run_attempt = args.run_attempt if args.run_attempt is not None else latest_attempt
            if not 1 <= run_attempt <= latest_attempt:
                raise ValueError(f"Requested run attempt must be between 1 and {latest_attempt}.")
            _validate_target_run(
                run, require_feature_branch=args.require_feature_branch,
                require_failure=args.require_failure and run_attempt == latest_attempt,
                expected_workflow_name=args.expected_workflow_name,
            )
            jobs = client.list_jobs(args.repo, args.run_id, run_attempt=run_attempt)
            failed_jobs = [j for j in jobs if j.get("conclusion") in {"failure", "timed_out", "cancelled"}]
            if args.require_failure and not failed_jobs:
                raise ValueError(f"Refusing to analyze attempt {run_attempt}: no failed jobs were returned.")
            analyses = []
            for job in failed_jobs:
                job_id = job.get("id")
                if not isinstance(job_id, int):
                    continue
                failed_steps = [
                    step for step in (job.get("steps") or [])
                    if step.get("conclusion") in {"failure", "timed_out"} and isinstance(step.get("number"), int)
                ]
                step_logs: list[str] = []
                step_log_error: str | None = None
                for step in failed_steps:
                    try:
                        text = client.download_step_log(args.repo, job_id, step["number"], max_bytes=args.max_log_bytes)
                        step_logs.append(f"===== FAILED STEP {step['number']}: {step.get('name', 'unnamed')} =====\n{text}")
                    except GitHubApiError as exc:
                        step_log_error = str(exc)
                        break
                if failed_steps and step_logs and step_log_error is None:
                    log_text, scope = "\n".join(step_logs), "failed_step_logs"
                else:
                    if step_log_error:
                        print(f"Warning: step log retrieval failed; using job log. {step_log_error}", file=sys.stderr)
                    log_text = client.download_job_log(args.repo, job_id, max_bytes=args.max_log_bytes)
                    scope = "full_job_log_fallback" if failed_steps else "full_job_log"
                analysis = analyze_job(job=job, raw_log=log_text, rules=rules)
                analysis["log_scope"] = scope
                analyses.append(analysis)
    except (GitHubApiError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    report_run = dict(run)
    report_run["latest_run_attempt"] = latest_attempt
    report_run["run_attempt"] = run_attempt
    report = build_report(repository=args.repo, run=report_run, jobs=jobs, analyses=analyses)
    _print_report_summary(report)
    filename = f"{_safe_filename(args.repo)}-run-{args.run_id}-attempt-{run_attempt}.json"
    output_path = _write_json(report, Path(args.output_dir), filename)
    _append_github_step_summary(report)
    print(f"\nSanitized diagnosis report written to: {output_path}")
    print("Policy preview only: no validation, approval request, remediation, retry, commit, or push was executed.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cicd-recovery",
        description="Read-only deterministic CI/CD diagnosis and remediation policy preview.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    list_parser = subparsers.add_parser("list-runs", help="List recent GitHub Actions runs")
    list_parser.add_argument("--repo", required=True)
    list_parser.add_argument("--limit", type=int, default=10)
    list_parser.set_defaults(func=command_list_runs)

    diagnose = subparsers.add_parser("diagnose", help="Diagnose a GitHub Actions run")
    diagnose.add_argument("--repo", required=True)
    diagnose.add_argument("--run-id", required=True, type=int)
    diagnose.add_argument("--run-attempt", type=int)
    diagnose.add_argument("--expected-workflow-name")
    diagnose.add_argument("--rules", type=Path, default=DEFAULT_RULES)
    diagnose.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "reports")
    diagnose.add_argument("--max-log-bytes", type=int, default=5_000_000)
    diagnose.add_argument("--require-feature-branch", action="store_true")
    diagnose.add_argument("--require-failure", action="store_true")
    diagnose.set_defaults(func=command_diagnose_github)

    fixture = subparsers.add_parser("diagnose-fixture", help="Diagnose a local log fixture")
    fixture.add_argument("--file", required=True, type=Path)
    fixture.add_argument("--rules", type=Path, default=DEFAULT_RULES)
    fixture.add_argument("--output", type=Path)
    fixture.set_defaults(func=command_diagnose_fixture)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
