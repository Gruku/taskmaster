"""Thin Linear GraphQL client (linear-003).

A pure transport layer — knows GraphQL, retries, and token sanitization.
Knows nothing about Taskmaster entities. The mapper (linear-004) is what
translates TM tasks/epics/etc to the payloads passed in here.

The runtime sync path imports this module directly (`httpx`-backed). It is
NEVER routed through the Linear MCP server, because MCP tool calls cost
LLM tokens per invocation. See IDEA-015 for the token-economy rationale.
"""
from __future__ import annotations

import time
from typing import Any, Callable

import httpx


LINEAR_API_URL = "https://api.linear.app/graphql"


class LinearAPIError(RuntimeError):
    """Raised on an API failure the client could not recover from.

    Carries a structured `permanent` flag so callers classify retry-eligibility
    from the flag, not by substring-matching the message (B-027). `permanent=True`
    means do-not-retry (auth rejected, 4xx, GraphQL-level errors); `permanent=False`
    means retry-eligible (network, 429/5xx after the client's own retries). The
    optional `status_code` carries the originating HTTP status when known.

    Defaults to `permanent=False` so any unclassified raise is treated as
    retry-eligible rather than silently parked forever.
    """

    def __init__(self, message: str, *, permanent: bool = False, status_code: int | None = None) -> None:
        super().__init__(message)
        self.permanent = permanent
        self.status_code = status_code


