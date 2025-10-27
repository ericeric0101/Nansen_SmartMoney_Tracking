from __future__ import annotations

import asyncio
from asyncio.subprocess import Process
from typing import Any, Dict


class LocalPipelineRunner:
    """Simple async runner used when Zeabur API is unavailable."""

    def __init__(self, command: str) -> None:
        self._command = command
        self._process: Process | None = None

    async def run_once(self) -> Dict[str, Any]:
        if self.is_running:
            return {"status": "running", "detail": "pipeline is already running"}
        self._process = await asyncio.create_subprocess_shell(
            self._command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await self._process.communicate()
        stdout_text = stdout.decode("utf-8", errors="ignore") if stdout else ""
        stderr_text = stderr.decode("utf-8", errors="ignore") if stderr else ""
        return {
            "status": "completed",
            "returncode": self._process.returncode,
            "stdout": stdout_text[-2000:],
            "stderr": stderr_text[-2000:],
        }

    async def terminate(self) -> Dict[str, Any]:
        if not self.is_running:
            return {"status": "idle"}
        assert self._process is not None  # for type checker
        self._process.terminate()
        await self._process.wait()
        return {
            "status": "terminated",
            "returncode": self._process.returncode,
        }

    async def status(self) -> Dict[str, Any]:
        if self.is_running:
            return {"status": "running"}
        if self._process is None:
            return {"status": "idle"}
        return {
            "status": "idle",
            "returncode": self._process.returncode,
        }

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.returncode is None
