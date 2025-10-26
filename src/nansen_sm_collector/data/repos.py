from __future__ import annotations

from datetime import datetime
from typing import Iterable, List, Optional, Sequence

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


class TokenScreenerRepository(BaseRepository):
    """儲存 Token Screener 快照與最新指標。"""

    def bulk_insert_snapshots(
        self,
        run_id: int,
        snapshots: Sequence[dict],
        captured_at: datetime,
    ) -> None:
        if not snapshots:
            return
        models = []
        for entry in snapshots:
            model = schemas.TokenScreenerSnapshotModel(
                run_id=run_id,
                captured_at=captured_at,
                chain=entry.get("chain", ""),
                token_address=(entry.get("token_address") or "").lower(),
                token_symbol=entry.get("token_symbol") or "",
                token_age_days=entry.get("token_age_days"),
                market_cap_usd=entry.get("market_cap_usd"),
                liquidity=entry.get("liquidity"),
                price_usd=entry.get("price_usd"),
                price_change=entry.get("price_change"),
                fdv=entry.get("fdv"),
                fdv_mc_ratio=entry.get("fdv_mc_ratio"),
                buy_volume=entry.get("buy_volume"),
                sell_volume=entry.get("sell_volume"),
                volume=entry.get("volume"),
                netflow=entry.get("netflow"),
                inflow_fdv_ratio=entry.get("inflow_fdv_ratio"),
                outflow_fdv_ratio=entry.get("outflow_fdv_ratio"),
            )
            models.append(model)
        self.session.bulk_save_objects(models)

    def upsert_market_metrics(
        self,
        snapshots: Sequence[dict],
        captured_at: datetime,
    ) -> None:
        for entry in snapshots:
            chain = entry.get("chain", "")
            token_address = (entry.get("token_address") or "").lower()
            token_symbol = entry.get("token_symbol") or ""
            model = (
                self.session.query(schemas.TokenMarketMetricModel)
                .filter(schemas.TokenMarketMetricModel.chain == chain)
                .filter(schemas.TokenMarketMetricModel.token_address == token_address)
                .one_or_none()
            )
            if model is None:
                model = schemas.TokenMarketMetricModel(
                    chain=chain,
                    token_address=token_address,
                    token_symbol=token_symbol,
                    snapshot_captured_at=captured_at,
                )
            model.token_symbol = token_symbol or model.token_symbol
            model.snapshot_captured_at = captured_at
            model.market_cap_usd = entry.get("market_cap_usd")
            model.liquidity = entry.get("liquidity")
            model.price_usd = entry.get("price_usd")
            model.price_change = entry.get("price_change")
            model.fdv = entry.get("fdv")
            model.fdv_mc_ratio = entry.get("fdv_mc_ratio")
            model.buy_volume = entry.get("buy_volume")
            model.sell_volume = entry.get("sell_volume")
            model.volume = entry.get("volume")
            model.netflow = entry.get("netflow")
            model.inflow_fdv_ratio = entry.get("inflow_fdv_ratio")
            model.outflow_fdv_ratio = entry.get("outflow_fdv_ratio")
            model.token_age_days = entry.get("token_age_days")
            model.updated_at = utc_now()
            self.session.add(model)


class TradeCandidateRepository(BaseRepository):
    """儲存每次策略候選清單資料。"""

    def bulk_insert(self, run_id: int, entries: Sequence[dict]) -> None:
        if not entries:
            return
        models: List[schemas.TradeCandidateModel] = []
        for entry in entries:
            model = schemas.TradeCandidateModel(
                run_id=run_id,
                scope=entry.get("scope", "all"),
                rank=entry.get("rank", 0),
                token_symbol=entry.get("token_symbol", ""),
                token_address=entry.get("token_address"),
                chain=entry.get("chain", ""),
                composite_score=entry.get("composite_score"),
                market_score=entry.get("market_score"),
                liquidity_score=entry.get("liquidity_score"),
                smart_money_score=entry.get("smart_money_score"),
                has_smart_money=bool(entry.get("has_smart_money")),
                market=entry.get("market"),
                smart_money=entry.get("smart_money"),
            )
            models.append(model)
        self.session.bulk_save_objects(models)
