from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class Wallet(BaseModel):
    """錢包資訊與評估分數。"""

    address: str
    labels: List[str] = Field(default_factory=list)
    alpha_score: Optional[float] = None
    last_active_at: Optional[datetime] = None


class Token(BaseModel):
    """標的代幣相關屬性。"""

    address: Optional[str] = None
    symbol: str
    chain: Optional[str] = None
    liquidity_score: Optional[float] = None
    blacklist_flags: List[str] = Field(default_factory=list)


class EventFeature(BaseModel):
    """事件特徵值集合。"""

    usd_notional: Optional[float] = None
    volume_jump: Optional[float] = None
    smart_money_netflow: Optional[float] = None
    is_buy: Optional[bool] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class Event(BaseModel):
    """由 Nansen 來源轉換後的事件。"""

    source: str
    token: Token
    wallet: Optional[Wallet] = None
    tx_hash: Optional[str] = None
    chain: Optional[str] = None
    occurred_at: datetime
    features: EventFeature = Field(default_factory=EventFeature)


class SignalReason(BaseModel):
    """評分時產出的理由敘述。"""

    code: str
    message: str


class Signal(BaseModel):
    """最終給策略與交易模組使用的訊號。"""

    token: Token
    wallets: List[Wallet] = Field(default_factory=list)
    score: float
    reasons: List[SignalReason] = Field(default_factory=list)
    generated_at: datetime
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def summarize(self) -> str:
        """回傳單行摘要。"""

        reason_codes = ",".join(reason.code for reason in self.reasons)
        return f"{self.token.symbol} score={self.score:.2f} reasons={reason_codes}"
