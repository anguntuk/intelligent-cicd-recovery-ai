"""CLI for local fixture tests and GitHub-hosted deterministic failure diagnosis."""

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
from cicd_recovery.redaction import redact_text
from cicd_recovery.rules import load_rules, match_rules

def find_project_root(start: Path | None = None) -> Path:
    """Locate the repository root from the current working directory."""

    current = (start or Path.cwd()).resolve()

    if current.is_file():
        current = current.parent

    for candidate in (current, *current.parents):
        if (
            (candidate / "pyproject.toml").is_file()
            and (candidate / "config" / "rules.yaml").is_file()
        ):
            return candidate

    raise FileNotFoundError(
        "Could not locate project root containing "
        "pyproject.toml and config/rules.yaml"
    )


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


def _print_report_summary(report: dict[str, Any]) -> None:
    run = report.get("workflow_run", {})
    print("\n=== Intelligent CI/CD Failure Recovery: Diagnosis ===")
    print(f"Repository : {report.get('repository', 'local fixture')}")
    if run:
        print(f"Run ID     : {run.get('id')}")
        print(f"Workflow   : {run.get('name')}")
        print(f"Conclusion : {run.get('conclusion')}")
        print(f"Run URL    : {run.get('html_url')}")
    print(f"AI enabled : {report.get('ai_enabled')}")
    print(f"Recovery   : {report.get('recovery_enabled')}")

    analyses = report.get("analyses", [])
    if not analyses:
        print("\nNo failed jobs were found to analyze.")
    for analysis in analyses:
        diagnosis = analysis["diagnosis"]
        print("\n----------------------------------------")
        print(f"Job        : {analysis.get('job_name')} (ID: {analysis.get('job_id')})")
        print(f"Conclusion : {analysis.get('job_conclusion')}")
        failed_steps = analysis.get("failed_steps", [])
        print("Failed step : " + (", ".join(step["name"] for step in failed_steps) or "not reported"))
        print(f"Category   : {diagnosis['category']}")
        print(f"Diagnosis  : {diagnosis['title']}")
        print(f"Rule IDs   : {', '.join(diagnosis['matched_rule_ids']) or 'none (unknown)'}")
        print(f"Retry hint : {diagnosis['retry_candidate_for_future_policy_review']} (not executed)")
        print(f"Next step  : {diagnosis['recommendation']}")
        for evidence_group in diagnosis.get("evidence", []):
            for line in evidence_group.get("lines", []):
                print(f"Evidence   : {line}")


def command_diagnose_fixture(args: argparse.Namespace) -> int:
    log_path = Path(args.file)
    if not log_path.is_file():
        print(f"Fixture file not found: {log_path}", file=sys.stderr)
        return 2
    rules = load_rules(args.rules)
    safe_log = redact_text(log_path.read_text(encoding="utf-8", errors="replace"))
    matches = match_rules(safe_log, rules)
    report = {
        "schema_version": 1,
        "mode": "local_fixture_deterministic_diagnosis",
        "ai_enabled": False,
        "recovery_enabled": False,
        "fixture": log_path.name,
        "diagnosis": (
            {
                "category": matches[0].category,
                "title": matches[0].title,
                "matched_rule_ids": [match.rule_id for match in matches],
                "recommendation": matches[0].recommendation,
                "retry_candidate_for_future_policy_review": any(
                    match.retry_candidate for match in matches
                ),
                "evidence": [
                    {"rule_id": match.rule_id, "lines": list(match.evidence)} for match in matches
                ],
            }
            if matches
            else {
                "category": "unknown",
                "title": "Unclassified CI/CD failure",
                "matched_rule_ids": [],
                "recommendation": "Review the evidence and add a tested rule if reusable.",
                "retry_candidate_for_future_policy_review": False,
                "evidence": [],
            }
        ),
    }
    print(json.dumps(report, indent=2))
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(f"\nReport written to: {output_path}")
    return 0


