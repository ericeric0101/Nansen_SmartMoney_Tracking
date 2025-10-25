from __future__ import annotations

import typer

from ..collectors.pipeline import CollectorPipeline, PipelineResult
from ..config.settings import get_settings
from ..core.errors import PhaseGateError
from .gates import ensure_phase_allowed, exit_with_gate_error, mark_phase_complete

app = typer.Typer(help="Nansen Smart Money Collector CLI")


@app.command("run-once")
def command_run_once(
    use_mock: bool = typer.Option(True, help="是否使用模擬 Nansen 回應資料"),
) -> None:
    """執行 Phase-1 蒐集流程。"""

    settings = get_settings()

    try:
        ensure_phase_allowed(command_phase=1, settings=settings)
    except PhaseGateError as error:
        exit_with_gate_error(error)

    pipeline = CollectorPipeline(settings=settings)
    result = pipeline.run_once(use_mock=use_mock)
    _print_result(result)
    mark_phase_complete(phase=1)


@app.command("run-live")
def command_run_live(
    dry_run: bool = typer.Option(True, help="是否僅模擬交易流程（Phase-2）"),
) -> None:
    """執行 Phase-2 交易流程（需先通過 Phase-1）。"""

    settings = get_settings()

    try:
        ensure_phase_allowed(command_phase=2, settings=settings)
    except PhaseGateError as error:
        exit_with_gate_error(error)

    typer.echo("Phase-2 功能尚未實作，請完成 Phase-1 後再擴充。")
    if not dry_run:
        typer.echo("實際下單功能需另行實作並確認風險管理。")


@app.command("run-ablation")
def command_run_ablation() -> None:
    """執行 Phase-3 資料增強對照實驗。"""

    settings = get_settings()

    try:
        ensure_phase_allowed(command_phase=3, settings=settings)
    except PhaseGateError as error:
        exit_with_gate_error(error)

    typer.echo("Phase-3 功能尚未實作，請完成 Phase-2 後再擴充。")


def _print_result(result: PipelineResult) -> None:
    """輸出流程摘要。"""

    typer.echo("=== Pipeline Summary ===")
    typer.echo(f"Signals: {len(result.signals)}")
    if result.signals:
        buy_count = sum(1 for s in result.signals if (s.metadata or {}).get("signal_type", "buy") != "sell")
        sell_count = len(result.signals) - buy_count
        typer.echo(f"Buy signals: {buy_count}")
        typer.echo(f"Sell signals: {sell_count}")
    if result.report_path:
        typer.echo(f"Report: {result.report_path}")
    if result.stats:
        stats = result.stats
        typer.echo("--- Event Stats ---")
        for key in (
            "dex_events",
            "token_screener_events",
            "netflow_events",
            "total_events",
            "enriched_events",
            "merged_events",
        ):
            if key in stats:
                typer.echo(f"{key}: {stats[key]}")
        filter_stats = stats.get("filter_stats")
        if isinstance(filter_stats, dict):
            typer.echo("--- Filter Stats ---")
            for name, value in filter_stats.items():
                typer.echo(f"{name}: {value}")
        trade_stats = stats.get("trade_simulation")
        if isinstance(trade_stats, dict):
            typer.echo("--- Trade Simulation ---")
            for name, value in trade_stats.items():
                typer.echo(f"{name}: {value}")
