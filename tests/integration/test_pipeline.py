from pathlib import Path

from nansen_sm_collector.collectors.pipeline import CollectorPipeline
from nansen_sm_collector.config.settings import AppSettings


def test_pipeline_run_once_with_mock(tmp_path: Path) -> None:
    db_file = tmp_path / "collector.db"
    settings = AppSettings(NANSEN_API_KEY="dummy-key", DB_URL=f"sqlite:///{db_file}")
    pipeline = CollectorPipeline(settings=settings)
    result = pipeline.run_once(use_mock=True)
    assert result.signals  # 使用模擬資料應至少產出一筆訊號
