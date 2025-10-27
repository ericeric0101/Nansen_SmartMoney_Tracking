from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import httpx
import json


logger = logging.getLogger(__name__)


class TelegramNotifier:
    """Encapsulates Telegram Bot API interactions."""

    def __init__(
        self,
        *,
        bot_token: str,
        chat_id: str,
        timeout: float = 15.0,
    ) -> None:
        self._bot_token = bot_token.strip()
        self._chat_id = chat_id.strip()
        self._timeout = timeout
        self._api_base = f"https://api.telegram.org/bot{self._bot_token}"

    def send_text(
        self,
        text: str,
        *,
        parse_mode: Optional[str] = None,
        reply_markup: Optional[dict] = None,
    ) -> bool:
        message = text.strip()
        if not message:
            return False
        # Telegram message limit is 4096 characters
        if len(message) > 4000:
            message = f"{message[:3900].rstrip()}\n\n...[truncated]"

        url = f"{self._api_base}/sendMessage"
        data: dict[str, str] = {"chat_id": self._chat_id, "text": message}
        if parse_mode:
            data["parse_mode"] = parse_mode
        if reply_markup:
            data["reply_markup"] = json.dumps(reply_markup)

        try:
            response = httpx.post(url, data=data, timeout=self._timeout)
            response.raise_for_status()
            return True
        except httpx.HTTPError as error:
            logger.warning(
                "telegram_send_message_failed",
                extra={"error": str(error)},
            )
            return False

    def send_document(self, file_path: Path, caption: Optional[str] = None) -> bool:
        if not file_path.exists():
            logger.warning("telegram_document_missing", extra={"path": str(file_path)})
            return False

        url = f"{self._api_base}/sendDocument"
        data: dict[str, str] = {"chat_id": self._chat_id}
        if caption:
            data["caption"] = caption

        try:
            with file_path.open("rb") as handle:
                files = {
                    "document": (
                        file_path.name,
                        handle,
                        "text/markdown" if file_path.suffix.lower() == ".md" else "application/octet-stream",
                    )
                }
                response = httpx.post(url, data=data, files=files, timeout=self._timeout)
            response.raise_for_status()
            return True
        except httpx.HTTPError as error:
            logger.warning(
                "telegram_send_document_failed",
                extra={"path": str(file_path), "error": str(error)},
            )
            return False
