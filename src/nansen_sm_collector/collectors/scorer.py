from __future__ import annotations

from typing import List

from ..config.settings import AppSettings
from ..core.errors import ScoringError
from ..core.types import Event, Signal, SignalReason
from ..core.utils import utc_now


class SignalScorer:
    """依照 Phase-1 規則產生訊號分數。"""

    def __init__(self, settings: AppSettings) -> None:
        self._settings = settings

    def score(self, event: Event) -> Signal:
        """將事件轉換為訊號。"""

        if event.wallet is None:
            raise ScoringError("缺少錢包資訊，無法建立訊號")

        reasons: List[SignalReason] = []
        score = 0.0
        is_buy_signal = False
        is_sell_signal = False

        usd_value = event.features.usd_notional or 0.0
        score += self._settings.weight_usd * self._scale_usd(usd_value)
        if usd_value:
            is_buy_signal = True
            reasons.append(SignalReason(code="smart_buy", message="高金額聰明資金買入"))

        if event.wallet.labels:
            score += self._settings.weight_label
            reasons.append(SignalReason(code="label", message="錢包具 Smart Money 標籤"))

        if event.wallet.alpha_score:
            score += self._settings.weight_alpha * event.wallet.alpha_score
            reasons.append(SignalReason(code="alpha", message="錢包歷史命中率佳"))

        volume_jump = event.features.volume_jump or 0.0
        score += self._settings.weight_volz * self._scale_volume(volume_jump)
        if volume_jump:
            reasons.append(SignalReason(code="vol_jump", message="交易量異常放大"))

        netflow = event.features.smart_money_netflow or 0.0
        if netflow > 0:
            score += self._settings.weight_bias
            is_buy_signal = True
            reasons.append(SignalReason(code="netflow_buy", message="聰明資金淨流入"))
        elif netflow < 0:
            score += self._settings.weight_bias
            is_sell_signal = True
            reasons.append(SignalReason(code="netflow_sell", message="聰明資金淨流出"))

        if self._is_explosive(event):
            score -= self._settings.penalty_explosive
            reasons.append(SignalReason(code="penalty_explosive", message="價格波動過大"))

        if self._is_low_liquidity(event):
            score -= self._settings.penalty_low_liq
            reasons.append(SignalReason(code="penalty_liq", message="流動性不足"))

        signal_type = "buy"
        if netflow < 0 and not is_buy_signal:
            signal_type = "sell"

        metadata = {
            "source_event": event.model_dump(mode="json"),
            "signal_type": signal_type,
        }
        return Signal(
            token=event.token,
            wallets=[event.wallet],
            score=max(score, 0.0),
            reasons=reasons,
            generated_at=utc_now(),
            metadata=metadata,
        )

    def _scale_usd(self, value: float) -> float:
        return min(value / max(self._settings.min_usd_notional, 1), 2.0)

    def _scale_volume(self, value: float) -> float:
        return min(value / max(self._settings.volume_z_th_1h, 1.0), 2.0)

    def _is_explosive(self, event: Event) -> bool:
        return (event.features.volume_jump or 0) > (self._settings.volume_z_th_1h * 3)

    def _is_low_liquidity(self, event: Event) -> bool:
        return (event.token.liquidity_score or 0) < self._settings.liquidity_min_score
