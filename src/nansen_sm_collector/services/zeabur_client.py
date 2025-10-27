from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx


class ZeaburAPIError(RuntimeError):
    """Raised when Zeabur API requests fail."""


class ZeaburAPIClient:
    """Lightweight Zeabur API helper used by the Telegram dashboard."""

    def __init__(
        self,
        *,
        base_url: str,
        api_token: str,
        project_id: Optional[str] = None,
        service_id: Optional[str] = None,
        hourly_job_id: Optional[str] = None,
        pipeline_command: str,
        run_job_endpoint: Optional[str] = None,
        enable_job_endpoint: Optional[str] = None,
        disable_job_endpoint: Optional[str] = None,
        job_status_endpoint: Optional[str] = None,
        timeout: float = 15.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = api_token
        self._project_id = project_id
        self._service_id = service_id
        self._hourly_job_id = hourly_job_id
        self._pipeline_command = pipeline_command
        self._run_job_endpoint = run_job_endpoint
        self._enable_job_endpoint = enable_job_endpoint
        self._disable_job_endpoint = disable_job_endpoint
        self._job_status_endpoint = job_status_endpoint
        self._timeout = timeout

    async def trigger_pipeline_once(self, command: Optional[str] = None) -> dict[str, Any]:
        endpoint = self._run_job_endpoint or self._default_run_job_endpoint()
        payload: dict[str, Any] = {"command": command or self._pipeline_command}
        if self._service_id:
            payload.setdefault("serviceId", self._service_id)
        return await self._request("POST", endpoint, json=payload)

    async def enable_hourly_scheduler(self, duration_hours: int, command: Optional[str] = None) -> dict[str, Any]:
        endpoint = self._enable_job_endpoint or self._default_enable_job_endpoint()
        if duration_hours <= 0:
            raise ZeaburAPIError("duration_hours must be positive")
        expires_at = datetime.now(timezone.utc) + timedelta(hours=duration_hours)
        payload: dict[str, Any] = {
            "command": command or self._pipeline_command,
            "schedule": {
                "type": "cron",
                "expression": "0 * * * *",
                "enabled": True,
                "expiresAt": expires_at.isoformat().replace("+00:00", "Z"),
            },
        }
        if self._service_id:
            payload.setdefault("serviceId", self._service_id)
        return await self._request("PUT", endpoint, json=payload)

    async def disable_hourly_scheduler(self) -> dict[str, Any]:
        endpoint = self._disable_job_endpoint or self._default_disable_job_endpoint()
        payload = {"schedule": {"enabled": False}}
        return await self._request("PUT", endpoint, json=payload)

    async def fetch_scheduler_status(self) -> dict[str, Any]:
        endpoint = self._job_status_endpoint or self._default_job_status_endpoint()
        return await self._request("GET", endpoint)

    def _default_run_job_endpoint(self) -> str:
        if not self._project_id:
            raise ZeaburAPIError("project_id is required to trigger jobs")
        return f"/projects/{self._project_id}/jobs"

    def _default_enable_job_endpoint(self) -> str:
        if not (self._project_id and self._hourly_job_id):
            raise ZeaburAPIError("project_id and hourly_job_id are required to enable scheduler")
        return f"/projects/{self._project_id}/jobs/{self._hourly_job_id}"

    def _default_disable_job_endpoint(self) -> str:
        if not (self._project_id and self._hourly_job_id):
            raise ZeaburAPIError("project_id and hourly_job_id are required to disable scheduler")
        return f"/projects/{self._project_id}/jobs/{self._hourly_job_id}"

    def _default_job_status_endpoint(self) -> str:
        if not (self._project_id and self._hourly_job_id):
            raise ZeaburAPIError("project_id and hourly_job_id are required to fetch scheduler status")
        return f"/projects/{self._project_id}/jobs/{self._hourly_job_id}"

    async def _request(
        self,
        method: str,
        endpoint: str,
        *,
        json: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        if not self._token:
            raise ZeaburAPIError("ZEABUR_API_TOKEN is not configured")
        url = self._compose_url(endpoint)
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.request(method.upper(), url, headers=headers, json=json)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ZeaburAPIError(
                f"Zeabur API request failed: {exc.response.status_code} {exc.response.text}"
            ) from exc
        if not response.content:
            return {}
        try:
            return response.json()
        except json.JSONDecodeError as exc:
            raise ZeaburAPIError("Zeabur API returned non-JSON response") from exc

    def _compose_url(self, endpoint: str) -> str:
        if endpoint.startswith("http://") or endpoint.startswith("https://"):
            return endpoint
        path = endpoint if endpoint.startswith("/") else f"/{endpoint}"
        return f"{self._base_url}{path}"
