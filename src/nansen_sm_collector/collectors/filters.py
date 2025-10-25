from __future__ import annotations

from __future__ import annotations

from datetime import timedelta
from typing import Dict, Iterable, List, Tuple

from ..config.settings import AppSettings
from ..core.types import Event
from ..data.repos import EventRepository
from ..core.utils import utc_now


class EventFilterSet:
    """負責 Phase-1 必須條件過濾。"""

    def __init__(self, settings: AppSettings, event_repo: EventRepository) -> None:
        self._settings = settings
        self._event_repo = event_repo

    def apply(self, events: Iterable[Event]) -> Tuple[List[Event], dict[str, int]]:
        """套用所有條件並回傳符合的事件與統計。"""

        stats = {
            "evaluated": 0,
            "passed": 0,
            "fail_usd_notional": 0,
            "fail_liquidity": 0,
            "fail_blacklist": 0,
        }
        passed: List[Event] = []
        threshold_cache: Dict[tuple[str | None, str | None], float] = {}
        for event in events:
            stats["evaluated"] += 1
            if not self._usd_notional_ok(event, threshold_cache):
                stats["fail_usd_notional"] += 1
                continue
            if not self._liquidity_ok(event):
                stats["fail_liquidity"] += 1
                continue
            if not self._not_blacklisted(event):
                stats["fail_blacklist"] += 1
                continue
            stats["passed"] += 1
            passed.append(event)
        return passed, stats

    def _usd_notional_ok(
        self,
        event: Event,
        threshold_cache: Dict[tuple[str | None, str | None], float],
    ) -> bool:
        value = event.features.usd_notional or 0
        threshold = self._resolve_notional_threshold(event, threshold_cache)
        return value >= threshold

    def _resolve_notional_threshold(
        self,
        event: Event,
        threshold_cache: Dict[tuple[str | None, str | None], float],
    ) -> float:
        if not self._settings.min_usd_notional_dynamic:
            return float(self._settings.min_usd_notional)

        token_symbol = event.token.symbol
        chain = event.token.chain or event.chain
        cache_key = (token_symbol, chain)
        if cache_key in threshold_cache:
            return threshold_cache[cache_key]

        threshold = self._calculate_dynamic_threshold(event, token_symbol, chain)
        threshold_cache[cache_key] = threshold
        return threshold

    def _calculate_dynamic_threshold(
        self,
        event: Event,
        token_symbol: str | None,
        chain: str | None,
    ) -> float:
        fallback = float(self._settings.min_usd_notional_fallback)
        if not token_symbol:
            return fallback

        lookback_minutes = self._settings.min_usd_notional_lookback_minutes
        since = (event.occurred_at or utc_now()) - timedelta(minutes=lookback_minutes)
        history = self._event_repo.get_usd_notional_history(
            token_symbol=token_symbol,
            chain=chain,
            since=since,
        )

        min_samples = self._settings.min_usd_notional_min_samples
        if len(history) < min_samples:
            return fallback

        quantile = self._settings.min_usd_notional_quantile
        threshold = _percentile(history, quantile)
        return max(threshold, fallback)

    def _liquidity_ok(self, event: Event) -> bool:
        liquidity = event.token.liquidity_score or 0
        return liquidity >= self._settings.liquidity_min_score

    def _not_blacklisted(self, event: Event) -> bool:
        return not event.token.blacklist_flags


def _percentile(values: List[float], q: float) -> float:
    if not values:
        return 0.0
    if q <= 0:
        return min(values)
    if q >= 1:
        return max(values)
    sorted_vals = sorted(values)
    rank = q * (len(sorted_vals) - 1)
    lower_index = int(rank)
    upper_index = min(lower_index + 1, len(sorted_vals) - 1)
    weight = rank - lower_index
    return sorted_vals[lower_index] * (1 - weight) + sorted_vals[upper_index] * weight
