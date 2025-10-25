from __future__ import annotations

from typing import Dict, Iterable

import httpx

from ..core.errors import AdapterError


class GeckoTerminalClient:
    """簡易封裝 GeckoTerminal Token Price API。"""

    NETWORK_MAP = {
        "ethereum": "eth",
        "eth": "eth",
        "solana": "sol",
        "sol": "sol",
        "base": "base",
        "bsc": "bsc",
        "arbitrum": "arb",
        "optimism": "opt",
        "polygon": "polygon",
        "matic": "polygon",
    }

    def __init__(
        self,
        base_url: str,
        version: str,
        timeout: float = 10.0,
    ) -> None:
        self._client = httpx.Client(
            base_url=base_url,
            headers={"accept": f"application/json;version={version}"},
            timeout=timeout,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "GeckoTerminalClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def get_prices(self, chain: str, addresses: Iterable[str]) -> Dict[str, float]:
        network = self._resolve_network(chain)
        if not network:
            raise AdapterError(f"不支援的 GeckoTerminal 網路: {chain}")

        address_list = [addr.lower() for addr in addresses if addr]
        if not address_list:
            return {}

        joined = ",".join(address_list)
        url = f"/simple/networks/{network}/token_price/{joined}"
        response = self._client.get(
            url,
            params={
                "include_market_cap": "false",
                "mcap_fdv_fallback": "false",
                "include_24hr_vol": "false",
                "include_24hr_price_change": "false",
                "include_total_reserve_in_usd": "false",
            },
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            raise AdapterError(f"GeckoTerminal API 錯誤: {error}") from error

        payload = response.json()
        prices = payload.get("data", {}).get("attributes", {}).get("token_prices", {})
        return {addr.lower(): float(price) for addr, price in prices.items() if price is not None}

    @classmethod
    def _resolve_network(cls, chain: str | None) -> str | None:
        if not chain:
            return None
        normalized = chain.lower()
        if normalized in cls.NETWORK_MAP:
            return cls.NETWORK_MAP[normalized]
        return normalized
