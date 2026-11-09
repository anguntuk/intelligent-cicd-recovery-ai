from pathlib import Path

from cicd_recovery.rules import load_rules, match_rules

ROOT = Path(__file__).resolve().parents[1]
RULES = load_rules(ROOT / "config" / "rules.yaml")
FIXTURES = ROOT / "fixtures" / "logs"


def test_known_fixtures_match_expected_rules() -> None:
    expected = {
        "registry_timeout.log": "npm_registry_timeout",
        "registry_auth.log": "npm_registry_auth",
        "dependency_conflict.log": "npm_dependency_resolution",
        "lint_failure.log": "lint_failure",
        "typecheck_failure.log": "typescript_compile_error",
        "unit_test_failure.log": "unit_test_failure",
        "build_failure.log": "frontend_build_failure",
        "out_of_memory.log": "javascript_heap_exhaustion",
        "security_gate_failure.log": "security_gate_failure",
    }
    for filename, rule_id in expected.items():
        text = (FIXTURES / filename).read_text(encoding="utf-8")
        matches = match_rules(text, RULES)
        assert matches, filename
        assert matches[0].rule_id == rule_id, filename


def test_unknown_fixture_does_not_force_a_classification() -> None:
    text = (FIXTURES / "unknown_failure.log").read_text(encoding="utf-8")
    assert match_rules(text, RULES) == []
