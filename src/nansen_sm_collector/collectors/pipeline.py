from __future__ import annotations

import json
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Sequence
from uuid import uuid4
from zoneinfo import ZoneInfo

from ..adapters.gecko_terminal import GeckoTerminalClient
from ..adapters.mock_nansen import MockNansenClient
from ..adapters.nansen_api import NansenAPIClient
from ..config.settings import AppSettings
from ..core.types import Event, Signal
from ..core.utils import utc_now
from ..data import db
from ..data.repos import (
    EventRepository,
    RunHistoryRepository,
    SignalRepository,
    SimulatedTradeRepository,
    TokenRepository,
    WalletRepository,
)
from ..services.trade_simulator import TradeSimulator
from ..services.wallet_alpha import WalletAlphaService
from .enrich import EventEnricher
from .filters import EventFilterSet
from .normalize import EventNormalizer
from .scorer import SignalScorer


@dataclass
class PipelineResult:
    """封裝管線輸出結果。"""

    signals: Sequence[Signal]
    report_path: Path | None = None
    stats: dict[str, object] | None = None
    history_entries: List[dict] | None = None


class CollectorPipeline:
    """Phase-1 蒐集與評分流程。"""

    def __init__(self, settings: AppSettings) -> None:
        self._settings = settings
        self._engine = db.create_db_engine(settings.db_url)
        db.Base.metadata.create_all(self._engine)
        db.upgrade_schema(self._engine)
        self._session_factory = db.create_session_factory(self._engine)
        self._logger = logging.getLogger(__name__)

    def run_once(self, use_mock: bool = True) -> PipelineResult:
        """執行單次資料蒐集。"""

        client = self._build_client(use_mock)
        gecko_client: GeckoTerminalClient | None = None
        if self._settings.trade_simulation_enabled and not use_mock:
            gecko_client = GeckoTerminalClient(
                base_url=self._settings.gecko_terminal_base_url,
                version=self._settings.gecko_terminal_version,
            )
        wallet_alpha = WalletAlphaService(session_factory=self._session_factory)
        normalizer = EventNormalizer()
        scorer = SignalScorer(self._settings)
        timezone = ZoneInfo(self._settings.timezone)
        report_path: Path | None = None
        history_entries: List[dict] = []

        with db.session_scope(self._session_factory) as session:
            token_repo = TokenRepository(session)
            wallet_repo = WalletRepository(session)
            event_repo = EventRepository(session)
            signal_repo = SignalRepository(session)
            run_repo = RunHistoryRepository(session)
            filters = EventFilterSet(self._settings, event_repo)
            trade_simulator: TradeSimulator | None = None
            if gecko_client and self._settings.trade_simulation_enabled:
                trade_repo = SimulatedTradeRepository(session)
                trade_simulator = TradeSimulator(
                    repo=trade_repo,
                    price_client=gecko_client,
                    gain_threshold=self._settings.trade_simulation_gain,
                    timezone=timezone,
                )

            stats: dict[str, object] = {}
            buy_count = 0
            sell_count = 0

            events, source_stats = self._fetch_and_normalize(client, normalizer)
            stats.update(source_stats)
            enricher = EventEnricher(
                client=client,
                wallet_alpha=wallet_alpha,
                enable_labels=self._settings.nansen_enable_wallet_labels,
            )
            enriched_events = enricher.enrich(events)
            stats["enriched_events"] = len(enriched_events)
            merged_events = self._merge_events(enriched_events)
            stats["merged_events"] = len(merged_events)
            filtered_events, filter_stats = filters.apply(merged_events)
            stats["filter_stats"] = filter_stats
            signals: List[Signal] = []
            for event in filtered_events:
                signal = scorer.score(event)
                self._persist_entities(
                    signal=signal,
                    event=event,
                    token_repo=token_repo,
                    wallet_repo=wallet_repo,
                    event_repo=event_repo,
                    signal_repo=signal_repo,
                )
                signals.append(signal)
                if (signal.metadata or {}).get("signal_type") == "sell":
                    sell_count += 1
                else:
                    buy_count += 1

            stats["signals"] = len(signals)
            stats["buy_signals"] = buy_count
            stats["sell_signals"] = sell_count

            if trade_simulator:
                trade_stats = trade_simulator.process_signals(signals)
                stats["trade_simulation"] = trade_stats

            run_time = utc_now()
            run_time_local = run_time.astimezone(timezone)
            report_path, history_entries = self._write_report(signals, run_time, run_time_local)
            run_id = str(uuid4())
            run_repo.create_run(
                run_id=run_id,
                executed_at=run_time,
                executed_at_local=run_time_local,
                total_signals=len(signals),
                buy_signals=buy_count,
                sell_signals=sell_count,
                stats=stats,
            )
            run_repo.bulk_insert_summaries(run_id, history_entries or [])

        if isinstance(client, NansenAPIClient):
            client.close()
        if gecko_client:
            gecko_client.close()
        if self._logger.isEnabledFor(logging.INFO):
            self._logger.info("pipeline_stats", extra={"stats": stats})
        return PipelineResult(
            signals=signals,
            report_path=report_path,
            stats=stats,
            history_entries=history_entries,
        )

    def _build_client(self, use_mock: bool):
        if use_mock:
            return MockNansenClient()
        return NansenAPIClient(
            base_url=str(self._settings.nansen_base_url),
            api_key=self._settings.nansen_api_key,
        )

    def _fetch_and_normalize(
        self,
        client: NansenAPIClient | MockNansenClient,
        normalizer: EventNormalizer,
    ) -> tuple[List[Event], dict[str, int]]:
        stats: dict[str, int] = {}

        dex_events = self._fetch_dex_events(client, normalizer)
        stats["dex_events"] = len(dex_events)

        screener_payload = client.fetch_token_screener(self._build_token_screener_payload())
        screener_events = normalizer.token_screener(screener_payload)
        stats["token_screener_events"] = len(screener_events)

        netflow_payload = client.fetch_netflows(self._build_netflow_payload())
        netflow_events = normalizer.netflows(netflow_payload)
        stats["netflow_events"] = len(netflow_events)

        events: List[Event] = []
        events.extend(dex_events)
        events.extend(screener_events)
        events.extend(netflow_events)
        stats["total_events"] = len(events)
        return events, stats

    def _fetch_dex_events(
        self,
        client: NansenAPIClient | MockNansenClient,
        normalizer: EventNormalizer,
    ) -> List[Event]:
        payload = self._build_dex_payload()
        response = client.fetch_dex_trades(payload)
        if isinstance(response, dict):
            data = response.get("data", [])
            chains = payload.get("chains", [])
            default_chain = chains[0] if len(chains) == 1 else None
            if default_chain:
                for item in data:
                    item.setdefault("chain", default_chain)
        return normalizer.dex_trades(response)

    def _merge_events(self, events: Iterable[Event]) -> List[Event]:
        grouped: Dict[tuple[str | None, str | None], Dict[str, List[Event]]] = defaultdict(
            lambda: {"dex_trades": [], "token_screener": [], "netflows": []}
        )
        for event in events:
            key = (event.token.symbol, event.token.chain or event.chain)
            buckets = grouped[key]
            buckets.setdefault(event.source, [])
            buckets[event.source].append(event)

        merged_events: List[Event] = []
        for buckets in grouped.values():
            dex_events = buckets.get("dex_trades", [])
            screener_events = buckets.get("token_screener", [])
            netflow_events = buckets.get("netflows", [])

            if not dex_events or not screener_events or not netflow_events:
                continue

            best_screener = max(
                screener_events,
                key=lambda e: e.token.liquidity_score or 0,
            )
            best_netflow = max(
                netflow_events,
                key=lambda e: abs(e.features.smart_money_netflow or 0),
            )

            netflow_value = best_netflow.features.smart_money_netflow or 0.0
            if abs(netflow_value) < self._settings.netflow_min_positive:
                continue

            for dex_event in dex_events:
                cloned = dex_event.model_copy(deep=True)

                cloned.token.liquidity_score = best_screener.token.liquidity_score
                cloned.token.address = cloned.token.address or best_screener.token.address
                cloned.token.chain = cloned.token.chain or best_screener.token.chain

                metadata = dict(cloned.features.metadata)
                metadata.update(
                    {
                        "screener_buy_volume": best_screener.features.metadata.get("buy_volume"),
                        "screener_sell_volume": best_screener.features.metadata.get("sell_volume"),
                        "screener_netflow": best_screener.features.metadata.get("netflow"),
                        "netflow_24h_usd": best_netflow.features.metadata.get("net_flow_24h_usd"),
                        "netflow_7d_usd": best_netflow.features.metadata.get("net_flow_7d_usd"),
                        "netflow_30d_usd": best_netflow.features.metadata.get("net_flow_30d_usd"),
                        "trader_count": best_netflow.features.metadata.get("trader_count"),
                        "netflow_value": netflow_value,
                    }
                )
                cloned.features.metadata = metadata
                cloned.features.smart_money_netflow = netflow_value

                merged_events.append(cloned)

        return merged_events

    def _build_dex_payload(self) -> dict:
        filters: dict = {}
        include_labels = self._settings.dex_include_labels
        exclude_labels = self._settings.dex_exclude_labels
        if include_labels:
            filters["include_smart_money_labels"] = include_labels
        if exclude_labels:
            filters["exclude_smart_money_labels"] = exclude_labels

        filters["token_bought_age_days"] = {
            "min": self._settings.nansen_dex_min_age_days,
            "max": self._settings.nansen_dex_max_age_days,
        }

        trade_filter: dict = {"min": self._settings.min_usd_notional}
        if self._settings.nansen_dex_trade_max_usd:
            trade_filter["max"] = self._settings.nansen_dex_trade_max_usd
        filters["trade_value_usd"] = trade_filter

        if self._settings.nansen_dex_token_address:
            filters["token_bought_address"] = self._settings.nansen_dex_token_address

        return {
            "chains": self._settings.chains,
            "filters": filters,
            "pagination": {"page": 1, "per_page": 100},
            "order_by": [
                {"field": "chain", "direction": "ASC"},
            ],
        }

    def _build_token_screener_payload(self) -> dict:
        return {
            "chains": self._settings.chains,
            "date": self._build_time_window(hours=24),
            "pagination": {"page": 1, "per_page": 25},
            "filters": {
                "only_smart_money": True,
            },
            "order_by": [
                {"field": "volume", "direction": "DESC"},
            ],
        }

    def _build_netflow_payload(self) -> dict:
        return {
            "chains": self._settings.chains,
            "filters": {
                "include_smart_money_labels": ["Fund", "Smart Trader", "30D Smart Trader"],
            },
            "pagination": {"page": 1, "per_page": 20},
            "order_by": [
                {"field": "net_flow_7d_usd", "direction": "DESC"},
            ],
        }

    def _build_time_window(self, hours: int) -> dict:
        end = utc_now()
        start = end - timedelta(hours=hours)
        return {
            "from": self._to_iso(start),
            "to": self._to_iso(end),
        }

    @staticmethod
    def _to_iso(value: datetime) -> str:
        return value.replace(microsecond=0).isoformat().replace("+00:00", "Z")

    def _persist_entities(
        self,
        signal: Signal,
        event: Event,
        token_repo: TokenRepository,
        wallet_repo: WalletRepository,
        event_repo: EventRepository,
        signal_repo: SignalRepository,
    ) -> None:
        token_model = token_repo.upsert(signal.token)
        wallet_model = None
        if event.wallet:
            wallet_model = wallet_repo.upsert(event.wallet)
        event_repo.create(event, token_model=token_model, wallet_model=wallet_model)
        signal_repo.create(signal, token_model=token_model, wallet_models=[wallet_model] if wallet_model else [])

    def _write_report(
        self,
        signals: Sequence[Signal],
        run_time: datetime,
        run_time_local: datetime,
    ) -> tuple[Path, List[dict]]:
        report_dir = Path("reports")
        report_dir.mkdir(exist_ok=True)
        report_path = report_dir / "phase1_latest.md"
        timestamp_text = run_time.isoformat()
        timestamp_local_text = run_time_local.isoformat()
        lines = [
            "# Phase-1 Signals Summary",
            "",
            f"產出時間 (UTC): {timestamp_text}",
            f"當地時間 ({self._settings.timezone}): {timestamp_local_text}",
            "",
        ]
        history_entries: List[dict] = []
        if not signals:
            lines.append("尚未產生任何訊號。")
        else:
            buy_groups: Dict[tuple[str, str | None], List[Signal]] = {}
            sell_groups: Dict[tuple[str, str | None], List[Signal]] = {}

            for signal in signals:
                signal_type = (signal.metadata or {}).get("signal_type", "buy")
                key = (signal.token.symbol, signal.token.address)
                if signal_type == "sell":
                    sell_groups.setdefault(key, []).append(signal)
                else:
                    buy_groups.setdefault(key, []).append(signal)

            def _sorted_groups(groups: Dict[tuple[str, str | None], List[Signal]]):
                return sorted(
                    groups.items(),
                    key=lambda item: max(sig.score for sig in item[1]),
                    reverse=True,
                )

            def _collect_top_wallets(group: List[Signal], top_n: int = 3) -> List[str]:
                wallet_scores: Dict[str, float] = {}
                for signal in group:
                    for wallet in signal.wallets:
                        addr = wallet.address
                        if not addr:
                            continue
                        wallet_scores[addr] = max(wallet_scores.get(addr, 0.0), signal.score)
                return [addr for addr, _ in sorted(wallet_scores.items(), key=lambda item: item[1], reverse=True)[:top_n]]

            def _render(section_title: str, groups: Dict[tuple[str, str | None], List[Signal]]):
                lines.append(f"## {section_title}")
                lines.append("")
                for (symbol, address), group in _sorted_groups(groups):
                    best_signal = max(group, key=lambda s: s.score)
                    reason_codes = {reason.code for reason in best_signal.reasons}
                    reason_str = ",".join(sorted(reason_codes)) if reason_codes else "-"
                    count_suffix = f"（共 {len(group)} 筆）" if len(group) > 1 else ""
                    addr_display = address or "N/A"
                    chain_display = best_signal.token.chain or "unknown"
                    lines.append(
                        f"- {symbol} ({addr_display}) [chain: {chain_display}] score={best_signal.score:.2f}"
                        f" reasons={reason_str}{count_suffix}"
                    )
                    top_wallets = _collect_top_wallets(group)
                    if top_wallets:
                        lines.append(f"  Top wallets: {', '.join(top_wallets)}")
                    history_entries.append(
                        {
                            "section": section_title,
                            "token_symbol": symbol,
                            "token_address": address,
                            "chain": chain_display,
                            "score": best_signal.score,
                            "reasons": sorted(reason_codes),
                            "count": len(group),
                            "top_wallets": top_wallets,
                            "generated_at": timestamp_text,
                            "generated_at_local": timestamp_local_text,
                        }
                    )
                lines.append("")

            if buy_groups:
                _render("建議買入", buy_groups)
            if sell_groups:
                _render("建議賣出", sell_groups)

        markdown_content = "\n".join(lines)
        report_path.write_text(markdown_content, encoding="utf-8")

        history_dir = report_dir / "history"
        history_dir.mkdir(exist_ok=True)
        history_filename = run_time.strftime("phase1_%Y%m%dT%H%M%SZ")
        (history_dir / f"{history_filename}.md").write_text(markdown_content, encoding="utf-8")

        import json

        (history_dir / f"{history_filename}.json").write_text(
            json.dumps(history_entries, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return report_path, history_entries
