from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base
from ..core.utils import utc_now

signal_wallets = Table(
    "signal_wallets",
    Base.metadata,
    Column("signal_id", ForeignKey("signals.id"), primary_key=True),
    Column("wallet_id", ForeignKey("wallets.id"), primary_key=True),
)


class WalletModel(Base):
    """錢包資料表。"""

    __tablename__ = "wallets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    address: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    labels: Mapped[str] = mapped_column(Text, default="")
    alpha_score: Mapped[float | None] = mapped_column(Float)
    last_active_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    signals: Mapped[list["SignalModel"]] = relationship(
        secondary=signal_wallets, back_populates="wallets"
    )


class TokenModel(Base):
    """代幣資料表。"""

    __tablename__ = "tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    address: Mapped[str | None] = mapped_column(String(128))
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    chain: Mapped[str | None] = mapped_column(String(32))
    liquidity_score: Mapped[float | None] = mapped_column(Float)
    blacklist_flags: Mapped[str] = mapped_column(Text, default="")

    events: Mapped[list["EventModel"]] = relationship(back_populates="token")
    signals: Mapped[list["SignalModel"]] = relationship(back_populates="token")


class EventModel(Base):
    """事件資料表。"""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    token_id: Mapped[int] = mapped_column(ForeignKey("tokens.id"), nullable=False)
    wallet_id: Mapped[int | None] = mapped_column(ForeignKey("wallets.id"))
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    tx_hash: Mapped[str | None] = mapped_column(String(128))
    chain: Mapped[str | None] = mapped_column(String(32))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    features: Mapped[dict] = mapped_column(JSON, default=dict)

    token: Mapped["TokenModel"] = relationship(back_populates="events")
    wallet: Mapped["WalletModel"] = relationship()


class SignalModel(Base):
    """訊號資料表。"""

    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    token_id: Mapped[int] = mapped_column(ForeignKey("tokens.id"), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    reasons: Mapped[list] = mapped_column(JSON, default=list)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    context: Mapped[dict] = mapped_column("metadata", JSON, default=dict)

    token: Mapped["TokenModel"] = relationship(back_populates="signals")
    wallets: Mapped[list["WalletModel"]] = relationship(
        secondary=signal_wallets,
        back_populates="signals",
    )


class SimulatedTradeModel(Base):
    """模擬交易紀錄。"""

    __tablename__ = "simulated_trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    token_address: Mapped[str] = mapped_column(String(128), nullable=False)
    token_symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    chain: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="OPEN", nullable=False)
    buy_price: Mapped[float] = mapped_column(Float, nullable=False)
    buy_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    buy_time_local: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    target_price: Mapped[float] = mapped_column(Float, nullable=False)
    sell_price: Mapped[float | None] = mapped_column(Float)
    sell_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sell_time_local: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    extra: Mapped[dict] = mapped_column(JSON, default=dict)


class ExecutedTradeModel(Base):
    """記錄透過 0x Swap API 執行的交易。"""

    __tablename__ = "executed_trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)  # SIMULATION 或 LIVE
    status: Mapped[str] = mapped_column(String(16), default="PENDING", nullable=False)
    side: Mapped[str] = mapped_column(String(16), nullable=False)
    chain_id: Mapped[int] = mapped_column(Integer, nullable=False)

    base_token_symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    base_token_address: Mapped[str] = mapped_column(String(128), nullable=False)
    quote_token_symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    quote_token_address: Mapped[str] = mapped_column(String(128), nullable=False)

    sell_token_address: Mapped[str] = mapped_column(String(128), nullable=False)
    buy_token_address: Mapped[str] = mapped_column(String(128), nullable=False)
    sell_amount: Mapped[str] = mapped_column(String(128), nullable=False)
    sell_amount_decimal: Mapped[float | None] = mapped_column(Float)
    buy_amount: Mapped[str | None] = mapped_column(String(128))
    buy_amount_decimal: Mapped[float | None] = mapped_column(Float)
    price: Mapped[float | None] = mapped_column(Float)
    slippage_bps: Mapped[int | None] = mapped_column(Integer)

    allowance_target: Mapped[str | None] = mapped_column(String(128))
    quote_id: Mapped[str | None] = mapped_column(String(64))
    tx_hash: Mapped[str | None] = mapped_column(String(128))
    error_message: Mapped[str | None] = mapped_column(Text)

    price_response: Mapped[dict | None] = mapped_column(JSON)
    quote_response: Mapped[dict | None] = mapped_column(JSON)
    transaction_payload: Mapped[dict | None] = mapped_column(JSON)

    executed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    executed_at_local: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    integrator_fee_usdc: Mapped[float | None] = mapped_column(Float)


