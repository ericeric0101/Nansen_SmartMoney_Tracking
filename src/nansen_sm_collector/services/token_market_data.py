from __future__ import annotations

import logging
from typing import Dict, Iterable, List, Mapping, MutableMapping, Sequence

from ..adapters.gecko_terminal import GeckoTerminalClient
from ..core.errors import AdapterError

logger = logging.getLogger(__name__)


class TokenMarketDataService:
    """使用 GeckoTerminal API 補充池子 OHLCV 與交易深度資訊。"""

    def __init__(
        self,
        gecko_client: GeckoTerminalClient | None,
        *,
        timeframe: str,
        limit: int,
        min_trade_usd: float,
        pool_map: Mapping[str, Mapping[str, Sequence[str]]],
    ) -> None:
        self._client = gecko_client
        self._timeframe = timeframe
        self._limit = max(1, min(limit, 1000))
        self._min_trade_usd = max(0.0, float(min_trade_usd))
        self._pool_map = pool_map

    def enrich(self, overview: Sequence[dict]) -> List[dict]:
        if not self._client or not overview:
            return list(overview)

        enriched: List[dict] = []
        for entry in overview:
            enriched.append(self._enrich_single(entry))
        return enriched

    def _enrich_single(self, entry: dict) -> dict:
        chain = (entry.get("chain") or "").lower()
        token_address = (entry.get("token_address") or "").lower()
        market: MutableMapping[str, object] = dict(entry.get("market") or {})

        pools = list(self._resolve_pools(chain, token_address))
        pool_payloads = []
        for pool in pools:
            pool_info = {"pool_address": pool}
            ohlcv = self._fetch_pool_ohlcv(chain, pool)
            if ohlcv:
                pool_info["ohlcv"] = ohlcv
            trades = self._fetch_pool_trades(chain, pool)
            if trades:
                pool_info["trade_stats"] = self._summarize_trades(trades)
                pool_info["trades"] = trades[: min(20, len(trades))]
            if len(pool_info) > 1:
                pool_payloads.append(pool_info)

        if pool_payloads:
            market["pools"] = pool_payloads

        entry["market"] = market
        return entry

    def _resolve_pools(self, chain: str, token_address: str) -> Iterable[str]:
        chain_map = self._pool_map.get(chain)
        if not chain_map:
            return []
        pools = chain_map.get(token_address, [])
        return [pool.lower() for pool in pools if pool]

    def _fetch_pool_ohlcv(self, chain: str, pool_address: str) -> List[dict]:
        try:
            return self._client.get_pool_ohlcv(
                chain,
                pool_address,
                timeframe=self._timeframe,
                limit=self._limit,
            )
        except AdapterError as error:
            logger.warning("gecko_ohlcv_failed", extra={"chain": chain, "pool": pool_address, "error": str(error)})
        return []

    def _fetch_pool_trades(self, chain: str, pool_address: str) -> List[dict]:
        try:
            return self._client.get_pool_trades(
                chain,
                pool_address,
                min_volume_usd=self._min_trade_usd,
            )
        except AdapterError as error:
            logger.warning("gecko_trades_failed", extra={"chain": chain, "pool": pool_address, "error": str(error)})
        return []

    def _summarize_trades(self, trades: Sequence[dict]) -> Dict[str, float | int | None]:
        count = 0
        buy_volume = 0.0
        sell_volume = 0.0
        total_volume = 0.0
        max_volume = 0.0
        last_timestamp: str | None = None

        for trade in trades:
            attributes = trade.get("attributes") if isinstance(trade.get("attributes"), dict) else {}
            volume = (
                attributes.get("amount_in_usd")
                or attributes.get("volume_in_usd")
                or attributes.get("trade_volume_usd")
                or trade.get("volume_in_usd")
                or trade.get("trade_volume_usd")
            )
            if volume is None:
                continue
            try:
                volume = float(volume)
            except (TypeError, ValueError):
                continue
            total_volume += volume
            max_volume = max(max_volume, volume)
            count += 1

            side = (
                attributes.get("trade_type")
                or attributes.get("side")
                or trade.get("trade_type")
                or trade.get("side")
                or ""
            )
            normalized_side = str(side).lower()
            if normalized_side in ("buy", "swap_buy"):
                buy_volume += volume
            elif normalized_side in ("sell", "swap_sell"):
                sell_volume += volume

            timestamp = (
                attributes.get("block_timestamp")
                or attributes.get("timestamp")
                or trade.get("block_timestamp")
                or trade.get("timestamp")
            )
            if timestamp:
                last_timestamp = str(timestamp)

        return {
            "trade_count": count,
            "total_volume_usd": total_volume,
            "buy_volume_usd": buy_volume,
            "sell_volume_usd": sell_volume,
            "max_trade_volume_usd": max_volume,
            "last_trade_timestamp": last_timestamp,
        }
