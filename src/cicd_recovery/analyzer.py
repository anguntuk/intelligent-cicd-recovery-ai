"""Deterministic diagnosis and read-only remediation-policy planning."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from cicd_recovery.policy import evaluate_remediation_policy
from cicd_recovery.redaction import redact_text
from cicd_recovery.rules import Rule, RuleMatch, match_rules

UNKNOWN_RECOMMENDATION = (
    "No configured rule matched the available evidence. Review the failed step and surrounding "
    "logs, confirm the root cause, then add a tested rule if this pattern is reusable."
)


def analyze_job(
    *, job: dict[str, Any], raw_log: str, rules: list[Rule],
    max_excerpt_chars: int = 500, remediation_attempts_used: int = 0,
) -> dict[str, Any]:
    safe_log = redact_text(raw_log)
    matches: list[RuleMatch] = match_rules(safe_log, rules)
    steps = job.get("steps") or []
    failed_steps = [
        {"name": step.get("name", "(unnamed step)"), "conclusion": step.get("conclusion")}
        for step in steps
        if step.get("conclusion") in {"failure", "timed_out", "cancelled"}
    ]

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

    plan = evaluate_remediation_policy(
        retry_candidate=retry_candidate,
        remediation=remediation_spec,
        attempts_used=remediation_attempts_used,
    ).to_dict()
    nonempty_lines = [line.strip() for line in safe_log.splitlines() if line.strip()]
    excerpt = "\n".join(nonempty_lines[-8:])[-max_excerpt_chars:]

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
        "limitations": [
            "Diagnosis uses deterministic configured rules; no AI model is invoked.",
            "A matching signature is a diagnostic hypothesis and must be checked against context.",
            "Remediation is policy-preview only: no code fix is generated or applied.",
            "Validation commands are reported but not executed.",
            "No approval request, retry, workflow mutation, deployment, commit, or push is performed.",
            "Attempt counts are not persisted across workflow runs yet.",
            "Log redaction is best-effort and does not replace platform secret masking or review.",
        ],
    }
