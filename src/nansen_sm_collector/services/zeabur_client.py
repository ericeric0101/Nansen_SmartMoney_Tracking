from __future__ import annotations

import json
from typing import Any, Optional, Sequence

import httpx


class ZeaburAPIError(RuntimeError):
    """Raised when Zeabur API requests fail."""


class ZeaburAPIClient:
    """GraphQL-based Zeabur helper for the Telegram dashboard."""

    _EXECUTE_COMMAND_MUTATION = (
        "mutation ExecuteCommand($serviceId: ObjectID!, $environmentId: ObjectID!, $command: [String!]!)"
        " { executeCommand(serviceID: $serviceId, environmentID: $environmentId, command: $command) "
        "{ exitCode output } }"
    )

    def __init__(
        self,
        *,
        base_url: str,
        api_token: str,
        project_id: Optional[str] = None,
        service_id: Optional[str] = None,
        environment_id: Optional[str] = None,
        pipeline_command: str,
        timeout: float = 20.0,
    ) -> None:
        self._graphql_url = base_url
        self._token = api_token
        self._project_id = project_id
        self._service_id = service_id
        self._environment_id = environment_id
        self._pipeline_command = pipeline_command
        self._timeout = timeout

    async def trigger_pipeline_once(self, command: Optional[str] = None) -> dict[str, Any]:
        cmd = command or self._pipeline_command
        return await self._execute_bash(cmd)

    async def start_scheduler(self, interval_hours: int) -> dict[str, Any]:
        if interval_hours <= 0:
            raise ZeaburAPIError("interval_hours must be positive")
        seconds = interval_hours * 3600
        command = (
            "RUN_LOOP_INTERVAL_SECONDS="
            f"{seconds} nohup python scripts/run_loop.py > /tmp/nansen_run_loop.log 2>&1 & echo RUN_LOOP_STARTED"
        )
        return await self._execute_bash(command)

    async def stop_scheduler(self) -> dict[str, Any]:
        command = 'pkill -f "python scripts/run_loop.py" || true'
        return await self._execute_bash(command)

    async def fetch_scheduler_status(self) -> dict[str, Any]:
        command = (
            'if pgrep -f "python scripts/run_loop.py" >/dev/null; '
            'then echo "running"; else echo "idle"; fi'
        )
        result = await self._execute_bash(command)
        output_text = result.get("output", "")
        lines = [line.strip() for line in output_text.replace("\r\n", "\n").split("\n") if line.strip()]
        return {
            "status": lines[-1] if lines else "unknown",
            "exit_code": result.get("exit_code"),
            "raw": result,
        }

    async def execute_command(self, command: Sequence[str]) -> dict[str, Any]:
        if not self._service_id or not self._environment_id:
            raise ZeaburAPIError("service_id and environment_id must be configured")
        payload = {
            "query": self._EXECUTE_COMMAND_MUTATION,
            "variables": {
                "serviceId": self._service_id,
                "environmentId": self._environment_id,
                "command": list(command),
            },
        }
        data = await self._graphql_request(payload)
        result = (data.get("executeCommand") if isinstance(data, dict) else None) or {}
        return {
            "exit_code": result.get("exitCode"),
            "output": result.get("output", ""),
        }

    async def _execute_bash(self, command: str) -> dict[str, Any]:
        wrapped = ["bash", "-lc", command]
        result = await self.execute_command(wrapped)
        return result

    async def _graphql_request(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self._token:
            raise ZeaburAPIError("ZEABUR_API_TOKEN is not configured")
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(self._graphql_url, headers=headers, json=payload)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ZeaburAPIError(
                f"Zeabur API request failed: {exc.response.status_code} {exc.response.text}"
            ) from exc
        try:
            data = response.json()
        except json.JSONDecodeError as exc:
            raise ZeaburAPIError("Zeabur API returned non-JSON response") from exc
        if errors := data.get("errors"):
            raise ZeaburAPIError(str(errors))
        return data.get("data") or {}
