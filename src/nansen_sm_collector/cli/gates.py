from __future__ import annotations

from pathlib import Path

import typer

from ..config.settings import AppSettings
from ..core.errors import PhaseGateError


def _ok_file_name(phase: int) -> str:
    return f"phase{phase}.ok"


def ensure_phase_allowed(command_phase: int, settings: AppSettings) -> None:
    """確認指令執行時的階段限制。"""

    if settings.phase < command_phase:
        raise PhaseGateError(f"目前設定的 PHASE={settings.phase}，無法執行 Phase-{command_phase} 指令。")

    if command_phase > 1:
        previous_file = Path(_ok_file_name(command_phase - 1))
        if not previous_file.exists():
            message = (
                "尚未通過前一階段驗證，請先完成 Phase"
                f"-{command_phase - 1} 並產生 {previous_file.name}。"
            )
            raise PhaseGateError(message)


def mark_phase_complete(phase: int) -> None:
    """在成功完成階段流程後建立對應的 OK 檔案。"""

    ok_path = Path(_ok_file_name(phase))
    ok_path.write_text("ok\n", encoding="utf-8")


def exit_with_gate_error(error: PhaseGateError) -> None:
    """輸出錯誤訊息並以代碼 2 結束程式。"""

    typer.echo(f"[STOP_GATE] {error}", err=True)
    raise typer.Exit(code=2)
