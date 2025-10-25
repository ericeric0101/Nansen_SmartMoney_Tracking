from __future__ import annotations

from .settings import AppSettings


def validate_weights(settings: AppSettings) -> None:
    """確認評分權重與懲罰係數位於合理區間。"""

    total_weight = settings.weight_usd + settings.weight_label + settings.weight_alpha
    total_weight += settings.weight_volz + settings.weight_bias
    if not 0.99 <= total_weight <= 1.01:
        raise ValueError("評分權重總和必須接近 1.0")

    if not (0 <= settings.penalty_explosive <= 1 and 0 <= settings.penalty_low_liq <= 1):
        raise ValueError("懲罰係數需介於 0 與 1 之間")
