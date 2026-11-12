
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
    """Find the repository root from the current working directory."""
    current = (start or Path.cwd()).resolve()

    if current.is_file():
        current = current.parent

    for candidate in (current, *current.parents):
        if (
            (candidate / "pyproject.toml").is_file()
            and (candidate / "config" / "rules.yaml").is_file()
        ):
            return candidate

    return current


PROJECT_ROOT = find_project_root()
DEFAULT_RULES = PROJECT_ROOT / "config" / "rules.yaml"
DEFAULT_FIXTURES = PROJECT_ROOT / "fixtures" / "logs"


def _safe_filename(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)


def _write_json(
    report: dict[str, Any],
    output_dir: Path,
    filename: str,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)

    output_path = output_dir / filename
    output_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    return output_path


def _print_report_summary(report: dict[str, Any]) -> None:
    """Print diagnosis details and recommendations in the terminal."""

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
        print(
            f"Job        : {analysis.get('job_name')} "
            f"(ID: {analysis.get('job_id')})"
        )
        print(f"Conclusion : {analysis.get('job_conclusion')}")

        failed_steps = analysis.get("failed_steps", [])

        print(
            "Failed step : "
            + (
                ", ".join(
                    step.get("name", "(unnamed)")
                    for step in failed_steps
                )
                or "not reported"
            )
        )

        print(f"Category   : {diagnosis['category']}")
        print(f"Diagnosis  : {diagnosis['title']}")

        print(
            "Rule IDs   : "
            + (
                ", ".join(diagnosis["matched_rule_ids"])
                or "none (unknown)"
            )
        )

        print(
            "Retry hint : "
            f"{diagnosis['retry_candidate_for_future_policy_review']} "
            "(not executed)"
        )

        print("\nRecommended resolution steps:")

        recommendation = str(
            diagnosis.get(
                "recommendation",
                "Review logs manually.",
            )
        )

        for line in recommendation.splitlines():
            if line.strip():
                print(f"  {line.strip()}")

        for evidence_group in diagnosis.get("evidence", []):
            for line in evidence_group.get("lines", []):
                print(f"\nEvidence   : {line}")


def command_diagnose_fixture(args: argparse.Namespace) -> int:
    """Diagnose a local fixture using deterministic rules."""

    log_path = Path(args.file)

    if not log_path.is_file():
        print(
            f"Fixture file not found: {log_path}",
            file=sys.stderr,
        )
        return 2

    try:
        rules = load_rules(args.rules)
    except (OSError, ValueError) as exc:
        print(
            f"Rules configuration error: {exc}",
            file=sys.stderr,
        )
        return 2

    safe_log = redact_text(
        log_path.read_text(
            encoding="utf-8",
            errors="replace",
        )
    )

    matches = match_rules(safe_log, rules)

    if matches:
        primary = matches[0]

        diagnosis = {
            "category": primary.category,
            "title": primary.title,
            "matched_rule_ids": [
                match.rule_id for match in matches
            ],
            "recommendation": primary.recommendation,
            "retry_candidate_for_future_policy_review": any(
                match.retry_candidate for match in matches
            ),
            "evidence": [
                {
                    "rule_id": match.rule_id,
                    "lines": list(match.evidence),
                }
                for match in matches
            ],
        }

    else:
        diagnosis = {
            "category": "unknown",
            "title": "Unclassified CI/CD failure",
            "matched_rule_ids": [],
            "recommendation": (
                "Review the evidence and add a tested rule "
                "if the failure pattern is reusable."
            ),
            "retry_candidate_for_future_policy_review": False,
            "evidence": [],
        }

    report = {
        "schema_version": 1,
        "mode": "local_fixture_deterministic_diagnosis",
        "ai_enabled": False,
        "recovery_enabled": False,
        "fixture": log_path.name,
        "diagnosis": diagnosis,
    }

    print(json.dumps(report, indent=2, ensure_ascii=False))

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        output_path.write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        print(f"\nReport written to: {output_path}")

    return 0


