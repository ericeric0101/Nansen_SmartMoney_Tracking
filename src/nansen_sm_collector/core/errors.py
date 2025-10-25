from __future__ import annotations


class CollectorError(Exception):
    """管線執行時的基底例外。"""


class ConfigurationError(CollectorError):
    """設定或環境變數錯誤。"""


class PhaseGateError(CollectorError):
    """階段 STOP_GATE 驗證錯誤。"""


class AdapterError(CollectorError):
    """外部資料提供者錯誤。"""


class NormalizationError(CollectorError):
    """資料正規化失敗。"""


class EnrichmentError(CollectorError):
    """資料增豐失敗。"""


class ScoringError(CollectorError):
    """評分計算錯誤。"""
