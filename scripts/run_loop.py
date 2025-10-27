from __future__ import annotations

import os
import time
from pathlib import Path

from nansen_sm_collector.collectors.pipeline import CollectorPipeline
from nansen_sm_collector.config.settings import get_settings


LOG_PATH = Path("logs/loop.log")
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)


def main() -> None:
    settings = get_settings()
    pipeline = CollectorPipeline(settings=settings)

    interval_seconds = _load_interval_seconds()
    while True:
        start = time.time()
        try:
            result = pipeline.run_once(use_mock=False)
            LOG_PATH.write_text(
                f"Last run: signals={len(result.signals)} stats={result.stats}\n",
                encoding="utf-8",
            )
        except Exception as exc:  # noqa: BLE001
            LOG_PATH.write_text(f"Last run failed: {exc}\n", encoding="utf-8")
        elapsed = time.time() - start
        sleep_seconds = max(interval_seconds - elapsed, 0)
        time.sleep(sleep_seconds)


def _load_interval_seconds() -> int:
    value = os.getenv("RUN_LOOP_INTERVAL_SECONDS")
    try:
        if value:
            seconds = int(value)
            if seconds > 0:
                return seconds
    except ValueError:
        pass
    return 3600


if __name__ == "__main__":
    main()