def command_list_runs(args: argparse.Namespace) -> int:
    """List recent GitHub Actions workflow runs."""

    token = os.getenv("GH_TOKEN", "")

    if not token:
        print(
            "GH_TOKEN is not set. In GitHub Actions, pass "
            "${{ github.token }}; for local API testing, use "
            "a fine-grained token with Actions: read.",
            file=sys.stderr,
        )
        return 2

    api_version = os.getenv(
        "GITHUB_API_VERSION",
        "2026-03-10",
    )

    try:
        with GitHubActionsClient(
            token,
            api_version=api_version,
        ) as client:
            runs = client.list_runs(
                args.repo,
                args.limit,
            )

    except (GitHubApiError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    print(f"Recent workflow runs for {args.repo}:")

    for run in runs:
        print(
            f"- run_id={run.get('id')} "
            f"attempt={run.get('run_attempt')} "
            f"status={run.get('status')} "
            f"conclusion={run.get('conclusion')} "
            f"workflow={run.get('name')} "
            f"url={run.get('html_url')}"
        )

    if not runs:
        print("No workflow runs found.")

    return 0


def _append_github_step_summary(
    report: dict[str, Any],
) -> None:
    """Write diagnosis, recommendations and evidence to GitHub job summary."""

    summary_path = os.getenv("GITHUB_STEP_SUMMARY")

    if not summary_path:
        return

    run = report.get("workflow_run", {})

    lines = [
        "## Intelligent CI/CD Failure Recovery",
        "",
        f"- **Repository:** {report.get('repository', 'unknown')}",
        f"- **Workflow:** {run.get('name', 'unknown')}",
        (
            f"- **Run:** [{run.get('id', 'unknown')}]"
            f"({run.get('html_url', '')})"
        ),
        f"- **Conclusion:** {run.get('conclusion', 'unknown')}",
        "- **AI:** Disabled",
        "- **Recovery:** Disabled",
        "",
    ]

    for analysis in report.get("analyses", []):
        diagnosis = analysis.get("diagnosis", {})

        failed_steps = analysis.get("failed_steps", [])

        step_names = (
            ", ".join(
                step.get("name", "(unnamed)")
                for step in failed_steps
            )
            or "not reported"
        )

        lines.extend(
            [
                f"### {analysis.get('job_name', 'Unknown job')}",
                "",
                f"- **Failed step(s):** {step_names}",
                (
                    f"- **Log scope:** "
                    f"{analysis.get('log_scope', 'full job log')}"
                ),
                (
                    f"- **Category:** "
                    f"`{diagnosis.get('category', 'unknown')}`"
                ),
                (
                    f"- **Matched rules:** "
                    f"{', '.join(diagnosis.get('matched_rule_ids', [])) or 'none'}"
                ),
                "",
                "#### Recommended resolution steps",
                "",
            ]
        )

        recommendation = str(
            diagnosis.get(
                "recommendation",
                "Review logs manually.",
            )
        ).strip()

        if recommendation:
            # Preserve multiline YAML recommendations as Markdown.
            lines.extend(recommendation.splitlines())
        else:
            lines.append("Review the failed-step logs manually.")

        lines.append("")

        evidence_groups = diagnosis.get("evidence", [])

        if evidence_groups:
            lines.extend(
                [
                    "#### Supporting log evidence",
                    "",
                ]
            )

        for group in evidence_groups:
            for evidence_line in group.get("lines", []):
                safe_evidence = evidence_line.replace(
                    "`",
                    "'",
                )
                lines.append(f"> `{safe_evidence}`")

        lines.append("")

        sanitized_excerpt = analysis.get(
            "sanitized_log_excerpt"
        )

        if sanitized_excerpt:
            lines.extend(
                [
                    "#### Additional sanitized log context",
                    "",
                    "```text",
                    sanitized_excerpt,
                    "```",
                    "",
                ]
            )

    with open(
        summary_path,
        "a",
        encoding="utf-8",
    ) as summary_file:
        summary_file.write(
            "\n".join(lines) + "\n"
        )


def _validate_target_run(
    run: dict[str, Any],
    *,
    require_feature_branch: bool,
    require_failure: bool,
    expected_workflow_name: str | None = None,
) -> None:
    """Reject runs outside the configured diagnostic scope."""

    if (
        expected_workflow_name
        and run.get("name") != expected_workflow_name
    ):
        raise ValueError(
            "Refusing to analyze this run: expected workflow "
            f"{expected_workflow_name!r}, "
            f"received {run.get('name')!r}."
        )

    if require_feature_branch:
        branch = run.get("head_branch")

        # Matches feature/login, but not main or feature-login.
        if (
            not isinstance(branch, str)
            or re.fullmatch(r"feature/[^/]+", branch) is None
        ):
            raise ValueError(
                "Refusing to analyze this run: its source "
                "branch must match feature/*; "
                f"received {branch!r}."
            )

    if (
        require_failure
        and run.get("conclusion") != "failure"
    ):
        raise ValueError(
            "Refusing to analyze this run: the source "
            "workflow must have conclusion 'failure'; "
            f"received {run.get('conclusion')!r}."
        )


def command_diagnose_github(
    args: argparse.Namespace,
) -> int:
    """Retrieve failed GitHub jobs, analyze logs and write a report."""

    token = os.getenv("GH_TOKEN", "")

    if not token:
        print(
            "GH_TOKEN is not set. In GitHub Actions, pass "
            "${{ github.token }}; for local API testing, use "
            "a fine-grained token with Actions: read.",
            file=sys.stderr,
        )
        return 2

    try:
        rules = load_rules(args.rules)
    except (OSError, ValueError) as exc:
        print(
            f"Rules configuration error: {exc}",
            file=sys.stderr,
        )
        return 2

    api_version = os.getenv(
        "GITHUB_API_VERSION",
        "2026-03-10",
    )

    try:
        with GitHubActionsClient(
            token,
            api_version=api_version,
        ) as client:

            run = client.get_run(
                args.repo,
                args.run_id,
            )

            latest_attempt = int(
                run.get("run_attempt", 1)
            )

            run_attempt = (
                args.run_attempt
                if args.run_attempt is not None
                else latest_attempt
            )

            if (
                run_attempt < 1
                or run_attempt > latest_attempt
            ):
                raise ValueError(
                    "Requested run attempt must be between "
                    f"1 and {latest_attempt}."
                )

            _validate_target_run(
                run,
                require_feature_branch=args.require_feature_branch,
                require_failure=(
                    args.require_failure
                    and run_attempt == latest_attempt
                ),
                expected_workflow_name=args.expected_workflow_name,
            )

            jobs = client.list_jobs(
                args.repo,
                args.run_id,
                run_attempt=run_attempt,
            )

            failed_jobs = [
                job
                for job in jobs
                if job.get("conclusion")
                in {"failure", "timed_out", "cancelled"}
            ]

            if args.require_failure and not failed_jobs:
                raise ValueError(
                    f"Refusing to analyze attempt {run_attempt}: "
                    "no failed, timed-out, or cancelled jobs "
                    "were returned for that attempt."
                )

            analyses = []

            for job in failed_jobs:
                job_id = job.get("id")

                if not isinstance(job_id, int):
                    continue

                failed_steps = [
                    step
                    for step in (job.get("steps") or [])
                    if step.get("conclusion")
                    in {"failure", "timed_out"}
                    and isinstance(step.get("number"), int)
                ]

                step_logs: list[str] = []
                step_log_error: str | None = None

                for step in failed_steps:
                    step_number = step["number"]
                    step_name = step.get(
                        "name",
                        f"step {step_number}",
                    )

                    try:
                        step_log = client.download_step_log(
                            args.repo,
                            job_id,
                            step_number,
                            max_bytes=args.max_log_bytes,
                        )

                        step_logs.append(
                            f"===== FAILED STEP {step_number}: "
                            f"{step_name} =====\n{step_log}"
                        )

                    except GitHubApiError as exc:
                        step_log_error = str(exc)
                        break

                if (
                    failed_steps
                    and step_logs
                    and step_log_error is None
                ):
                    log_text = "\n".join(step_logs)
                    log_scope = "failed_step_logs"

                else:
                    if step_log_error:
                        print(
                            "Warning: step-specific log download "
                            f"failed for job {job_id}; falling back "
                            f"to full job log. {step_log_error}",
                            file=sys.stderr,
                        )

                    log_text = client.download_job_log(
                        args.repo,
                        job_id,
                        max_bytes=args.max_log_bytes,
                    )

                    log_scope = (
                        "full_job_log_fallback"
                        if failed_steps
                        else "full_job_log"
                    )

                analysis = analyze_job(
                    job=job,
                    raw_log=log_text,
                    rules=rules,
                )

                analysis["log_scope"] = log_scope
                analyses.append(analysis)

    except (GitHubApiError, ValueError) as exc:
        print(
            f"Error: {exc}",
            file=sys.stderr,
        )
        return 2

    if run.get("status") != "completed":
        print(
            f"Info: workflow run status is '{run.get('status')}'. "
            "This can be expected because the diagnostic "
            "workflow is analyzing a completed failed job "
            "while the source run context is still being resolved.",
            file=sys.stderr,
        )

    report_run = dict(run)
    report_run["latest_run_attempt"] = latest_attempt
    report_run["run_attempt"] = run_attempt

    report = build_report(
        repository=args.repo,
        run=report_run,
        jobs=jobs,
        analyses=analyses,
    )

    _print_report_summary(report)

    repo_slug = _safe_filename(args.repo)

    filename = (
        f"{repo_slug}-run-{args.run_id}-"
        f"attempt-{run_attempt}.json"
    )

    output_path = _write_json(
        report,
        Path(args.output_dir),
        filename,
    )

    _append_github_step_summary(report)

    print(
        "\nSanitized diagnosis report written to: "
        f"{output_path}"
    )
    print(
        "No retry or GitHub write operation was performed."
    )

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cicd-recovery",
        description=(
            "Read-only deterministic diagnosis "
            "for GitHub Actions failures."
        ),
    )

    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
    )

    list_parser = subparsers.add_parser(
        "list-runs",
        help="List recent GitHub Actions runs",
    )

    list_parser.add_argument(
        "--repo",
        required=True,
        help="Repository in OWNER/REPO format",
    )
    list_parser.add_argument(
        "--limit",
        type=int,
        default=10,
    )
    list_parser.set_defaults(
        func=command_list_runs,
    )

    diagnose_parser = subparsers.add_parser(
        "diagnose",
        help="Diagnose a GitHub Actions run",
    )

    diagnose_parser.add_argument(
        "--repo",
        required=True,
        help="Repository in OWNER/REPO format",
    )
    diagnose_parser.add_argument(
        "--run-id",
        required=True,
        type=int,
    )
    diagnose_parser.add_argument(
        "--run-attempt",
        type=int,
        help="Specific workflow attempt; defaults to current attempt",
    )
    diagnose_parser.add_argument(
        "--expected-workflow-name",
        help=(
            "Refuse to analyze a run whose workflow "
            "name does not match this value"
        ),
    )
    diagnose_parser.add_argument(
        "--rules",
        type=Path,
        default=DEFAULT_RULES,
    )
    diagnose_parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "reports",
    )
    diagnose_parser.add_argument(
        "--max-log-bytes",
        type=int,
        default=5_000_000,
    )
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
    diagnose_parser.set_defaults(
        func=command_diagnose_github,
    )

    fixture_parser = subparsers.add_parser(
        "diagnose-fixture",
        help="Diagnose a local log fixture without GitHub credentials",
    )

    fixture_parser.add_argument(
        "--file",
        required=True,
        type=Path,
    )
    fixture_parser.add_argument(
        "--rules",
        type=Path,
        default=DEFAULT_RULES,
    )
    fixture_parser.add_argument(
        "--output",
        type=Path,
    )
    fixture_parser.set_defaults(
        func=command_diagnose_fixture,
    )

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
