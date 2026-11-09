"""Load and apply deterministic failure rules."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class Rule:
    rule_id: str
    category: str
    title: str
    priority: int
    patterns: tuple[re.Pattern[str], ...]
    recommendation: str
    retry_candidate: bool


@dataclass(frozen=True)
class RuleMatch:
    rule_id: str
    category: str
    title: str
    priority: int
    evidence: tuple[str, ...]
    recommendation: str
    retry_candidate: bool


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

        rule_id = str(item["id"])
        if rule_id in seen_ids:
            raise ValueError(f"Duplicate rule id: {rule_id}")
        seen_ids.add(rule_id)

        pattern_values = item["patterns"]
        if not isinstance(pattern_values, list) or not pattern_values:
            raise ValueError(f"Rule {rule_id} must define at least one pattern")
        try:
            compiled = tuple(re.compile(str(value)) for value in pattern_values)
        except re.error as exc:
            raise ValueError(f"Invalid regex in rule {rule_id}: {exc}") from exc

        rules.append(
            Rule(
                rule_id=rule_id,
                category=str(item["category"]),
                title=str(item["title"]),
                priority=int(item["priority"]),
                patterns=compiled,
                recommendation=str(item["recommendation"]).strip(),
                retry_candidate=bool(item.get("retry_candidate", False)),
            )
        )

    return sorted(rules, key=lambda rule: rule.priority, reverse=True)


def match_rules(
    log_text: str,
    rules: list[Rule],
    *,
    max_evidence_per_rule: int = 3,
) -> list[RuleMatch]:
    """Return matching rules ordered by priority, with redacted evidence lines."""
    lines = log_text.splitlines()
    matches: list[RuleMatch] = []

    for rule in rules:
        evidence: list[str] = []
        for line in lines:
            if any(pattern.search(line) for pattern in rule.patterns):
                cleaned = line.strip()
                if cleaned and cleaned not in evidence:
                    evidence.append(cleaned[:500])
                if len(evidence) >= max_evidence_per_rule:
                    break
        if evidence:
            matches.append(
                RuleMatch(
                    rule_id=rule.rule_id,
                    category=rule.category,
                    title=rule.title,
                    priority=rule.priority,
                    evidence=tuple(evidence),
                    recommendation=rule.recommendation,
                    retry_candidate=rule.retry_candidate,
                )
            )

    return matches
