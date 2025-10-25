from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List

from ..core.errors import NormalizationError
from ..core.types import Event, EventFeature, Token, Wallet


def _parse_timestamp(value: str) -> datetime:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError as error:
        raise NormalizationError(f"時間格式無法解析：{value}") from error


class EventNormalizer:
    """將 Nansen 原始資料轉換為事件。"""

    def dex_trades(self, payload: Dict[str, Any]) -> List[Event]:
        events: List[Event] = []
        for item in payload.get("data", []):
            timestamp = item.get("timestamp") or item.get("block_timestamp")
            occurred_at = _parse_timestamp(timestamp) if timestamp else datetime.now(tz=timezone.utc)
            token = Token(
                symbol=
                item.get("tokenSymbol")
                or item.get("token_bought_symbol")
                or item.get("token_name")
                or item.get("token_symbol", ""),
                address=item.get("token_bought_address")
                or item.get("token_address"),
                chain=item.get("chain"),
                liquidity_score=item.get("liquidityScore", 1.0),
            )
            wallet = Wallet(address=item.get("address") or item.get("trader_address", ""))
            usd_notional = (
                item.get("usdNotional")
                or item.get("trade_value_usd")
                or item.get("token_bought_in_usd")
                or item.get("estimated_value_usd")
            )
            features = EventFeature(
                usd_notional=usd_notional,
                is_buy=None,
                metadata={
                    "tx_hash": item.get("txHash") or item.get("transaction_hash"),
                    "token_sold_symbol": item.get("token_sold_symbol") or item.get("traded_token_name"),
                    "traded_token_amount": item.get("traded_token_amount"),
                },
            )
            events.append(
                Event(
                    source="dex_trades",
                    token=token,
                    wallet=wallet,
                    tx_hash=item.get("txHash") or item.get("transaction_hash"),
                    chain=item.get("chain"),
                    occurred_at=occurred_at,
                    features=features,
                )
            )
        return events

    def token_screener(self, payload: Dict[str, Any]) -> List[Event]:
        events: List[Event] = []
        now = datetime.now(tz=timezone.utc)
        for item in payload.get("data", []):
            token = Token(
                symbol=item.get("tokenSymbol") or item.get("token_symbol", ""),
                address=item.get("token_address"),
                chain=item.get("chain"),
                liquidity_score=item.get("liquidity"),
            )
            features = EventFeature(
                volume_jump=item.get("volumeJump"),
                metadata={
                    "buy_volume": item.get("buy_volume"),
                    "sell_volume": item.get("sell_volume"),
                    "netflow": item.get("netflow"),
                    "market_cap_usd": item.get("market_cap_usd"),
                },
            )
            events.append(
                Event(
                    source="token_screener",
                    token=token,
                    occurred_at=now,
                    features=features,
                )
            )
        return events

    def netflows(self, payload: Dict[str, Any]) -> List[Event]:
        events: List[Event] = []
        now = datetime.now(tz=timezone.utc)
        for item in payload.get("data", []):
            wallet = None
            if "address" in item:
                wallet = Wallet(address=item.get("address", ""), labels=[item.get("cohort", "")])
            features = EventFeature(
                smart_money_netflow=
                item.get("netflowUsd")
                or item.get("net_flow_24h_usd")
                or item.get("net_flow_7d_usd"),
                metadata={
                    "net_flow_30d_usd": item.get("net_flow_30d_usd"),
                    "trader_count": item.get("trader_count"),
                    "market_cap_usd": item.get("market_cap_usd"),
                },
            )
            events.append(
                Event(
                    source="netflows",
                    token=Token(
                        symbol=item.get("tokenSymbol") or item.get("token_symbol", "UNKNOWN"),
                        address=item.get("token_address"),
                        chain=item.get("chain"),
                    ),
                    wallet=wallet,
                    occurred_at=now,
                    features=features,
                )
            )
        return events
