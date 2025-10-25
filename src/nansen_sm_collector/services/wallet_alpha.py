from __future__ import annotations

from collections import deque
from typing import Deque

from sqlalchemy.orm import sessionmaker

from ..data import schemas


class WalletAlphaService:
    """依據歷史事件評估錢包命中率。"""

    def __init__(self, session_factory: sessionmaker, lookback: int = 100) -> None:
        self._session_factory = session_factory
        self._lookback = lookback

    def score_wallet(self, address: str) -> float:
        """回傳介於 0 與 1 之間的簡易 Alpha 分數。"""

        with self._session_factory() as session:
            wallet = (
                session.query(schemas.WalletModel)
                .filter(schemas.WalletModel.address == address)
                .one_or_none()
            )
            if wallet is None:
                return 0.0

            events = (
                session.query(schemas.EventModel)
                .filter(schemas.EventModel.wallet_id == wallet.id)
                .order_by(schemas.EventModel.occurred_at.desc())
                .limit(self._lookback)
                .all()
            )

        if not events:
            return 0.0

        scores: Deque[float] = deque(maxlen=self._lookback)
        for event in events:
            usd_notional = event.features.get("usd_notional", 0)
            scores.append(1.0 if usd_notional and usd_notional > 0 else 0.0)

        alpha = sum(scores) / len(scores)
        return round(alpha, 4)
