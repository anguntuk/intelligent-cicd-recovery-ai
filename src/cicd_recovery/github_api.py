"""Read-only GitHub Actions REST API adapter."""

from __future__ import annotations

import json
from typing import Any

import httpx


class GitHubApiError(RuntimeError):
    """Raised when the GitHub API request or response cannot be used."""


class GitHubActionsClient:
    def __init__(
        self,
        token: str,
        *,
        api_version: str = "2026-03-10",
        timeout_seconds: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not token.strip():
            raise ValueError("GitHub token is empty")
        self._client = httpx.Client(
            base_url="https://api.github.com",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token.strip()}",
                "X-GitHub-Api-Version": api_version,
                "User-Agent": "intelligent-cicd-recovery/0.3.0",
            },
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=True,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "GitHubActionsClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _get_json(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            response = self._client.get(path, params=params)
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status == 401:
                hint = "Token is invalid or expired."
            elif status == 403:
                hint = "Check repository access, Actions: read permission, SSO and API rate limits."
            elif status == 404:
                hint = "Repository or workflow run was not found, or the token cannot access it."
            else:
                hint = "Check GitHub API status and request details."
            raise GitHubApiError(f"GitHub API returned HTTP {status}. {hint}") from exc
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            raise GitHubApiError(f"GitHub API request failed: {exc}") from exc

        if not isinstance(payload, dict):
            raise GitHubApiError("GitHub API returned an unexpected JSON response")
        return payload

    def get_run(self, repository: str, run_id: int) -> dict[str, Any]:
        owner, repo = self._split_repository(repository)
        return self._get_json(f"/repos/{owner}/{repo}/actions/runs/{run_id}")

    def list_runs(self, repository: str, limit: int = 10) -> list[dict[str, Any]]:
        owner, repo = self._split_repository(repository)
        payload = self._get_json(
            f"/repos/{owner}/{repo}/actions/runs",
            params={"per_page": min(max(limit, 1), 100)},
        )
        runs = payload.get("workflow_runs", [])
        if not isinstance(runs, list):
            raise GitHubApiError("GitHub API returned an invalid workflow_runs field")
        return runs

    def list_jobs(
        self, repository: str, run_id: int, run_attempt: int | None = None
    ) -> list[dict[str, Any]]:
        owner, repo = self._split_repository(repository)
        if run_attempt is not None:
            if run_attempt < 1:
                raise ValueError("run_attempt must be >= 1")
            path = (
                f"/repos/{owner}/{repo}/actions/runs/{run_id}/attempts/"
                f"{run_attempt}/jobs"
            )
            params = {"per_page": 100}
        else:
            path = f"/repos/{owner}/{repo}/actions/runs/{run_id}/jobs"
            params = {"per_page": 100, "filter": "latest"}
        payload = self._get_json(path, params=params)
        jobs = payload.get("jobs", [])
        if not isinstance(jobs, list):
            raise GitHubApiError("GitHub API returned an invalid jobs field")
        return jobs

    def download_job_log(self, repository: str, job_id: int, max_bytes: int = 5_000_000) -> str:
        owner, repo = self._split_repository(repository)
        path = f"/repos/{owner}/{repo}/actions/jobs/{job_id}/logs"
        try:
            with self._client.stream("GET", path) as response:
                response.raise_for_status()
                chunks: list[bytes] = []
                total = 0
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        raise GitHubApiError(
                            f"Job log exceeded the configured {max_bytes:,}-byte limit"
                        )
                    chunks.append(chunk)
            return b"".join(chunks).decode("utf-8", errors="replace")
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status == 401:
                hint = "Token is invalid or expired."
            elif status == 403:
                hint = "Check Actions: read permission, repository access, SSO and API rate limits."
            elif status == 404:
                hint = "Job log is missing, expired, or inaccessible."
            else:
                hint = "Check GitHub API status and request details."
            raise GitHubApiError(f"Unable to download job log (HTTP {status}). {hint}") from exc
        except httpx.HTTPError as exc:
            raise GitHubApiError(f"Unable to download job log: {exc}") from exc

    def download_step_log(
        self,
        repository: str,
        job_id: int,
        step_number: int,
        max_bytes: int = 2_000_000,
    ) -> str:
        """Download logs for one workflow step (Actions: read)."""
        owner, repo = self._split_repository(repository)
        if step_number < 1:
            raise ValueError("step_number must be >= 1")
        path = f"/repos/{owner}/{repo}/actions/jobs/{job_id}/steps/{step_number}/logs"
        try:
            with self._client.stream("GET", path) as response:
                response.raise_for_status()
                chunks: list[bytes] = []
                total = 0
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        raise GitHubApiError(
                            f"Step log exceeded the configured {max_bytes:,}-byte limit"
                        )
                    chunks.append(chunk)
            return b"".join(chunks).decode("utf-8", errors="replace")
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status == 401:
                hint = "Token is invalid or expired."
            elif status == 403:
                hint = "Check Actions: read permission, repository access, SSO and API rate limits."
            elif status == 404:
                hint = "Step log is missing, expired, or inaccessible."
            else:
                hint = "Check GitHub API status and request details."
            raise GitHubApiError(f"Unable to download step log (HTTP {status}). {hint}") from exc
        except httpx.HTTPError as exc:
            raise GitHubApiError(f"Unable to download step log: {exc}") from exc

    @staticmethod
    def _split_repository(repository: str) -> tuple[str, str]:
        parts = repository.strip().split("/")
        if len(parts) != 2 or not all(parts):
            raise ValueError("Repository must be in OWNER/REPO format")
        return parts[0], parts[1]
