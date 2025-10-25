from __future__ import annotations

import logging
from typing import Any, Iterable, List

from ..core.errors import EnrichmentError
from ..core.types import Event
from ..services.wallet_alpha import WalletAlphaService


logger = logging.getLogger(__name__)


class EventEnricher:
    """整合標籤、Alpha 與流動性資訊。"""

    def __init__(
        self,
        client: Any,
        wallet_alpha: WalletAlphaService,
        enable_labels: bool = True,
    ) -> None:
        self._client = client
        self._wallet_alpha = wallet_alpha
        self._enable_labels = enable_labels

    def enrich(self, events: Iterable[Event]) -> List[Event]:
        """補充事件所需資訊。"""

        enriched_events: List[Event] = []
        if self._enable_labels:
            address_chain_pairs = {
                event.wallet.address: event.chain or event.token.chain
                for event in events
                if event.wallet and event.wallet.address
            }
            label_map = self._fetch_labels(address_chain_pairs) if address_chain_pairs else {}
        else:
            label_map = {}

        for event in events:
            wallet = event.wallet
            if wallet and wallet.address in label_map:
                wallet.labels = label_map[wallet.address]
                wallet.alpha_score = self._wallet_alpha.score_wallet(wallet.address)
            elif wallet:
                wallet.alpha_score = self._wallet_alpha.score_wallet(wallet.address)
            enriched_events.append(event)
        return enriched_events

    def _fetch_labels(self, address_chain_pairs: dict[str, str | None]) -> dict[str, list[str]]:
        results: dict[str, list[str]] = {}
        try:
            for address, chain in address_chain_pairs.items():
                if not chain:
                    continue
                response = self._client.fetch_address_labels(chain=chain, address=address)
                records = response.get("data", []) if isinstance(response, dict) else response
                labels = [
                    record.get("label")
                    for record in records
                    if isinstance(record, dict) and record.get("label")
                ]
                results[address] = labels
        except Exception as error:  # noqa: BLE001
            logger.warning("取得錢包標籤失敗", exc_info=error)
            return {}
        return results
