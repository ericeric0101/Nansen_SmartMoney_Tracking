from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, Iterable

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from ..config.settings import AppSettings, get_settings
from ..services.local_pipeline_runner import LocalPipelineRunner
from ..services.zeabur_client import ZeaburAPIClient, ZeaburAPIError


logger = logging.getLogger(__name__)


DASHBOARD_CALLBACKS = {
    "run_once",
    "schedule_menu",
    "schedule_stop",
    "status",
    "schedule_duration_1",
    "schedule_duration_3",
    "schedule_duration_6",
    "schedule_duration_12",
    "schedule_duration_24",
}


def run_bot() -> None:
    settings = get_settings()
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured")

    application = (
        ApplicationBuilder()
        .token(settings.telegram_bot_token)
        .post_init(_post_init)
        .build()
    )

    zeabur_client: ZeaburAPIClient | None = None
    if settings.zeabur_api_token and settings.zeabur_project_id:
        zeabur_client = ZeaburAPIClient(
            base_url=str(settings.zeabur_api_base),
            api_token=settings.zeabur_api_token,
            project_id=settings.zeabur_project_id,
            service_id=settings.zeabur_service_id,
            hourly_job_id=settings.zeabur_hourly_job_id,
            pipeline_command=settings.zeabur_pipeline_command,
            run_job_endpoint=settings.zeabur_run_job_endpoint,
            enable_job_endpoint=settings.zeabur_enable_job_endpoint,
            disable_job_endpoint=settings.zeabur_disable_job_endpoint,
            job_status_endpoint=settings.zeabur_job_status_endpoint,
        )

    local_runner: LocalPipelineRunner | None = None
    if not zeabur_client:
        local_runner = LocalPipelineRunner(settings.zeabur_pipeline_command)

    authorized_ids = _build_authorized_chat_ids(settings)
    application.bot_data["settings"] = settings
    application.bot_data["zeabur_client"] = zeabur_client
    application.bot_data["local_runner"] = local_runner
    application.bot_data["local_schedule_task"] = None
    application.bot_data["local_schedule_stop_event"] = None
    application.bot_data["local_schedule_interval_hours"] = None
    application.bot_data["authorized_chat_ids"] = authorized_ids

    application.add_handler(CommandHandler(["start", "dashboard"], _dashboard_command))
    application.add_handler(CommandHandler("help", _help_command))
    application.add_handler(CallbackQueryHandler(_handle_callback))

    logger.info("Starting Telegram dashboard bot")
    application.run_polling()


async def _dashboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update, context):
        await _deny_access(update)
        return
    keyboard = _build_primary_keyboard()
    await update.effective_message.reply_text("選擇操作：", reply_markup=keyboard)


async def _help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update, context):
        await _deny_access(update)
        return
    lines = [
        "可用指令:",
        "/dashboard - 顯示控制面板",
        "/help - 顯示說明",
        "",
        "使用控制面板按鈕即可觸發 Zeabur 管線與排程控制。",
    ]
    await update.effective_message.reply_text("\n".join(lines))


