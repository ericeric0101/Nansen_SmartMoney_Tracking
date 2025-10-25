from __future__ import annotations


class CryptoPanicClient:
    """Phase-3 外部新聞資料來源（預設停用）。"""

    def fetch_latest(self, symbol: str) -> dict:
        raise NotImplementedError("CryptoPanic 介面尚未實作")
