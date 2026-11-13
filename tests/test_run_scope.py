import pytest

from cicd_recovery.cli import _validate_target_run, build_parser


def test_feature_branch_failure_is_accepted() -> None:
    _validate_target_run(
        {"head_branch": "feature/login", "conclusion": "failure"},
        require_feature_branch=True,
        require_failure=True,
    )


@pytest.mark.parametrize("branch", ["main", "develop", "feature-login", "feature/"])
def test_non_feature_branch_is_rejected(branch: str) -> None:
    with pytest.raises(ValueError, match=r"feature/\*"):
        _validate_target_run(
            {"head_branch": branch, "conclusion": "failure"},
            require_feature_branch=True,
            require_failure=True,
        )


def test_nested_feature_branch_is_accepted() -> None:
    _validate_target_run(
        {"head_branch": "feature/team/login", "conclusion": "failure"},
        require_feature_branch=True,
        require_failure=True,
    )


def test_successful_workflow_is_rejected() -> None:
    with pytest.raises(ValueError, match="conclusion 'failure'"):
        _validate_target_run(
            {"head_branch": "feature/login", "conclusion": "success"},
            require_feature_branch=True,
            require_failure=True,
        )


def test_missing_branch_is_rejected() -> None:
    with pytest.raises(ValueError, match=r"feature/\*"):
        _validate_target_run(
            {"conclusion": "failure"},
            require_feature_branch=True,
            require_failure=True,
        )


def test_expected_workflow_name_is_enforced() -> None:
    _validate_target_run(
        {"name": "Frontend CI", "head_branch": "feature/login", "conclusion": "failure"},
        require_feature_branch=True,
        require_failure=True,
        expected_workflow_name="Frontend CI",
    )


def test_unexpected_workflow_name_is_rejected() -> None:
    with pytest.raises(ValueError, match="expected workflow"):
        _validate_target_run(
            {"name": "Unrelated Workflow", "head_branch": "feature/login", "conclusion": "failure"},
            require_feature_branch=True,
            require_failure=True,
            expected_workflow_name="Frontend CI",
        )


def test_diagnose_parser_exposes_expected_workflow_name_guard() -> None:
    args = build_parser().parse_args(
        ["diagnose", "--repo", "octo-org/demo", "--run-id", "123", "--expected-workflow-name", "Frontend CI"]
    )
    assert args.expected_workflow_name == "Frontend CI"
