from __future__ import annotations

from collections import defaultdict
from typing import Dict, Iterable, List, Sequence
from zoneinfo import ZoneInfo

from ..adapters.gecko_terminal import GeckoTerminalClient
from ..core.types import Signal
from ..core.utils import utc_now
from ..data.repos import SimulatedTradeRepository


class TradeSimulator:
    """紀錄模擬買入並在達到目標時賣出。"""

    def __init__(
        self,
        repo: SimulatedTradeRepository,
        price_client: GeckoTerminalClient,
        gain_threshold: float,
        timezone: ZoneInfo,
    ) -> None:
        self._repo = repo
        self._price_client = price_client
        self._gain_threshold = gain_threshold
        self._timezone = timezone

    def process_signals(self, signals: Sequence[Signal]) -> Dict[str, int]:
        opened = self._open_trades(signals)
        closed = self._close_trades()
        return {"opened": opened, "closed": closed}

    def _open_trades(self, signals: Sequence[Signal]) -> int:
        candidates: List[Signal] = [
            signal
            for signal in signals
            if (signal.metadata or {}).get("signal_type", "buy") == "buy"
            and signal.token.address
        ]
        grouped: Dict[str, List[Signal]] = defaultdict(list)
        for signal in candidates:
            chain = signal.token.chain or ""
            grouped[chain].append(signal)

        opened = 0
        for chain, group in grouped.items():
            addresses = [s.token.address for s in group]
            prices = self._fetch_prices(chain, addresses)
            for signal in group:
                address = signal.token.address
                if not address:
                    continue
                if self._repo.get_open_trade(address, signal.token.chain):
                    continue
                price = prices.get(address.lower())
                if price is None:
                    continue
                target_price = price * (1 + self._gain_threshold)
                now_utc = utc_now()
                now_local = now_utc.astimezone(self._timezone)
                metadata = {
                    "opened_from": (signal.metadata or {}).get("source_event", {}),
                    "opened_at": now_local.isoformat(),
                }
                self._repo.create_trade(
                    token_address=address,
                    token_symbol=signal.token.symbol,
                    chain=signal.token.chain,
                    buy_price=price,
                    target_price=target_price,
                    metadata=metadata,
                    buy_time=now_utc,
                    buy_time_local=now_local,
                )
                opened += 1
        return opened

    def _close_trades(self) -> int:
        open_trades = self._repo.list_open_trades()
        grouped: Dict[str | None, List[str]] = defaultdict(list)
        for trade in open_trades:
            grouped[trade.chain].append(trade.token_address)

        prices_by_chain: Dict[tuple[str | None, str], float] = {}
        for chain, addresses in grouped.items():
            prices = self._fetch_prices(chain, addresses)
            for address, price in prices.items():
                prices_by_chain[(chain, address)] = price

        closed = 0
        for trade in open_trades:
            price = prices_by_chain.get((trade.chain, trade.token_address.lower()))
            if price is None:
                continue
            if price >= trade.target_price:
                now_utc = utc_now()
                now_local = now_utc.astimezone(self._timezone)
                self._repo.close_trade(trade, price, sell_time=now_utc, sell_time_local=now_local)
                extra = trade.extra or {}
                extra["closed_at"] = now_local.isoformat()
                trade.extra = extra
                closed += 1
        return closed

    def _fetch_prices(self, chain: str | None, addresses: Iterable[str]) -> Dict[str, float]:
        try:
            return self._price_client.get_prices(chain or "", addresses)
        except Exception:  # noqa: BLE001
            return {}
