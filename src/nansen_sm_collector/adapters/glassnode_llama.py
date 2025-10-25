from __future__ import annotations


class MacroDataClient:
    """Phase-3 宏觀資料來源（預設停用）。"""

    def fetch_indicators(self, symbol: str) -> dict:
        raise NotImplementedError("Glassnode/DeFiLlama 介面尚未實作")
