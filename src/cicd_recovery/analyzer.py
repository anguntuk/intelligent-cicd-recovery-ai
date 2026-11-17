"""Deterministic diagnosis and read-only remediation-policy planning."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from cicd_recovery.policy import evaluate_remediation_policy
from cicd_recovery.redaction import redact_text
from cicd_recovery.rules import Rule, RuleMatch, match_rules

UNKNOWN_RECOMMENDATION = (
    "No configured rule matched the available evidence. This failure is routed to manual "
    "engineering investigation only. Review the failed step and surrounding sanitized logs, "
    "confirm the root cause, and add a tested rule if this pattern is reusable."
)

UNKNOWN_TRIAGE_ACTIONS = (
    "Identify the first failing command and capture its exit code and relevant step context.",
    "Review the sanitized log excerpt and compare it with the source commit and recent changes.",
    "Check dependent services, credentials, runner state, configuration and external systems "
    "that the failed step relies on.",
    "Reproduce the issue safely outside the production deployment path where possible.",
    "After the root cause is confirmed, propose a versioned rule and regression fixture for "
    "engineering review; do not promote a rule directly from this incident.",
)


def analyze_job(
    *, job: dict[str, Any], raw_log: str, rules: list[Rule],
    max_excerpt_chars: int = 3000, remediation_attempts_used: int = 0,
) -> dict[str, Any]:
    safe_log = redact_text(raw_log)
    matches: list[RuleMatch] = match_rules(safe_log, rules)
    steps = job.get("steps") or []
    failed_steps = [
        {"name": step.get("name", "(unnamed step)"), "conclusion": step.get("conclusion")}
        for step in steps
        if step.get("conclusion") in {"failure", "timed_out", "cancelled"}
    ]

    unknown_triage: dict[str, Any] | None = None

    if matches:
        primary = matches[0]
        category = primary.category
        title = primary.title
        recommendation = primary.recommendation
        evidence = [
            {"rule_id": match.rule_id, "lines": list(match.evidence)} for match in matches
        ]
        rule_ids = [match.rule_id for match in matches]
        retry_candidate = any(match.retry_candidate for match in matches)
        remediation_spec = primary.remediation
        confidence = "rule_match"
    else:
        category = "unknown"
        title = "Unclassified CI/CD failure"
        recommendation = UNKNOWN_RECOMMENDATION
        evidence = []
        rule_ids = []
        retry_candidate = False
        remediation_spec = None
        confidence = "unknown"
        unknown_triage = {
            "route": "manual_engineering_investigation",
            "status": "requires_investigation",
            "reason": "No configured rule matched the sanitized failure evidence.",
            "approval_required": False,
            "remediation_allowed": False,
            "commit_allowed": False,
            "push_allowed": False,
            "ci_retry_allowed": False,
            "ai_enabled": False,
            "recommended_actions": list(UNKNOWN_TRIAGE_ACTIONS),
        }

    plan = evaluate_remediation_policy(
        retry_candidate=retry_candidate,
        remediation=remediation_spec,
        attempts_used=remediation_attempts_used,
    ).to_dict()
    nonempty_lines = [line.strip() for line in safe_log.splitlines() if line.strip()]
    excerpt = "\n".join(nonempty_lines[-40:])[-max_excerpt_chars:]

    return {
        "job_id": job.get("id"),
        "job_name": job.get("name", "(unnamed job)"),
        "job_conclusion": job.get("conclusion"),
        "failed_steps": failed_steps,
        "diagnosis": {
            "category": category,
            "title": title,
            "confidence": confidence,
            "matched_rule_ids": rule_ids,
            "evidence": evidence,
            "recommendation": recommendation,
            "retry_candidate_for_future_policy_review": retry_candidate,
            "remediation_plan": plan,
            "unknown_triage": unknown_triage,
        },
        "sanitized_log_excerpt": excerpt,
    }


def build_report(
    *, repository: str, run: dict[str, Any], jobs: list[dict[str, Any]],
    analyses: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schema_version": 2,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "read_only_deterministic_diagnosis_and_remediation_policy",
        "ai_enabled": False,
        "recovery_enabled": False,
        "repository": repository,
        "workflow_run": {
            "id": run.get("id"), "name": run.get("name"),
            "workflow_id": run.get("workflow_id"), "event": run.get("event"),
            "status": run.get("status"), "conclusion": run.get("conclusion"),
            "head_branch": run.get("head_branch"), "head_sha": run.get("head_sha"),
            "run_number": run.get("run_number"),
            "run_attempt": run.get("run_attempt", 1),
            "latest_run_attempt": run.get("latest_run_attempt", run.get("run_attempt", 1)),
            "html_url": run.get("html_url"),
        },
        "job_count": len(jobs),
        "failed_job_count": len(analyses),
        "analyses": analyses,
        "routing": {
            "unknown_failure_present": any(
                item.get("diagnosis", {}).get("category") == "unknown" for item in analyses
            ),
            "unknown_failure_approval_gate": False,
            "unknown_failure_action": "manual_engineering_investigation_only",
            "unknown_failure_remediation_allowed": False,
        },
        "limitations": [
            "Diagnosis uses deterministic configured rules; no AI model is invoked.",
            "A matching signature is a diagnostic hypothesis and must be checked against context.",
            "Remediation is policy-preview only: no code fix is generated or applied.",
            "Validation commands are reported but not executed.",
            "Unknown failures are routed to manual engineering investigation and never enter remediation approval gates.",
            "No approval request, retry, workflow mutation, deployment, commit, or push is performed by diagnosis.",
            "Attempt counts are not persisted across workflow runs yet.",
            "Log redaction is best-effort and does not replace platform secret masking or review.",
        ],
    }