def command_list_runs(args: argparse.Namespace) -> int:
    token = os.getenv("GH_TOKEN", "")
    if not token:
        print(
            "GH_TOKEN is not set. In GitHub Actions, pass ${{ github.token }}; "
            "for local API testing, use a fine-grained token with Actions: read.",
            file=sys.stderr,
        )
        return 2
    api_version = os.getenv("GITHUB_API_VERSION", "2026-03-10")
    try:
        with GitHubActionsClient(token, api_version=api_version) as client:
            runs = client.list_runs(args.repo, args.limit)
    except (GitHubApiError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    print(f"Recent workflow runs for {args.repo}:")
    for run in runs:
        print(
            f"- run_id={run.get('id')} attempt={run.get('run_attempt')} "
            f"status={run.get('status')} conclusion={run.get('conclusion')} "
            f"workflow={run.get('name')} url={run.get('html_url')}"
        )
    if not runs:
        print("No workflow runs found.")
    return 0


def _append_github_step_summary(report: dict[str, Any]) -> None:
    """Append a concise, sanitized report to GitHub Actions' job summary when available."""
    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return

    run = report.get("workflow_run", {})
    lines = [
        "## Intelligent CI/CD Failure Recovery",
        "",
        f"- **Repository:** {report.get('repository', 'unknown')}",
        f"- **Workflow:** {run.get('name', 'unknown')}",
        f"- **Run:** [{run.get('id', 'unknown')}]({run.get('html_url', '')})",
        f"- **Conclusion:** {run.get('conclusion', 'unknown')}",
        "- **AI:** disabled",
        "- **Recovery:** disabled",
        "",
    ]
    for analysis in report.get("analyses", []):
        diagnosis = analysis.get("diagnosis", {})
        failed_steps = analysis.get("failed_steps", [])
        step_names = (
            ", ".join(step.get("name", "(unnamed)") for step in failed_steps)
            or "not reported"
        )
        lines.extend(
            [
                f"### {analysis.get('job_name', 'Unknown job')}",
                f"- **Failed step(s):** {step_names}",
                f"- **Log scope:** {analysis.get('log_scope', 'full job log')}",
                f"- **Category:** `{diagnosis.get('category', 'unknown')}`",
                f"- **Matched rules:** {', '.join(diagnosis.get('matched_rule_ids', [])) or 'none'}",
                f"- **Recommendation:** {diagnosis.get('recommendation', 'Review logs manually.')}",
                "",
            ]
        )
        for group in diagnosis.get("evidence", []):
            for evidence_line in group.get("lines", []):
                lines.append(f"> `{evidence_line.replace('`', chr(39))}`")
        lines.append("")

    with open(summary_path, "a", encoding="utf-8") as summary_file:
        summary_file.write("\n".join(lines) + "\n")


def _validate_target_run(
    run: dict[str, Any],
    *,
    require_feature_branch: bool,
    require_failure: bool,
    expected_workflow_name: str | None = None,
) -> None:
    """Fail closed when a requested diagnosis is outside the permitted scope."""
    if expected_workflow_name and run.get("name") != expected_workflow_name:
        raise ValueError(
            "Refusing to analyze this run: expected workflow "
            f"{expected_workflow_name!r}, received {run.get('name')!r}."
        )
    if require_feature_branch:
        branch = run.get("head_branch")
        # Match feature/* (one branch segment), not main, feature-x, or nested refs.
        if not isinstance(branch, str) or re.fullmatch(r"feature/[^/]+", branch) is None:
            raise ValueError(
                "Refusing to analyze this run: its source branch must match feature/*; "
                f"received {branch!r}."
            )
    if require_failure and run.get("conclusion") != "failure":
        raise ValueError(
            "Refusing to analyze this run: the source workflow must have conclusion 'failure'; "
            f"received {run.get('conclusion')!r}."
        )


def command_diagnose_github(args: argparse.Namespace) -> int:
    token = os.getenv("GH_TOKEN", "")
    if not token:
        print(
            "GH_TOKEN is not set. In GitHub Actions, pass ${{ github.token }}; "
            "for local API testing, use a fine-grained token with Actions: read.",
            file=sys.stderr,
        )
        return 2
    rules = load_rules(args.rules)
    api_version = os.getenv("GITHUB_API_VERSION", "2026-03-10")

    try:
        with GitHubActionsClient(token, api_version=api_version) as client:
            run = client.get_run(args.repo, args.run_id)
            latest_attempt = int(run.get("run_attempt", 1))
            run_attempt = args.run_attempt if args.run_attempt is not None else latest_attempt
            if run_attempt < 1 or run_attempt > latest_attempt:
                raise ValueError(
                    f"Requested run attempt must be between 1 and {latest_attempt}."
                )

            # The top-level run conclusion describes the latest attempt. If an older attempt is
            # explicitly selected, validate that attempt using its own job results below.
            _validate_target_run(
                run,
                require_feature_branch=args.require_feature_branch,
                require_failure=args.require_failure and run_attempt == latest_attempt,
                expected_workflow_name=args.expected_workflow_name,
            )
            jobs = client.list_jobs(args.repo, args.run_id, run_attempt=run_attempt)
            failed_jobs = [
                job for job in jobs if job.get("conclusion") in {"failure", "timed_out", "cancelled"}
            ]
            if args.require_failure and not failed_jobs:
                raise ValueError(
                    f"Refusing to analyze attempt {run_attempt}: no failed, timed-out, or "
                    "cancelled jobs were returned for that attempt."
                )
            analyses = []
            for job in failed_jobs:
                job_id = job.get("id")
                if not isinstance(job_id, int):
                    continue

                failed_steps = [
                    step
                    for step in (job.get("steps") or [])
                    if step.get("conclusion") in {"failure", "timed_out"}
                    and isinstance(step.get("number"), int)
                ]
                step_logs: list[str] = []
                step_log_error: str | None = None
                for step in failed_steps:
                    step_number = step["number"]
                    step_name = step.get("name", f"step {step_number}")
                    try:
                        step_log = client.download_step_log(
                            args.repo, job_id, step_number, max_bytes=args.max_log_bytes
                        )
                        step_logs.append(
                            f"===== FAILED STEP {step_number}: {step_name} =====\n{step_log}"
                        )
                    except GitHubApiError as exc:
                        step_log_error = str(exc)
                        break

                if failed_steps and step_logs and step_log_error is None:
                    log_text = "\n".join(step_logs)
                    log_scope = "failed_step_logs"
                else:
                    if step_log_error:
                        print(
                            f"Warning: step-specific log download failed for job {job_id}; "
                            f"falling back to full job log. {step_log_error}",
                            file=sys.stderr,
                        )
                    log_text = client.download_job_log(
                        args.repo, job_id, max_bytes=args.max_log_bytes
                    )
                    log_scope = "full_job_log_fallback" if failed_steps else "full_job_log"

                analysis = analyze_job(job=job, raw_log=log_text, rules=rules)
                analysis["log_scope"] = log_scope
                analyses.append(analysis)
    except (GitHubApiError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    if run.get("status") != "completed":
        print(
            f"Info: workflow run status is '{run.get('status')}'. "
            "This is expected because the failure-analysis job is still running; "
            "the report analyzes completed failed jobs in the current attempt.",
            file=sys.stderr,
        )

    report_run = dict(run)
    report_run["latest_run_attempt"] = latest_attempt
    report_run["run_attempt"] = run_attempt
    report = build_report(repository=args.repo, run=report_run, jobs=jobs, analyses=analyses)
    _print_report_summary(report)

    repo_slug = _safe_filename(args.repo)
    filename = f"{repo_slug}-run-{args.run_id}-attempt-{run_attempt}.json"
    output_path = _write_json(report, Path(args.output_dir), filename)
    _append_github_step_summary(report)
    print(f"\nSanitized diagnosis report written to: {output_path}")
    print("No retry or GitHub write operation was performed.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cicd-recovery",
        description="Read-only deterministic diagnosis for GitHub Actions failures.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list-runs", help="List recent GitHub Actions runs")
    list_parser.add_argument("--repo", required=True, help="Repository in OWNER/REPO format")
    list_parser.add_argument("--limit", type=int, default=10)
    list_parser.set_defaults(func=command_list_runs)

    diagnose_parser = subparsers.add_parser("diagnose", help="Diagnose a GitHub Actions run")
    diagnose_parser.add_argument("--repo", required=True, help="Repository in OWNER/REPO format")
    diagnose_parser.add_argument("--run-id", required=True, type=int)
    diagnose_parser.add_argument(
        "--run-attempt", type=int, help="Specific workflow attempt (defaults to current attempt)"
    )
    diagnose_parser.add_argument(
        "--expected-workflow-name",
        help="Refuse to analyze a run whose workflow name does not match this value",
    )
    diagnose_parser.add_argument("--rules", type=Path, default=DEFAULT_RULES)
    diagnose_parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "reports")
    diagnose_parser.add_argument("--max-log-bytes", type=int, default=5_000_000)
    diagnose_parser.add_argument(
        "--require-feature-branch",
        action="store_true",
        help="Refuse runs whose source branch does not match feature/*",
    )
    diagnose_parser.add_argument(
        "--require-failure",
        action="store_true",
        help="Refuse workflow runs whose conclusion is not failure",
    )
    diagnose_parser.set_defaults(func=command_diagnose_github)

    fixture_parser = subparsers.add_parser(
        "diagnose-fixture", help="Diagnose a local log fixture without GitHub credentials"
    )
    fixture_parser.add_argument("--file", required=True, type=Path)
    fixture_parser.add_argument("--rules", type=Path, default=DEFAULT_RULES)
    fixture_parser.add_argument("--output", type=Path)
    fixture_parser.set_defaults(func=command_diagnose_fixture)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
