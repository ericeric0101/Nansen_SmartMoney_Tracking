from __future__ import annotations

from datetime import datetime
from typing import Iterable, List, Optional

from sqlalchemy.orm import Session

from ..core.types import Event, Signal, Token, Wallet
from ..core.utils import utc_now
from . import schemas


class BaseRepository:
    """封裝共同的 Session 行為。"""

    def __init__(self, session: Session) -> None:
        self.session = session


class TokenRepository(BaseRepository):
    """代幣資料存取。"""

    def get_by_symbol(self, symbol: str, chain: Optional[str] = None) -> Optional[schemas.TokenModel]:
        query = self.session.query(schemas.TokenModel).filter_by(symbol=symbol)
        if chain:
            query = query.filter_by(chain=chain)
        return query.one_or_none()

    def upsert(self, token: Token) -> schemas.TokenModel:
        model = self.get_by_symbol(token.symbol, token.chain)
        if model is None:
            model = schemas.TokenModel(symbol=token.symbol, chain=token.chain)
        model.address = token.address
        model.liquidity_score = token.liquidity_score
        model.blacklist_flags = ",".join(token.blacklist_flags)
        self.session.add(model)
        return model


class WalletRepository(BaseRepository):
    """錢包資料存取。"""

    def get_by_address(self, address: str) -> Optional[schemas.WalletModel]:
        return (
            self.session.query(schemas.WalletModel)
            .filter(schemas.WalletModel.address == address)
            .one_or_none()
        )

    def upsert(self, wallet: Wallet) -> schemas.WalletModel:
        model = self.get_by_address(wallet.address)
        if model is None:
            model = schemas.WalletModel(address=wallet.address)
        model.labels = ",".join(wallet.labels)
        model.alpha_score = wallet.alpha_score
        model.last_active_at = wallet.last_active_at
        self.session.add(model)
        return model


class EventRepository(BaseRepository):
    """事件資料存取。"""

    def create(self, event: Event, token_model: schemas.TokenModel, wallet_model: Optional[schemas.WalletModel]) -> schemas.EventModel:
        model = schemas.EventModel(
            token=token_model,
            wallet=wallet_model,
            source=event.source,
            tx_hash=event.tx_hash,
            chain=event.chain,
            occurred_at=event.occurred_at,
            features=event.features.model_dump(),
        )
        self.session.add(model)
        return model

    def get_usd_notional_history(
        self,
        token_symbol: str,
        chain: Optional[str],
        since: datetime,
    ) -> List[float]:
        query = (
            self.session.query(schemas.EventModel)
            .join(schemas.TokenModel)
            .filter(schemas.TokenModel.symbol == token_symbol)
            .filter(schemas.EventModel.occurred_at >= since)
        )
        if chain:
            query = query.filter(schemas.TokenModel.chain == chain)
        events = query.all()
        values: List[float] = []
        for event in events:
            usd_notional = event.features.get("usd_notional") if event.features else None
            if usd_notional is None:
                continue
            try:
                values.append(float(usd_notional))
            except (TypeError, ValueError):
                continue
        return values


class SignalRepository(BaseRepository):
    """訊號資料存取。"""

    def create(
        self,
        signal: Signal,
        token_model: schemas.TokenModel,
        wallet_models: Iterable[schemas.WalletModel],
    ) -> schemas.SignalModel:
        model = schemas.SignalModel(
            token=token_model,
            score=signal.score,
            reasons=[reason.model_dump() for reason in signal.reasons],
            generated_at=signal.generated_at,
            context=signal.metadata,
        )
        model.wallets = list(wallet_models)
        self.session.add(model)
        return model

    def top_signals(self, limit: int = 10) -> List[schemas.SignalModel]:
        return (
            self.session.query(schemas.SignalModel)
            .order_by(schemas.SignalModel.score.desc())
            .limit(limit)
            .all()
        )


class SimulatedTradeRepository(BaseRepository):
    """管理模擬交易資料。"""

    def get_open_trade(self, token_address: str, chain: Optional[str]) -> Optional[schemas.SimulatedTradeModel]:
        query = (
            self.session.query(schemas.SimulatedTradeModel)
            .filter(schemas.SimulatedTradeModel.token_address == token_address.lower())
            .filter(schemas.SimulatedTradeModel.status == "OPEN")
        )
        if chain:
            query = query.filter(schemas.SimulatedTradeModel.chain == chain)
        return query.one_or_none()

    def create_trade(
        self,
        token_address: str,
        token_symbol: str,
        chain: Optional[str],
        buy_price: float,
        target_price: float,
        metadata: Optional[dict] = None,
        buy_time: Optional[datetime] = None,
        buy_time_local: Optional[datetime] = None,
    ) -> schemas.SimulatedTradeModel:
        model = schemas.SimulatedTradeModel(
            token_address=token_address.lower(),
            token_symbol=token_symbol,
            chain=chain,
            buy_price=buy_price,
            target_price=target_price,
            buy_time=buy_time or utc_now(),
            buy_time_local=buy_time_local or utc_now(),
            extra=metadata or {},
        )
        self.session.add(model)
        return model

    def list_open_trades(self) -> List[schemas.SimulatedTradeModel]:
        return (
            self.session.query(schemas.SimulatedTradeModel)
            .filter(schemas.SimulatedTradeModel.status == "OPEN")
            .all()
        )

    def close_trade(
        self,
        trade: schemas.SimulatedTradeModel,
        sell_price: float,
        sell_time: Optional[datetime] = None,
        sell_time_local: Optional[datetime] = None,
    ) -> None:
        trade.status = "CLOSED"
        trade.sell_price = sell_price
        trade.sell_time = sell_time or utc_now()
        trade.sell_time_local = sell_time_local or utc_now()