class LinearClient:
    """Synchronous GraphQL client for Linear.

    Retry policy: up to MAX_RETRIES attempts on 5xx, 429, and httpx network
    errors with exponential backoff. 4xx auth errors (401, 403) are permanent
    and never retried. GraphQL-level errors (200 OK with an `errors` array)
    raise LinearAPIError — they almost always indicate a bug in the caller,
    not a transient condition.

    Error messages are sanitized so the API token never appears in any
    LinearAPIError surface — important because callers may log error chains.
    """

    MAX_RETRIES = 3
    BACKOFF_BASE_SEC = 1.0

    def __init__(
        self,
        token: str,
        *,
        timeout: float = 30.0,
        _http_client: httpx.Client | None = None,
        _sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not token:
            raise ValueError("Linear API token is required")
        self._token = token
        self._http = _http_client or httpx.Client(timeout=timeout)
        self._sleep = _sleep

    # ── Public surface ─────────────────────────────────────────

    def list_teams(self) -> list[dict[str, Any]]:
        query = "query { teams { nodes { id name key } } }"
        data = self._execute(query, {})
        return list((data.get("teams") or {}).get("nodes") or [])

    def list_users(self, team_id: str) -> list[dict[str, Any]]:
        query = (
            "query($teamId: ID!) { users(filter: { teamMemberships: "
            "{ team: { id: { eq: $teamId } } } }) { nodes { id name email } } }"
        )
        data = self._execute(query, {"teamId": team_id})
        return list((data.get("users") or {}).get("nodes") or [])

    def list_issue_statuses(self, team_id: str) -> list[dict[str, Any]]:
        query = (
            "query($teamId: String!) { team(id: $teamId) "
            "{ states { nodes { id name type } } } }"
        )
        data = self._execute(query, {"teamId": team_id})
        team = data.get("team") or {}
        return list((team.get("states") or {}).get("nodes") or [])

    def list_labels(self, team_id: str) -> list[dict[str, Any]]:
        query = (
            "query($teamId: String!) { team(id: $teamId) "
            "{ labels { nodes { id name } } } }"
        )
        data = self._execute(query, {"teamId": team_id})
        team = data.get("team") or {}
        return list((team.get("labels") or {}).get("nodes") or [])

    def issue_upsert(self, team_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Create or update an issue based on `payload.id` presence.

        Returns the resulting `{id, identifier, ...}` from Linear so callers
        can stash the id on the local Tracker file for subsequent updates.
        """
        if payload.get("id"):
            issue_id = payload.pop("id")
            mutation = (
                "mutation($id: String!, $input: IssueUpdateInput!) "
                "{ issueUpdate(id: $id, input: $input) "
                "{ issue { id identifier } } }"
            )
            data = self._execute(mutation, {"id": issue_id, "input": payload})
            return (data.get("issueUpdate") or {}).get("issue") or {}

        input_payload = {**payload, "teamId": team_id}
        mutation = (
            "mutation($input: IssueCreateInput!) "
            "{ issueCreate(input: $input) { issue { id identifier } } }"
        )
        data = self._execute(mutation, {"input": input_payload})
        return (data.get("issueCreate") or {}).get("issue") or {}

    def project_upsert(self, team_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        if payload.get("id"):
            project_id = payload.pop("id")
            mutation = (
                "mutation($id: String!, $input: ProjectUpdateInput!) "
                "{ projectUpdate(id: $id, input: $input) "
                "{ project { id name } } }"
            )
            data = self._execute(mutation, {"id": project_id, "input": payload})
            return (data.get("projectUpdate") or {}).get("project") or {}

        input_payload = {**payload, "teamIds": [team_id]}
        mutation = (
            "mutation($input: ProjectCreateInput!) "
            "{ projectCreate(input: $input) { project { id name } } }"
        )
        data = self._execute(mutation, {"input": input_payload})
        return (data.get("projectCreate") or {}).get("project") or {}

    def comment_create(self, issue_id: str, body: str) -> dict[str, Any]:
        """v2-scope surface — stubbed in v1 so the worker can be wired
        without conditional imports later."""
        mutation = (
            "mutation($input: CommentCreateInput!) "
            "{ commentCreate(input: $input) { comment { id } } }"
        )
        data = self._execute(mutation, {"input": {"issueId": issue_id, "body": body}})
        return (data.get("commentCreate") or {}).get("comment") or {}

    # ── Transport + retry ──────────────────────────────────────

    def _execute(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        headers = {
            "Authorization": self._token,
            "Content-Type": "application/json",
        }
        payload = {"query": query, "variables": variables}

        last_error: str | None = None
        for attempt in range(self.MAX_RETRIES):
            try:
                resp = self._http.post(LINEAR_API_URL, json=payload, headers=headers)
            except httpx.RequestError as e:
                last_error = self._sanitize(str(e) or repr(e))
                if attempt < self.MAX_RETRIES - 1:
                    self._sleep(self.BACKOFF_BASE_SEC * (2 ** attempt))
                    continue
                # Network errors are retry-eligible — the blip may clear later.
                raise LinearAPIError(
                    f"network error after {self.MAX_RETRIES} attempts: {last_error}",
                    permanent=False,
                )

            sc = resp.status_code

            # Permanent auth failures — never retry.
            if sc in (401, 403):
                raise LinearAPIError(
                    f"Linear rejected the API token (HTTP {sc}); "
                    f"re-issue at https://linear.app/settings/api",
                    permanent=True, status_code=sc,
                )

            # Other 4xx — bad request / not found / unprocessable: permanent.
            if 400 <= sc < 500 and sc != 429:
                body = self._sanitize(resp.text or f"HTTP {sc}")
                raise LinearAPIError(
                    f"Linear request failed (HTTP {sc}): {body}",
                    permanent=True, status_code=sc,
                )

            # 429 + 5xx — retryable.
            if sc == 429 or sc >= 500:
                last_error = f"HTTP {sc}"
                if attempt < self.MAX_RETRIES - 1:
                    self._sleep(self.BACKOFF_BASE_SEC * (2 ** attempt))
                    continue
                raise LinearAPIError(
                    f"Linear server error after {self.MAX_RETRIES} attempts: {last_error}",
                    permanent=False, status_code=sc,
                )

            # 2xx — parse the GraphQL envelope.
            try:
                body = resp.json()
            except ValueError as e:
                # Malformed JSON on a 2xx is usually a gateway/proxy hiccup — retry-eligible.
                raise LinearAPIError(
                    f"malformed JSON from Linear: {self._sanitize(str(e))}",
                    permanent=False, status_code=sc,
                )

            if body.get("errors"):
                # GraphQL-level errors almost always indicate a caller bug — permanent.
                raise LinearAPIError(
                    f"GraphQL errors from Linear: {self._sanitize(str(body['errors']))}",
                    permanent=True, status_code=sc,
                )
            return body.get("data") or {}

        # Defensive: loop should always exit via return or raise.
        raise LinearAPIError(f"unreachable retry exit (last_error={last_error})", permanent=True)

    def _sanitize(self, msg: str) -> str:
        """Redact any literal occurrence of the API token from error strings."""
        if self._token and self._token in msg:
            return msg.replace(self._token, "<token-redacted>")
        return msg
