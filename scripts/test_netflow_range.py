from __future__ import annotations

import json
from typing import Any

from nansen_sm_collector.adapters.nansen_api import NansenAPIClient
from nansen_sm_collector.config.settings import get_settings


def build_payload(chains: list[str]) -> dict[str, Any]:
    return {
        "chains": chains,
        "dateRange": {"from": "1H_AGO", "to": "NOW"},
        "filters": {
            "include_smart_money_labels": ["Fund", "Smart Trader", "30D Smart Trader"],
        },
        "pagination": {"page": 1, "per_page": 10},
        "order_by": [{"field": "net_flow_1h_usd", "direction": "DESC"}],
    }


def main() -> None:
    settings = get_settings()
    client = NansenAPIClient(
        base_url=str(settings.nansen_base_url),
        api_key=settings.nansen_api_key,
    )
    try:
        payload = build_payload(settings.chains)
        response = client.fetch_netflows(payload)
        data = response.get("data", []) if isinstance(response, dict) else response
        print(f"Fetched {len(data)} records for 1H range")
        if data:
            print(json.dumps(data[0], ensure_ascii=False, indent=2))
    finally:
        client.close()


if __name__ == "__main__":
    main()