class ExecutedTradeRepository(BaseRepository):
    """紀錄實際透過 0x API 執行的交易。"""

    def create_record(
        self,
        *,
        mode: str,
        status: str,
        side: str,
        chain_id: int,
        base_token_symbol: str,
        base_token_address: str,
        quote_token_symbol: str,
        quote_token_address: str,
        sell_token_address: str,
        buy_token_address: str,
        sell_amount: str,
        sell_amount_decimal: Optional[float] = None,
        buy_amount: Optional[str] = None,
        buy_amount_decimal: Optional[float] = None,
        price: Optional[float] = None,
        slippage_bps: Optional[int] = None,
        integrator_fee_usdc: Optional[float] = None,
        allowance_target: Optional[str] = None,
        quote_id: Optional[str] = None,
        tx_hash: Optional[str] = None,
        error_message: Optional[str] = None,
        price_response: Optional[dict] = None,
        quote_response: Optional[dict] = None,
        transaction_payload: Optional[dict] = None,
        executed_at: Optional[datetime] = None,
        executed_at_local: Optional[datetime] = None,
    ) -> schemas.ExecutedTradeModel:
        model = schemas.ExecutedTradeModel(
            mode=mode,
            status=status,
            side=side,
            chain_id=chain_id,
            base_token_symbol=base_token_symbol,
            base_token_address=base_token_address,
            quote_token_symbol=quote_token_symbol,
            quote_token_address=quote_token_address,
            sell_token_address=sell_token_address,
            buy_token_address=buy_token_address,
            sell_amount=sell_amount,
            sell_amount_decimal=sell_amount_decimal,
            buy_amount=buy_amount,
            buy_amount_decimal=buy_amount_decimal,
            price=price,
            slippage_bps=slippage_bps,
            integrator_fee_usdc=integrator_fee_usdc,
            allowance_target=allowance_target,
            quote_id=quote_id,
            tx_hash=tx_hash,
            error_message=error_message,
            price_response=price_response,
            quote_response=quote_response,
            transaction_payload=transaction_payload,
            executed_at=executed_at or utc_now(),
            executed_at_local=executed_at_local or utc_now(),
        )
        self.session.add(model)
        self.session.flush()
        return model

    def get_by_id(self, trade_id: int) -> Optional[schemas.ExecutedTradeModel]:
        return self.session.get(schemas.ExecutedTradeModel, trade_id)

    def update_status(
        self,
        trade: schemas.ExecutedTradeModel,
        *,
        status: str,
        tx_hash: Optional[str] = None,
        error_message: Optional[str] = None,
        executed_at: Optional[datetime] = None,
        executed_at_local: Optional[datetime] = None,
        quote_response: Optional[dict] = None,
        transaction_payload: Optional[dict] = None,
        integrator_fee_usdc: Optional[float] = None,
    ) -> None:
        trade.status = status
        if tx_hash is not None:
            trade.tx_hash = tx_hash
        if error_message is not None:
            trade.error_message = error_message
        if executed_at is not None:
            trade.executed_at = executed_at
        if executed_at_local is not None:
            trade.executed_at_local = executed_at_local
        if quote_response is not None:
            trade.quote_response = quote_response
        if transaction_payload is not None:
            trade.transaction_payload = transaction_payload
        if integrator_fee_usdc is not None:
            trade.integrator_fee_usdc = integrator_fee_usdc


class RunHistoryRepository(BaseRepository):
    """紀錄每次 Pipeline 執行的彙整資料。"""

    def create_run(
        self,
        run_id: str,
        executed_at: datetime,
        executed_at_local: datetime,
        total_signals: int,
        buy_signals: int,
        sell_signals: int,
        stats: dict,
    ) -> schemas.RunHistoryModel:
        model = schemas.RunHistoryModel(
            run_uuid=run_id,
            executed_at=executed_at,
            executed_at_local=executed_at_local,
            total_signals=total_signals,
            buy_signals=buy_signals,
            sell_signals=sell_signals,
            stats=stats,
        )
        self.session.add(model)
        self.session.flush()
        return model

    def bulk_insert_summaries(self, run_uuid: str, entries: Iterable[dict]) -> None:
        run = (
            self.session.query(schemas.RunHistoryModel)
            .filter(schemas.RunHistoryModel.run_uuid == run_uuid)
            .one()
        )
        for entry in entries:
            generated_at = entry.get("generated_at")
            generated_dt = (
                datetime.fromisoformat(generated_at) if isinstance(generated_at, str) else utc_now()
            )
            model = schemas.SignalSummaryModel(
                run_id=run.id,
                section=entry.get("section", ""),
                token_symbol=entry.get("token_symbol", ""),
                token_address=entry.get("token_address"),
                chain=entry.get("chain"),
                score=entry.get("score", 0.0),
                reasons=entry.get("reasons", []),
                count=entry.get("count", 1),
                top_wallets=entry.get("top_wallets", []),
                generated_at=generated_dt,
                generated_at_local=datetime.fromisoformat(
                    entry.get("generated_at_local")
                )
                if isinstance(entry.get("generated_at_local"), str)
                else utc_now(),
            )
            self.session.add(model)
