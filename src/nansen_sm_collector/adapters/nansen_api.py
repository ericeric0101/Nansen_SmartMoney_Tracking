from __future__ import annotations

from typing import Any, Dict, Iterable, Optional

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from ..core.errors import AdapterError


class NansenAPIClient:
    """Nansen API 介面層。"""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout: float = 30.0,
        transport: Optional[httpx.BaseTransport] = None,
    ) -> None:
        self._client = httpx.Client(
            base_url=base_url,
            headers={
                "x-api-key": api_key,
                "apiKey": api_key,
                "Authorization": f"Bearer {api_key}",
                "User-Agent": "nansen-sm-collector/0.1.0",
            },
            timeout=timeout,
            transport=transport,
        )

    def close(self) -> None:
        """關閉底層 HTTP 連線。"""

        self._client.close()

    def __enter__(self) -> "NansenAPIClient":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    @retry(
        retry=retry_if_exception_type(AdapterError),
        wait=wait_exponential(multiplier=1, max=8),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def _post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        response = self._client.post(path, json=payload)
        if response.status_code >= 500:
            raise AdapterError(f"Nansen 伺服器錯誤：{response.status_code}")
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            body = response.text
            raise AdapterError(
                f"Nansen 回應錯誤：{error} — body: {body}"
            ) from error
        return response.json()

    @retry(
        retry=retry_if_exception_type(AdapterError),
        wait=wait_exponential(multiplier=1, max=8),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        response = self._client.get(path, params=params)
        if response.status_code >= 500:
            raise AdapterError(f"Nansen 伺服器錯誤：{response.status_code}")
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            raise AdapterError(f"Nansen 回應錯誤：{error}") from error
        return response.json()

    def fetch_dex_trades(self, filters: Dict[str, Any]) -> Dict[str, Any]:
        """取得 DEX 交易資料。"""

        return self._post("/api/v1/smart-money/dex-trades", filters)

    def fetch_token_screener(self, filters: Dict[str, Any]) -> Dict[str, Any]:
        """取得 Token Screener 異常偵測資料。"""

        return self._post("/api/v1/token-screener", filters)

    def fetch_netflows(self, filters: Dict[str, Any]) -> Dict[str, Any]:
        """取得 Smart Money 資金流向資料。"""

        return self._post("/api/v1/smart-money/netflow", filters)

    def fetch_address_labels(self, chain: str, address: str) -> Dict[str, Any]:
        """查詢單一地址標籤。"""

        payload = {
            "parameters": {
                "chain": chain,
                "address": address,
            },
            "pagination": {"page": 1, "recordsPerPage": 100},
        }
        return self._post("/api/beta/profiler/address/labels", payload)
