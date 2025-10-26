from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, Iterable, List, Sequence, Tuple

from ..core.types import Event


class TokenOverviewService:
    """彙整智慧錢包事件與市場熱度指標。"""

    def build_overview(
        self,
        smart_money_events: Sequence[Event],
        screener_rows: Sequence[dict],
    ) -> List[dict]:
        market_map = self._index_screener_rows(screener_rows)
        smart_map = self._summarize_events(smart_money_events)

        keys = set(market_map.keys()) | set(smart_map.keys())
        overview: List[dict] = []
        for key in keys:
            chain, address, symbol = key
            market = market_map.get(key)
            smart = smart_map.get(key)
            overview.append(
                {
                    "chain": chain,
                    "token_address": address or None,
                    "token_symbol": symbol,
                    "market": market or {},
                    "smart_money": smart or {},
                }
            )

        overview.sort(
            key=lambda item: (
                -(item["market"].get("volume") or 0.0),
                -(item["smart_money"].get("total_usd_notional") or 0.0),
            )
        )
        return overview

    def _index_screener_rows(self, rows: Sequence[dict]) -> Dict[Tuple[str, str, str], dict]:
        indexed: Dict[Tuple[str, str, str], dict] = {}
        for row in rows:
            chain = row.get("chain") or ""
            address = self._normalize_address(row.get("token_address"))
            symbol = row.get("token_symbol") or ""
            key = (chain, address, symbol)
            indexed[key] = {
                "volume": row.get("volume"),
                "buy_volume": row.get("buy_volume"),
                "sell_volume": row.get("sell_volume"),
                "netflow": row.get("netflow"),
                "price_change": row.get("price_change"),
                "price_usd": row.get("price_usd"),
                "liquidity": row.get("liquidity"),
                "market_cap_usd": row.get("market_cap_usd"),
                "token_age_days": row.get("token_age_days"),
                "fdv": row.get("fdv"),
                "fdv_mc_ratio": row.get("fdv_mc_ratio"),
                "inflow_fdv_ratio": row.get("inflow_fdv_ratio"),
                "outflow_fdv_ratio": row.get("outflow_fdv_ratio"),
            }
        return indexed

    def _summarize_events(self, events: Sequence[Event]) -> Dict[Tuple[str, str, str], dict]:
        grouped: Dict[Tuple[str, str, str], Dict[str, Any]] = defaultdict(
            lambda: {
                "event_count": 0,
                "wallets": set(),
                "total_usd_notional": 0.0,
                "netflow_values": [],
                "tx_hashes": [],
            }
        )

        for event in events:
            chain = event.token.chain or event.chain or ""
            address = self._normalize_address(event.token.address)
            symbol = event.token.symbol
            key = (chain, address, symbol)
            bucket = grouped[key]
            bucket["event_count"] += 1
            if event.wallet and event.wallet.address:
                bucket["wallets"].add(event.wallet.address.lower())
            usd_notional = event.features.usd_notional or 0.0
            bucket["total_usd_notional"] += float(usd_notional)
            if event.features.smart_money_netflow is not None:
                bucket["netflow_values"].append(float(event.features.smart_money_netflow))
            if event.tx_hash:
                bucket["tx_hashes"].append(event.tx_hash)

        summaries: Dict[Tuple[str, str, str], dict] = {}
        for key, bucket in grouped.items():
            netflows = bucket["netflow_values"]
            netflow_sum = sum(netflows) if netflows else 0.0
            positive_count = sum(1 for value in netflows if value > 0)
            negative_count = sum(1 for value in netflows if value < 0)
            avg_notional = (
                bucket["total_usd_notional"] / bucket["event_count"] if bucket["event_count"] else 0.0
            )
            summaries[key] = {
                "event_count": bucket["event_count"],
                "wallet_count": len(bucket["wallets"]),
                "total_usd_notional": bucket["total_usd_notional"],
                "average_usd_notional": avg_notional,
                "netflow_sum": netflow_sum,
                "netflow_positive": positive_count,
                "netflow_negative": negative_count,
                "netflow_summary": f"sum={netflow_sum:.2f}, +={positive_count}, -={negative_count}",
                "sample_tx_hashes": bucket["tx_hashes"][:5],
            }
        return summaries

    @staticmethod
    def _normalize_address(address: Any) -> str:
        if not address:
            return ""
        return str(address).lower()
