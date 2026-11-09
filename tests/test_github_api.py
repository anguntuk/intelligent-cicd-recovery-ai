import httpx

from cicd_recovery.github_api import GitHubActionsClient


def test_repository_parser() -> None:
    assert GitHubActionsClient._split_repository("octo-org/demo") == ("octo-org", "demo")


def test_invalid_repository_is_rejected() -> None:
    try:
        GitHubActionsClient._split_repository("not-a-repo")
    except ValueError as exc:
        assert "OWNER/REPO" in str(exc)
    else:
        raise AssertionError("Expected invalid repository to raise ValueError")


def test_list_jobs_targets_specific_attempt() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"jobs": []})

    transport = httpx.MockTransport(handler)
    with GitHubActionsClient("test-token", transport=transport) as client:
        assert client.list_jobs("octo-org/demo", 123, run_attempt=2) == []

    assert requests[0].url.path == (
        "/repos/octo-org/demo/actions/runs/123/attempts/2/jobs"
    )


def test_download_step_log_uses_step_specific_endpoint() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, text="npm error code ETIMEDOUT\n")

    transport = httpx.MockTransport(handler)
    with GitHubActionsClient("test-token", transport=transport) as client:
        text = client.download_step_log("octo-org/demo", 456, 4)

    assert text == "npm error code ETIMEDOUT\n"
    assert requests[0].url.path == (
        "/repos/octo-org/demo/actions/jobs/456/steps/4/logs"
    )