class RunHistoryModel(Base):
    """每次管線執行的彙整紀錄。"""

    __tablename__ = "run_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_uuid: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    executed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    executed_at_local: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    total_signals: Mapped[int] = mapped_column(Integer, default=0)
    buy_signals: Mapped[int] = mapped_column(Integer, default=0)
    sell_signals: Mapped[int] = mapped_column(Integer, default=0)
    stats: Mapped[dict] = mapped_column(JSON, default=dict)

    summaries: Mapped[list["SignalSummaryModel"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
    )
    screener_snapshots: Mapped[list["TokenScreenerSnapshotModel"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
    )
    trade_candidates: Mapped[list["TradeCandidateModel"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
    )


class TokenScreenerSnapshotModel(Base):
    """保存 Token Screener 單次快照紀錄。"""

    __tablename__ = "token_screener_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("run_history.id"), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    chain: Mapped[str] = mapped_column(String(32), nullable=False)
    token_address: Mapped[str] = mapped_column(String(128), nullable=False)
    token_symbol: Mapped[str] = mapped_column(String(128), nullable=False)
    token_age_days: Mapped[float | None] = mapped_column(Float)
    market_cap_usd: Mapped[float | None] = mapped_column(Float)
    liquidity: Mapped[float | None] = mapped_column(Float)
    price_usd: Mapped[float | None] = mapped_column(Float)
    price_change: Mapped[float | None] = mapped_column(Float)
    fdv: Mapped[float | None] = mapped_column(Float)
    fdv_mc_ratio: Mapped[float | None] = mapped_column(Float)
    buy_volume: Mapped[float | None] = mapped_column(Float)
    sell_volume: Mapped[float | None] = mapped_column(Float)
    volume: Mapped[float | None] = mapped_column(Float)
    netflow: Mapped[float | None] = mapped_column(Float)
    inflow_fdv_ratio: Mapped[float | None] = mapped_column(Float)
    outflow_fdv_ratio: Mapped[float | None] = mapped_column(Float)

    run: Mapped["RunHistoryModel"] = relationship(back_populates="screener_snapshots")


class TokenMarketMetricModel(Base):
    """彙總每個 Token 最新市場指標。"""

    __tablename__ = "token_market_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chain: Mapped[str] = mapped_column(String(32), nullable=False)
    token_address: Mapped[str] = mapped_column(String(128), nullable=False)
    token_symbol: Mapped[str] = mapped_column(String(128), nullable=False)
    snapshot_captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    market_cap_usd: Mapped[float | None] = mapped_column(Float)
    liquidity: Mapped[float | None] = mapped_column(Float)
    price_usd: Mapped[float | None] = mapped_column(Float)
    price_change: Mapped[float | None] = mapped_column(Float)
    fdv: Mapped[float | None] = mapped_column(Float)
    fdv_mc_ratio: Mapped[float | None] = mapped_column(Float)
    buy_volume: Mapped[float | None] = mapped_column(Float)
    sell_volume: Mapped[float | None] = mapped_column(Float)
    volume: Mapped[float | None] = mapped_column(Float)
    netflow: Mapped[float | None] = mapped_column(Float)
    inflow_fdv_ratio: Mapped[float | None] = mapped_column(Float)
    outflow_fdv_ratio: Mapped[float | None] = mapped_column(Float)
    token_age_days: Mapped[float | None] = mapped_column(Float)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    __table_args__ = (
        UniqueConstraint("chain", "token_address", name="uq_token_market_metrics_token"),
    )


class TradeCandidateModel(Base):
    """記錄策略候選清單與評分。"""

    __tablename__ = "trade_candidates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("run_history.id"), nullable=False)
    scope: Mapped[str] = mapped_column(String(32), nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    token_symbol: Mapped[str] = mapped_column(String(128), nullable=False)
    token_address: Mapped[str | None] = mapped_column(String(128))
    chain: Mapped[str] = mapped_column(String(32), nullable=False)
    composite_score: Mapped[float | None] = mapped_column(Float)
    market_score: Mapped[float | None] = mapped_column(Float)
    liquidity_score: Mapped[float | None] = mapped_column(Float)
    smart_money_score: Mapped[float | None] = mapped_column(Float)
    has_smart_money: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    market: Mapped[dict | None] = mapped_column(JSON)
    smart_money: Mapped[dict | None] = mapped_column(JSON)

    run: Mapped["RunHistoryModel"] = relationship(back_populates="trade_candidates")


class SignalSummaryModel(Base):
    """每次執行對應的訊號摘要。"""

    __tablename__ = "signal_summaries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("run_history.id"), nullable=False)
    section: Mapped[str] = mapped_column(String(32), nullable=False)
    token_symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    token_address: Mapped[str | None] = mapped_column(String(128))
    chain: Mapped[str | None] = mapped_column(String(32))
    score: Mapped[float] = mapped_column(Float, nullable=False)
    reasons: Mapped[list] = mapped_column(JSON, default=list)
    count: Mapped[int] = mapped_column(Integer, default=1)
    top_wallets: Mapped[list] = mapped_column(JSON, default=list)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    generated_at_local: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    run: Mapped["RunHistoryModel"] = relationship(back_populates="summaries")