async def _handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()
    if not _is_authorized(update, context):
        await _deny_access(update)
        return

    data = query.data or ""
    if data not in DASHBOARD_CALLBACKS:
        await query.edit_message_text("未知的操作")
        return

    zeabur_client: ZeaburAPIClient | None = context.bot_data.get("zeabur_client")
    local_runner: LocalPipelineRunner | None = context.bot_data.get("local_runner")
    message = ""

    if zeabur_client:
        try:
            if data == "run_once":
                response = await zeabur_client.trigger_pipeline_once()
                message = _format_simple_response("排程已提交", response)
            elif data == "schedule_menu":
                keyboard = _build_schedule_keyboard()
                await query.edit_message_text("選擇排程時長：", reply_markup=keyboard)
                return
            elif data.startswith("schedule_duration_"):
                hours = int(data.rsplit("_", 1)[-1])
                response = await zeabur_client.enable_hourly_scheduler(hours)
                message = _format_simple_response(f"已啟用 {hours} 小時排程", response)
            elif data == "schedule_stop":
                response = await zeabur_client.disable_hourly_scheduler()
                message = _format_simple_response("已停用排程", response)
            elif data == "status":
                response = await zeabur_client.fetch_scheduler_status()
                message = _format_status_response(response)
            else:
                message = "未支援的操作"
        except ZeaburAPIError as exc:
            logger.warning("zeabur_api_error", extra={"error": str(exc)})
            message = f"Zeabur API 發生錯誤：{exc}"
    elif local_runner:
        try:
            if data == "run_once":
                response = await local_runner.run_once()
                message = _format_simple_response("本地執行已完成", response)
            elif data == "schedule_menu":
                response = await _start_local_schedule(context, interval_hours=1)
                message = _format_simple_response("已啟用每小時排程", response)
            elif data.startswith("schedule_duration_"):
                hours = int(data.rsplit("_", 1)[-1])
                response = await _start_local_schedule(context, interval_hours=hours)
                message = _format_simple_response(f"已啟用 {hours} 小時排程", response)
            elif data == "schedule_stop":
                schedule_response = await _stop_local_schedule(context)
                runner_response = await local_runner.terminate()
                message = _format_simple_response(
                    "已停用排程",
                    {
                        "schedule": schedule_response,
                        "runner": runner_response,
                    },
                )
            elif data == "status":
                schedule_status = await _local_schedule_status(context)
                runner_status = await local_runner.status()
                message = _format_simple_response(
                    "本地執行狀態",
                    {
                        "schedule": schedule_status,
                        "runner": runner_status,
                    },
                )
            else:
                message = "未支援的操作"
        except Exception as exc:  # noqa: BLE001
            logger.warning("local_runner_error", extra={"error": str(exc)})
            message = f"本地模式發生錯誤：{exc}"
    else:
        message = "尚未設定 Zeabur 或本地執行環境。"

    keyboard = _build_primary_keyboard()
    await query.edit_message_text(message, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)


async def _post_init(application: Application) -> None:
    commands = [
        BotCommand("dashboard", "顯示控制面板"),
        BotCommand("help", "顯示說明"),
    ]
    try:
        await application.bot.set_my_commands(commands)
    except Exception as exc:  # noqa: BLE001
        logger.warning("set_bot_commands_failed", extra={"error": str(exc)})


def _build_primary_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton("▶️ 立即執行", callback_data="run_once")],
        [InlineKeyboardButton("⏱️ 啟用排程", callback_data="schedule_menu")],
        [InlineKeyboardButton("⛔ 停止排程", callback_data="schedule_stop")],
        [InlineKeyboardButton("📊 查看狀態", callback_data="status")],
    ]
    return InlineKeyboardMarkup(buttons)


def _build_schedule_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton("1 小時", callback_data="schedule_duration_1")],
        [InlineKeyboardButton("3 小時", callback_data="schedule_duration_3")],
        [InlineKeyboardButton("6 小時", callback_data="schedule_duration_6")],
        [InlineKeyboardButton("12 小時", callback_data="schedule_duration_12")],
        [InlineKeyboardButton("24 小時", callback_data="schedule_duration_24")],
        [InlineKeyboardButton("返回", callback_data="status")],
    ]
    return InlineKeyboardMarkup(buttons)


async def _start_local_schedule(context: ContextTypes.DEFAULT_TYPE, interval_hours: int) -> Dict[str, Any]:
    interval_hours = max(1, interval_hours)
    runner: LocalPipelineRunner | None = context.bot_data.get("local_runner")
    if not runner:
        return {"status": "unavailable"}
    task = context.bot_data.get("local_schedule_task")
    if task and not task.done():
        return {
            "status": "running",
            "interval_hours": context.bot_data.get("local_schedule_interval_hours", interval_hours),
        }
    stop_event = asyncio.Event()
    context.bot_data["local_schedule_stop_event"] = stop_event
    context.bot_data["local_schedule_interval_hours"] = interval_hours
    loop_task = asyncio.create_task(_local_schedule_loop(runner, stop_event, interval_hours))
    context.bot_data["local_schedule_task"] = loop_task
    return {
        "status": "scheduled",
        "interval_hours": interval_hours,
    }


