from __future__ import annotations

import logging

import structlog


def configure_logging(level: int = logging.INFO) -> None:
    """設定結構化日誌格式。"""

    logging.basicConfig(
        level=level,
        format="%(message)s",
    )
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(level),
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.dev.ConsoleRenderer(),
        ],
    )
