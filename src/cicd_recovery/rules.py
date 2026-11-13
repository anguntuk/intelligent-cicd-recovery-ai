"""Load, validate and apply deterministic CI/CD failure rules."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

ALLOWED_REMEDIATION_RISKS = {"low", "medium", "high"}
ALLOWED_REMEDIATION_MODES = {"assisted", "recommend_only"}
ALLOWED_TARGET_BRANCHES = {"originating_feature_branch"}
# Metadata-only validation commands in this phase. They are never executed here.
ALLOWED_VALIDATION_COMMANDS = {
    "npm test", "npm run build", "dotnet test", "dotnet build", "python -m pytest",
}


@dataclass(frozen=True)
class RemediationSpec:
    enabled: bool
    risk: str
    mode: str
    approval_required: bool
    max_attempts: int
    validation: tuple[str, ...]
    target_branch: str
    commit_only_after_validation: bool


@dataclass(frozen=True)
class Rule:
    rule_id: str
    category: str
    title: str
    priority: int
    patterns: tuple[re.Pattern[str], ...]
    recommendation: str
    retry_candidate: bool
    remediation: RemediationSpec | None = None


@dataclass(frozen=True)
class RuleMatch:
    rule_id: str
    category: str
    title: str
    priority: int
    evidence: tuple[str, ...]
    recommendation: str
    retry_candidate: bool
    remediation: RemediationSpec | None = None


def _require_bool(value: Any, field: str, rule_id: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"Rule {rule_id}: remediation field '{field}' must be true or false")
    return value


def _parse_remediation(raw: Any, rule_id: str) -> RemediationSpec | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError(f"Rule {rule_id}: 'remediation' must be a YAML mapping")

    enabled = _require_bool(raw.get("enabled", False), "enabled", rule_id)
    risk = str(raw.get("risk", "high")).strip().lower()
    if risk not in ALLOWED_REMEDIATION_RISKS:
        raise ValueError(f"Rule {rule_id}: remediation risk must be one of {sorted(ALLOWED_REMEDIATION_RISKS)}")

    mode = str(raw.get("mode", "recommend_only")).strip().lower()
    if mode not in ALLOWED_REMEDIATION_MODES:
        raise ValueError(f"Rule {rule_id}: remediation mode must be one of {sorted(ALLOWED_REMEDIATION_MODES)}")

    approval_required = _require_bool(raw.get("approval_required", True), "approval_required", rule_id)
    attempts = raw.get("max_attempts", 1)
    if isinstance(attempts, bool) or not isinstance(attempts, int) or not 1 <= attempts <= 3:
        raise ValueError(f"Rule {rule_id}: remediation max_attempts must be an integer from 1 to 3")

    validation_raw = raw.get("validation", [])
    if not isinstance(validation_raw, list) or not all(isinstance(v, str) and v.strip() for v in validation_raw):
        raise ValueError(f"Rule {rule_id}: remediation validation must be a list of non-empty commands")
    validation = tuple(v.strip() for v in validation_raw)
    unsupported = sorted(set(validation) - ALLOWED_VALIDATION_COMMANDS)
    if unsupported:
        raise ValueError(
            f"Rule {rule_id}: unsupported validation command(s): {', '.join(unsupported)}. "
            "Add a reviewed command identifier to the application allowlist first."
        )

    target = str(raw.get("target_branch", "originating_feature_branch")).strip()
    if target not in ALLOWED_TARGET_BRANCHES:
        raise ValueError(f"Rule {rule_id}: target_branch must be 'originating_feature_branch'")

    commit_after_validation = _require_bool(
        raw.get("commit_only_after_validation", True), "commit_only_after_validation", rule_id
    )
    if enabled and not commit_after_validation:
        raise ValueError(f"Rule {rule_id}: enabled remediation must require validation before commit")
    if enabled and not validation:
        raise ValueError(f"Rule {rule_id}: enabled remediation must define validation commands")

    return RemediationSpec(
        enabled=enabled,
        risk=risk,
        mode=mode,
        approval_required=approval_required,
        max_attempts=attempts,
        validation=validation,
        target_branch=target,
        commit_only_after_validation=commit_after_validation,
    )


def load_rules(path: str | Path) -> list[Rule]:
    rule_path = Path(path)
    if not rule_path.is_file():
        raise FileNotFoundError(f"Rules file not found: {rule_path}")
    try:
        document: dict[str, Any] = yaml.safe_load(rule_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML in rules file {rule_path}: {exc}") from exc

    raw_rules = document.get("rules")
    if not isinstance(raw_rules, list) or not raw_rules:
        raise ValueError("Rules file must contain a non-empty 'rules' list")

    rules: list[Rule] = []
    seen_ids: set[str] = set()
    for item in raw_rules:
        if not isinstance(item, dict):
            raise ValueError("Each rule must be a YAML mapping")
        required = ("id", "category", "title", "priority", "patterns", "recommendation")
        missing = [key for key in required if key not in item]
        if missing:
            raise ValueError(f"Rule is missing required fields: {', '.join(missing)}")

        rule_id = str(item["id"]).strip()
        if not rule_id or rule_id in seen_ids:
            raise ValueError(f"Invalid or duplicate rule id: {rule_id!r}")
        seen_ids.add(rule_id)

        raw_patterns = item["patterns"]
        if not isinstance(raw_patterns, list) or not raw_patterns:
            raise ValueError(f"Rule {rule_id} must define at least one pattern")
        try:
            patterns = tuple(re.compile(str(pattern)) for pattern in raw_patterns)
        except re.error as exc:
            raise ValueError(f"Invalid regex in rule {rule_id}: {exc}") from exc

        retry_candidate = item.get("retry_candidate", False)
        if not isinstance(retry_candidate, bool):
            raise ValueError(f"Rule {rule_id}: retry_candidate must be true or false")
        remediation = _parse_remediation(item.get("remediation"), rule_id)
        if remediation and remediation.enabled and not retry_candidate:
            raise ValueError(
                f"Rule {rule_id}: enabled remediation requires retry_candidate: true. "
                "This flag marks a candidate; it does not authorize execution."
            )

        recommendation = item["recommendation"]
        if not isinstance(recommendation, str) or not recommendation.strip():
            raise ValueError(f"Rule {rule_id}: recommendation must be a non-empty string")
        try:
            priority = int(item["priority"])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Rule {rule_id}: priority must be an integer") from exc

        rules.append(Rule(
            rule_id=rule_id,
            category=str(item["category"]),
            title=str(item["title"]),
            priority=priority,
            patterns=patterns,
            recommendation=recommendation.strip(),
            retry_candidate=retry_candidate,
            remediation=remediation,
        ))
    return sorted(rules, key=lambda rule: rule.priority, reverse=True)


def match_rules(log_text: str, rules: list[Rule], *, max_evidence_per_rule: int = 3) -> list[RuleMatch]:
    """Return matching rules ordered by priority, with supporting evidence lines."""
    matches: list[RuleMatch] = []
    for rule in rules:
        evidence: list[str] = []
        for line in log_text.splitlines():
            if any(pattern.search(line) for pattern in rule.patterns):
                cleaned = line.strip()
                if cleaned and cleaned not in evidence:
                    evidence.append(cleaned[:500])
                if len(evidence) >= max_evidence_per_rule:
                    break
        if evidence:
            matches.append(RuleMatch(
                rule_id=rule.rule_id,
                category=rule.category,
                title=rule.title,
                priority=rule.priority,
                evidence=tuple(evidence),
                recommendation=rule.recommendation,
                retry_candidate=rule.retry_candidate,
                remediation=rule.remediation,
            ))
    return matches
