from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence
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
    TokenScreenerRepository,
    TradeCandidateRepository,
    WalletRepository,
)
from ..services.token_market_data import TokenMarketDataService
from ..services.token_overview import TokenOverviewService
from ..services.trade_signal_builder import TradeSignalBuilder
from ..services.trade_simulator import TradeSimulator
from ..services.wallet_alpha import WalletAlphaService
from ..services.telegram_notifier import TelegramNotifier
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
    trade_candidates: dict | None = None


class CollectorPipeline:
    """Phase-1 蒐集與評分流程。"""

    def __init__(self, settings: AppSettings) -> None:
        self._settings = settings
        self._engine = db.create_db_engine(settings.db_url)
        db.Base.metadata.create_all(self._engine)
        db.upgrade_schema(self._engine)
        self._session_factory = db.create_session_factory(self._engine)
        self._logger = logging.getLogger(__name__)
        self._telegram_notifier: TelegramNotifier | None = None
        if (
            self._settings.telegram_notify_enabled
            and self._settings.telegram_bot_token
            and self._settings.telegram_chat_id
        ):
            self._telegram_notifier = TelegramNotifier(
                bot_token=self._settings.telegram_bot_token,
                chat_id=self._settings.telegram_chat_id,
            )

    def run_once(self, use_mock: bool = True) -> PipelineResult:
        """執行單次資料蒐集。"""

        client = self._build_client(use_mock)
        gecko_client: GeckoTerminalClient | None = None
        need_gecko = (
            self._settings.trade_simulation_enabled
            or self._settings.gecko_terminal_market_data_enabled
        )
        if need_gecko and not use_mock:
            gecko_client = GeckoTerminalClient(
                base_url=self._settings.gecko_terminal_base_url,
                version=self._settings.gecko_terminal_version,
            )
        wallet_alpha = WalletAlphaService(session_factory=self._session_factory)
        normalizer = EventNormalizer()
        scorer = SignalScorer(self._settings)
        trade_signal_builder = TradeSignalBuilder()
        timezone = ZoneInfo(self._settings.timezone)
        report_path: Path | None = None
        history_entries: List[dict] = []
        trade_candidates: dict | None = None

        with db.session_scope(self._session_factory) as session:
            token_repo = TokenRepository(session)
            wallet_repo = WalletRepository(session)
            event_repo = EventRepository(session)
            signal_repo = SignalRepository(session)
            run_repo = RunHistoryRepository(session)
            filters = EventFilterSet(self._settings, event_repo)
            screener_repo = TokenScreenerRepository(session)
            trade_candidate_repo = TradeCandidateRepository(session)
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

            events, source_stats, screener_rows = self._fetch_and_normalize(client, normalizer)
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
            raw_events_path: Path | None = None
            if self._settings.dump_phase1_raw_events:
                raw_events_path = self._dump_raw_events(merged_events)
                stats["raw_events_path"] = str(raw_events_path)
            stats["token_screener_snapshots"] = len(screener_rows)
            overview_service = TokenOverviewService()
            token_overview = overview_service.build_overview(merged_events, screener_rows)
            if (
                gecko_client
                and self._settings.gecko_terminal_market_data_enabled
                and token_overview
            ):
                market_data_service = TokenMarketDataService(
                    gecko_client,
                    timeframe=self._settings.gecko_terminal_ohlcv_timeframe,
                    limit=self._settings.gecko_terminal_ohlcv_limit,
                    min_trade_usd=self._settings.gecko_terminal_trade_min_usd,
                    pool_map=self._settings.gecko_terminal_token_pools_map,
                )
                token_overview = market_data_service.enrich(token_overview)
            stats["token_overview_count"] = len(token_overview)
            stats["market_data_pools"] = sum(
                len((entry.get("market") or {}).get("pools") or []) for entry in token_overview
            )
            trade_candidates = trade_signal_builder.build(token_overview)
            stats["trade_candidates_with_smart"] = len(trade_candidates.get("with_smart_money", []))
            stats["trade_candidates_without_smart"] = len(trade_candidates.get("without_smart_money", []))
            flat_candidates = self._flatten_trade_candidates(trade_candidates)
            stats["trade_candidates_total"] = len(flat_candidates)
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
            report_path, history_entries = self._write_report(
                signals,
                run_time,
                run_time_local,
                token_overview=token_overview,
                trade_candidates=trade_candidates,
            )
            run_id = str(uuid4())
            run_model = run_repo.create_run(
                run_id=run_id,
                executed_at=run_time,
                executed_at_local=run_time_local,
                total_signals=len(signals),
                buy_signals=buy_count,
                sell_signals=sell_count,
                stats=stats,
            )
            screener_repo.bulk_insert_snapshots(run_model.id, screener_rows, captured_at=run_time)
            screener_repo.upsert_market_metrics(screener_rows, captured_at=run_time)
            run_repo.bulk_insert_summaries(run_id, history_entries or [])
            overview_path = self._write_token_overview(token_overview, run_time)
            stats["token_overview_path"] = str(overview_path) if overview_path else None
            trade_candidates_path = self._write_trade_candidates(trade_candidates, run_time)
            stats["trade_candidates_path"] = str(trade_candidates_path) if trade_candidates_path else None
            trade_candidate_repo.bulk_insert(run_model.id, flat_candidates)

        if self._telegram_notifier and report_path:
            try:
                message = self._build_telegram_message(run_time, report_path)
                sent = self._telegram_notifier.send_text(message)
                if not sent:
                    caption = f"Phase-1 Summary {run_time.strftime('%Y-%m-%d %H:%M:%S UTC')}"
                    self._telegram_notifier.send_document(report_path, caption=caption)
            except Exception as error:  # noqa: BLE001
                if self._logger.isEnabledFor(logging.WARNING):
                    self._logger.warning("telegram_notify_failed", exc_info=error)

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
            trade_candidates=trade_candidates,
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
    ) -> tuple[List[Event], dict[str, int], List[dict]]:
        stats: dict[str, int] = {}

        dex_events = self._fetch_dex_events(client, normalizer)
        stats["dex_events"] = len(dex_events)

        screener_payload = client.fetch_token_screener(self._build_token_screener_payload())
        screener_events = normalizer.token_screener(screener_payload)
        stats["token_screener_events"] = len(screener_events)
        screener_rows: List[dict] = []
        if isinstance(screener_payload, dict):
            data = screener_payload.get("data")
            if isinstance(data, list):
                screener_rows = [row for row in data if isinstance(row, dict)]

        netflow_payload = client.fetch_netflows(self._build_netflow_payload())
        netflow_events = normalizer.netflows(netflow_payload)
        stats["netflow_events"] = len(netflow_events)

        events: List[Event] = []
        events.extend(dex_events)
        events.extend(screener_events)
        events.extend(netflow_events)
        stats["total_events"] = len(events)
        return events, stats, screener_rows

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
        token_overview: Sequence[dict] | None = None,
        trade_candidates: dict | None = None,
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

        if token_overview:
            lines.append("## 市場熱度對照")
            lines.append("")
            for entry in token_overview:
                market = entry.get("market", {})
                smart = entry.get("smart_money", {})
                lines.append(
                    f"- {entry.get('token_symbol')} ({entry.get('token_address')}) [chain: {entry.get('chain')}] "
                    f"volume={market.get('volume')} netflow={market.get('netflow')} price_change={market.get('price_change')}"
                )
                if smart:
                    lines.append(
                        f"  Smart money trades={smart.get('event_count')} "
                        f"total_usd={smart.get('total_usd_notional')} netflow={smart.get('netflow_summary')}"
                    )
                pools = market.get("pools") or []
                if pools:
                    first_pool = pools[0]
                    trade_stats = first_pool.get("trade_stats") or {}
                    lines.append(
                        f"  Pool {first_pool.get('pool_address')} trades={trade_stats.get('trade_count', 0)} "
                        f"volume_usd={trade_stats.get('total_volume_usd')} max_trade={trade_stats.get('max_trade_volume_usd')}"
                    )
            lines.append("")

        if trade_candidates:
            top_with = trade_candidates.get("with_smart_money") or []
            top_without = trade_candidates.get("without_smart_money") or []
            if top_with or top_without:
                lines.append("## 策略候選清單")
                lines.append("")
                if top_with:
                    lines.append("### 有智慧錢包支持")
                    lines.append("")
                    for item in top_with[:5]:
                        lines.append(
                            f"- {item['token_symbol']} ({item['token_address']}) [chain: {item['chain']}] "
                            f"score={item['composite_score']} market={item.get('market_score')} smart={item.get('smart_money_score')}"
                        )
                    lines.append("")
                if top_without:
                    lines.append("### 無智慧錢包紀錄")
                    lines.append("")
                    for item in top_without[:5]:
                        lines.append(
                            f"- {item['token_symbol']} ({item['token_address']}) [chain: {item['chain']}] "
                            f"score={item['composite_score']} market={item.get('market_score')}"
                        )
                    lines.append("")

        markdown_content = "\n".join(lines)
        report_path.write_text(markdown_content, encoding="utf-8")

        history_dir = report_dir / "history"
        history_dir.mkdir(exist_ok=True)
        history_filename = run_time.strftime("phase1_%Y%m%dT%H%M%SZ")
        (history_dir / f"{history_filename}.md").write_text(markdown_content, encoding="utf-8")

        (history_dir / f"{history_filename}.json").write_text(
            json.dumps(history_entries, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return report_path, history_entries

    def _write_token_overview(self, overview: Sequence[dict], run_time: datetime) -> Path | None:
        if not overview:
            return None
        report_dir = Path("reports")
        report_dir.mkdir(exist_ok=True)
        path = report_dir / "token_overview_latest.json"
        payload = {
            "generated_at": run_time.isoformat(),
            "data": overview,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def _write_trade_candidates(self, candidates: dict | None, run_time: datetime) -> Path | None:
        if not candidates:
            return None
        report_dir = Path("reports")
        report_dir.mkdir(exist_ok=True)
        path = report_dir / "trade_candidates_latest.json"
        payload = {
            "generated_at": run_time.isoformat(),
            "with_smart_money": candidates.get("with_smart_money", []),
            "without_smart_money": candidates.get("without_smart_money", []),
            "all": candidates.get("all", []),
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def _dump_raw_events(self, events: Sequence[Event]) -> Path:
        """將合併後的事件寫入 JSON，方便除錯檢視。"""

        report_dir = Path("reports")
        report_dir.mkdir(exist_ok=True)
        target_path = report_dir / "phase1_raw_events.json"
        data = [event.model_dump(mode="json") for event in events]
        target_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return target_path

    @staticmethod
    def _flatten_trade_candidates(trade_candidates: dict | None) -> List[dict]:
        if not isinstance(trade_candidates, dict):
            return []
        entries: List[dict] = []
        for scope in ("with_smart_money", "without_smart_money", "all"):
            items = trade_candidates.get(scope)
            if not isinstance(items, Sequence):
                continue
            for index, item in enumerate(items, start=1):
                if not isinstance(item, dict):
                    continue
                record = dict(item)
                record["scope"] = scope
                record["rank"] = index
                entries.append(record)
        return entries

    def _build_telegram_message(self, run_time: datetime, report_path: Path) -> str:
        timestamp = run_time.strftime("%Y-%m-%d %H:%M:%S UTC")
        lines = [f"Phase-1 Summary {timestamp}", ""]

        try:
            content = report_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return "\n".join(lines)

        address_pattern = re.compile(r"0x[a-fA-F0-9]{6,}")

        def _format_float(value: str) -> str:
            try:
                number = float(value)
            except ValueError:
                return value
            return f"{number:.2f}"

        def _shorten_line(line: str) -> str:
            lines_out: List[str] = []
            address_line = address_pattern.search(line)

            prefix = line
            address_text: Optional[str] = None
            suffix = ""
            chain_value: Optional[str] = None

            if address_line:
                start, end = address_line.span()
                address_text = address_line.group(0)
                prefix = line[:start].strip()
                suffix = line[end:].strip()
                chain_match = re.search(r"\[chain:\s*([^\]]+)\]", suffix, re.IGNORECASE)
                if chain_match:
                    chain_value = chain_match.group(1).strip()
                    suffix = re.sub(r"\[chain:[^\]]+\]", "", suffix).strip()

            def _format_segment(segment: str) -> str:
                parts = segment.split()
                formatted_parts: List[str] = []
                for part in parts:
                    if "=" in part:
                        key, _, raw_value = part.partition("=")
                        formatted_value = _format_float(raw_value)
                        formatted_parts.append(f"{key}={formatted_value}")
                    else:
                        formatted_parts.append(part)
                return " ".join(formatted_parts).strip()

            if prefix:
                formatted_prefix = _format_segment(prefix)
                if formatted_prefix.endswith("("):
                    formatted_prefix = formatted_prefix[:-1].rstrip()
                lines_out.append(formatted_prefix)

            if address_text:
                if chain_value:
                    link = f"https://dexscreener.com/{chain_value.lower()}/{address_text.lower()}"
                    lines_out.append(link)
                else:
                    lines_out.append(address_text)

            if suffix:
                suffix_formatted = _format_segment(suffix)
                if suffix_formatted:
                    lines_out.append(suffix_formatted)

            if not lines_out:
                return line.strip()
            return "\n".join(filter(None, lines_out))

        for line in content:
            stripped = line.strip()
            if not stripped or stripped.startswith("# Phase-1 Signals Summary"):
                continue
            if stripped.startswith("產出時間") or stripped.startswith("當地時間"):
                continue
            shortened = _shorten_line(stripped)
            lines.append(shortened)

        message = "\n".join(lines).strip()
        return message