async def _stop_local_schedule(context: ContextTypes.DEFAULT_TYPE) -> Dict[str, Any]:
    task: asyncio.Task | None = context.bot_data.get("local_schedule_task")
    stop_event: asyncio.Event | None = context.bot_data.get("local_schedule_stop_event")
    if not task:
        return {"status": "idle"}
    if stop_event and not stop_event.is_set():
        stop_event.set()
    try:
        await asyncio.wait_for(task, timeout=5.0)
    except asyncio.TimeoutError:
        task.cancel()
    except Exception as exc:  # noqa: BLE001
        logger.warning("local_schedule_stop_error", extra={"error": str(exc)})
    finally:
        context.bot_data["local_schedule_task"] = None
        context.bot_data["local_schedule_stop_event"] = None
    return {"status": "stopped"}


async def _local_schedule_status(context: ContextTypes.DEFAULT_TYPE) -> Dict[str, Any]:
    task: asyncio.Task | None = context.bot_data.get("local_schedule_task")
    interval_hours = context.bot_data.get("local_schedule_interval_hours")
    if task and not task.done():
        return {
            "status": "running",
            "interval_hours": interval_hours,
        }
    return {
        "status": "idle",
        "interval_hours": interval_hours,
    }


async def _local_schedule_loop(
    runner: LocalPipelineRunner,
    stop_event: asyncio.Event,
    interval_hours: int,
) -> None:
    interval_seconds = interval_hours * 3600
    while not stop_event.is_set():
        try:
            await runner.run_once()
        except Exception as exc:  # noqa: BLE001
            logger.warning("local_schedule_run_failed", extra={"error": str(exc)})
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
        except asyncio.TimeoutError:
            continue
        except Exception as exc:  # noqa: BLE001
            logger.warning("local_schedule_wait_failed", extra={"error": str(exc)})
            break


def _format_simple_response(title: str, payload: Dict[str, Any]) -> str:
    if not payload:
        return title
    pretty = json.dumps(payload, ensure_ascii=False, indent=2)
    return f"*{title}*\n```\n{pretty}\n```"


def _format_status_response(payload: Dict[str, Any]) -> str:
    if not payload:
        return "尚無排程資訊"
    status_lines = ["*排程狀態*"]
    schedule: Dict[str, Any] = payload.get("schedule") or {}
    enabled = schedule.get("enabled")
    status_lines.append(f"狀態：{'啟用' if enabled else '停用'}")
    if "expression" in schedule:
        status_lines.append(f"Cron：`{schedule['expression']}`")
    if expires_at := schedule.get("expiresAt"):
        status_lines.append(f"到期時間：{expires_at}")
    if last_run := payload.get("lastRunAt"):
        status_lines.append(f"上次執行：{last_run}")
    if next_run := payload.get("nextRunAt"):
        status_lines.append(f"下次執行：{next_run}")
    return "\n".join(status_lines)


def _build_authorized_chat_ids(settings: AppSettings) -> set[str]:
    result: set[str] = set(settings.dashboard_chat_ids)
    if settings.telegram_chat_id:
        result.add(str(settings.telegram_chat_id))
    return result


def _is_authorized(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    chat_id = update.effective_chat.id if update.effective_chat else None
    if chat_id is None:
        return False
    allowed_ids: Iterable[str] = context.bot_data.get("authorized_chat_ids", set())
    return not allowed_ids or str(chat_id) in allowed_ids


async def _deny_access(update: Update) -> None:
    if update.effective_message:
        await update.effective_message.reply_text("此 bot 僅限授權成員使用。")


def main() -> None:
    run_bot()


if __name__ == "__main__":
    main()
