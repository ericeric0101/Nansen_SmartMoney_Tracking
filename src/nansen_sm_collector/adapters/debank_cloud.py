from __future__ import annotations


class DeBankClient:
    """Phase-3 錢包行為資料來源（預設停用）。"""

    def fetch_wallet_profile(self, address: str) -> dict:
        raise NotImplementedError("DeBank 介面尚未實作")
