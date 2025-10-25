from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List


class MockNansenClient:
    """提供測試時使用的固定資料回應。"""

    def fetch_dex_trades(self, filters: Dict[str, Any]) -> Dict[str, Any]:
        now = datetime.now(tz=timezone.utc).isoformat()
        chains = filters.get("chains", ["mock"])
        data = []
        for index, chain in enumerate(chains, start=1):
            token_symbol = f"MOCK{index}"
            tx_hash = f"0xmockhash{index}"
            data.append(
                {
                    "txHash": tx_hash,
                    "transaction_hash": tx_hash,
                    "address": f"0xwallet{index}",
                    "trader_address": f"0xwallet{index}",
                    "tokenSymbol": token_symbol,
                    "token_bought_symbol": token_symbol,
                    "chain": chain,
                    "usdNotional": 150000 * index,
                    "trade_value_usd": 150000 * index,
                    "isBuy": True,
                    "timestamp": now,
                    "block_timestamp": now,
                }
            )
        return {
            "data": data,
            "pagination": {
                "page": 1,
                "per_page": len(data),
                "is_last_page": True,
            },
        }

    def fetch_token_screener(self, filters: Dict[str, Any]) -> Dict[str, Any]:
        chains = filters.get("chains", ["mock"])
        data: List[Dict[str, Any]] = []
        for index, chain in enumerate(chains, start=1):
            data.append(
                {
                    "chain": chain,
                    "token_address": f"0xmocktoken{index}",
                    "token_symbol": f"MOCK{index}",
                    "token_age_days": 30 * index,
                    "market_cap_usd": 1_000_000 * index,
                    "liquidity": 100_000 * index,
                    "buy_volume": 50_000 * index,
                    "sell_volume": 25_000 * index,
                    "volume": 75_000 * index,
                    "netflow": 25_000 * index,
                }
            )
        return {"data": data, "pagination": {"page": 1, "per_page": len(data), "is_last_page": True}}

    def fetch_netflows(self, filters: Dict[str, Any]) -> Dict[str, Any]:
        chains = filters.get("chains", ["mock"])
        chain = chains[0] if chains else "mock"
        return {
            "data": [
                {
                    "token_address": "0xmocktoken1",
                    "token_symbol": "MOCK1",
                    "net_flow_24h_usd": 10_000,
                    "net_flow_7d_usd": 70_000,
                    "net_flow_30d_usd": 100_000,
                    "chain": chain,
                    "trader_count": 5,
                    "market_cap_usd": 5_000_000,
                }
            ],
            "pagination": {"page": 1, "per_page": 1, "is_last_page": True},
        }

    def fetch_address_labels(self, chain: str, address: str) -> List[Dict[str, Any]]:
        return [
            {
                "label": "Smart Money Mock",
                "category": "mock",
            }
        ]
