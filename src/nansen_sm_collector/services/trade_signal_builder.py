from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence


@dataclass
class TradeCandidate:
    token_symbol: str
    token_address: str | None
    chain: str
    composite_score: float
    market_score: float | None
    liquidity_score: float | None
    smart_money_score: float | None
    has_smart_money: bool
    raw: dict


class TradeSignalBuilder:
    """根據 token overview 評估可交易標的。"""

    def __init__(
        self,
        *,
        top_n: int = 10,
        volume_range: tuple[float, float] = (100_000.0, 2_000_000.0),
        liquidity_range: tuple[float, float] = (200_000.0, 5_000_000.0),
        netflow_range: tuple[float, float] = (0.0, 500_000.0),
        price_change_range: tuple[float, float] = (0.0, 1.0),
        smart_notional_range: tuple[float, float] = (50_000.0, 500_000.0),
        smart_netflow_range: tuple[float, float] = (0.0, 100_000.0),
        smart_event_range: tuple[float, float] = (1.0, 5.0),
    ) -> None:
        self._top_n = top_n
        self._volume_range = volume_range
        self._liquidity_range = liquidity_range
        self._netflow_range = netflow_range
        self._price_change_range = price_change_range
        self._smart_notional_range = smart_notional_range
        self._smart_netflow_range = smart_netflow_range
        self._smart_event_range = smart_event_range

    def build(self, overview: Sequence[dict]) -> dict:
        candidates: List[TradeCandidate] = []
        for entry in overview:
            candidate = self._score_entry(entry)
            if candidate:
                candidates.append(candidate)

        sorted_candidates = sorted(
            candidates,
            key=lambda item: (
                item.composite_score,
                item.market_score or 0.0,
            ),
            reverse=True,
        )

        with_smart = [c for c in sorted_candidates if c.has_smart_money]
        without_smart = [c for c in sorted_candidates if not c.has_smart_money]

        return {
            "all": [self._to_payload(item) for item in sorted_candidates],
            "with_smart_money": [
                self._to_payload(item) for item in with_smart[: self._top_n]
            ],
            "without_smart_money": [
                self._to_payload(item) for item in without_smart[: self._top_n]
            ],
        }

    def _score_entry(self, entry: dict) -> Optional[TradeCandidate]:
        token_symbol = entry.get("token_symbol") or ""
        token_address = entry.get("token_address")
        chain = entry.get("chain") or ""
        market = entry.get("market") or {}
        smart = entry.get("smart_money") or {}

        market_score = self._score_market(market)
        liquidity_score = self._score_liquidity(market)
        smart_money_score = self._score_smart_money(smart)

        composite = self._combine_scores(
            {
                "market": (market_score, 0.5),
                "liquidity": (liquidity_score, 0.2),
                "smart": (smart_money_score, 0.3),
            }
        )

        if composite <= 0:
            return None

        return TradeCandidate(
            token_symbol=token_symbol,
            token_address=token_address,
            chain=chain,
            composite_score=round(composite, 4),
            market_score=self._round_or_none(market_score),
            liquidity_score=self._round_or_none(liquidity_score),
            smart_money_score=self._round_or_none(smart_money_score),
            has_smart_money=bool(smart.get("event_count")),
            raw=entry,
        )

    def _score_market(self, market: dict) -> Optional[float]:
        scores: List[float] = []
        volume = market.get("volume")
        netflow = market.get("netflow")
        price_change = market.get("price_change")

        vol_score = self._linear_score(volume, *self._volume_range)
        if vol_score is not None:
            scores.append(vol_score)

        netflow_score = self._linear_score(netflow, *self._netflow_range)
        if netflow_score is not None:
            scores.append(netflow_score)

        price_score = self._linear_score(price_change, *self._price_change_range)
        if price_score is not None:
            scores.append(price_score)

        if not scores:
            return None
        return sum(scores) / len(scores)

    def _score_liquidity(self, market: dict) -> Optional[float]:
        liquidity = market.get("liquidity")
        if liquidity is None:
            return None
        return self._linear_score(liquidity, *self._liquidity_range)

    def _score_smart_money(self, smart: dict) -> Optional[float]:
        if not smart:
            return None

        scores: List[float] = []

        notional = smart.get("total_usd_notional")
        notional_score = self._linear_score(
            notional,
            *self._smart_notional_range,
        )
        if notional_score is not None:
            scores.append(notional_score)

        netflow_sum = smart.get("netflow_sum")
        netflow_score = self._linear_score(
            netflow_sum,
            *self._smart_netflow_range,
        )
        if netflow_score is not None:
            scores.append(netflow_score)

        event_count = smart.get("event_count")
        event_score = self._linear_score(
            event_count,
            *self._smart_event_range,
        )
        if event_score is not None:
            scores.append(event_score)

        if not scores:
            return None
        return sum(scores) / len(scores)

    @staticmethod
    def _linear_score(
        value: Optional[float],
        min_value: float,
        max_value: float,
    ) -> Optional[float]:
        if value is None:
            return None
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return None
        if max_value <= min_value:
            return None
        if numeric <= min_value:
            return 0.0
        if numeric >= max_value:
            return 1.0
        ratio = (numeric - min_value) / (max_value - min_value)
        return max(0.0, min(1.0, ratio))

    @staticmethod
    def _combine_scores(weighted_scores: Dict[str, tuple[Optional[float], float]]) -> float:
        total_weight = 0.0
        accum = 0.0
        for score, weight in weighted_scores.values():
            if score is None or weight <= 0:
                continue
            accum += score * weight
            total_weight += weight
        if total_weight <= 0:
            return 0.0
        return accum / total_weight

    @staticmethod
    def _round_or_none(value: Optional[float]) -> Optional[float]:
        if value is None:
            return None
        return round(value, 4)

    @staticmethod
    def _to_payload(candidate: TradeCandidate) -> dict:
        payload = {
            "token_symbol": candidate.token_symbol,
            "token_address": candidate.token_address,
            "chain": candidate.chain,
            "composite_score": candidate.composite_score,
            "market_score": candidate.market_score,
            "liquidity_score": candidate.liquidity_score,
            "smart_money_score": candidate.smart_money_score,
            "has_smart_money": candidate.has_smart_money,
        }
        payload["market"] = candidate.raw.get("market")
        payload["smart_money"] = candidate.raw.get("smart_money")
        return payload
