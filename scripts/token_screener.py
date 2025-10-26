#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import requests
from dotenv import load_dotenv


NANSEN_BASE_URL = os.environ.get("NANSEN_BASE_URL", "https://api.nansen.ai")
TOKEN_SCREENER_ENDPOINT = "/api/v1/token-screener"

load_dotenv(dotenv_path=Path(".env"), override=False)


def _parse_chains(raw: Optional[Sequence[str]]) -> List[str]:
    if not raw:
        env_value = os.environ.get("NANSEN_CHAINS", "")
        if env_value:
            return [item.strip() for item in env_value.split(",") if item.strip()]
        return ["ethereum"]
    chains: List[str] = []
    for item in raw:
        chains.extend(sub_item.strip() for sub_item in item.split(",") if sub_item.strip())
    return chains or ["ethereum"]


def _default_date_range(hours: int) -> Tuple[str, str]:
    now = datetime.now(timezone.utc)
    start = now - timedelta(hours=hours)
    return _isoformat(start), _isoformat(now)


def _isoformat(value: datetime) -> str:
    return value.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _build_filters(args: argparse.Namespace) -> Dict[str, Any]:
    filters: Dict[str, Any] = {}
    if args.only_smart_money:
        filters["only_smart_money"] = True
    token_age: Dict[str, Any] = {}
    if args.min_token_age is not None:
        token_age["min"] = args.min_token_age
    if args.max_token_age is not None:
        token_age["max"] = args.max_token_age
    if token_age:
        filters["token_age_days"] = token_age
    return filters


def _build_order_by(order_fields: Sequence[str]) -> List[Dict[str, str]]:
    results: List[Dict[str, str]] = []
    for item in order_fields:
        parts = item.split(":")
        if not parts[0]:
            continue
        direction = parts[1].upper() if len(parts) > 1 else "DESC"
        if direction not in {"ASC", "DESC"}:
            direction = "DESC"
        results.append({"field": parts[0], "direction": direction})
    return results


def fetch_token_screener(args: argparse.Namespace) -> Dict[str, Any]:
    api_key = args.api_key or os.environ.get("NANSEN_API_KEY")
    if not api_key:
        raise RuntimeError("Nansen API key is required. Set --api-key or NANSEN_API_KEY env.")

    chains = _parse_chains(args.chains)
    date_from = args.date_from
    date_to = args.date_to
    if not (date_from and date_to):
        date_from, date_to = _default_date_range(args.lookback_hours)

    payload: Dict[str, Any] = {
        "chains": chains,
        "date": {"from": date_from, "to": date_to},
        "pagination": {"page": args.page, "per_page": args.per_page},
    }

    filters = _build_filters(args)
    if filters:
        payload["filters"] = filters

    order_by = _build_order_by(args.order_by or [])
    if order_by:
        payload["order_by"] = order_by

    url = f"{NANSEN_BASE_URL.rstrip('/')}{TOKEN_SCREENER_ENDPOINT}"
    headers = {
        "apiKey": api_key,
        "Content-Type": "application/json",
    }

    response = requests.post(url, headers=headers, json=payload, timeout=args.timeout)
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:  # pragma: no cover - runtime error path
        error_payload: Dict[str, Any]
        try:
            error_payload = response.json()
        except ValueError:
            error_payload = {"detail": response.text}
        raise RuntimeError(f"Token screener request failed: {exc}, payload={error_payload}") from exc
    return response.json()


def _render_preview(data: Dict[str, Any], limit: int) -> str:
    rows = data.get("data") or []
    preview = rows[:limit]
    if not preview:
        return "No token screener data returned."
    lines = ["Top results:"]
    for item in preview:
        symbol = item.get("token_symbol") or "-"
        chain = item.get("chain") or "-"
        price = item.get("price_usd")
        volume = item.get("volume")
        netflow = item.get("netflow")
        lines.append(
            f"- {symbol} [{chain}] price=${price:.4f} volume={volume:.2f} netflow={netflow:.2f}"
            if isinstance(price, (int, float)) and isinstance(volume, (int, float)) and isinstance(netflow, (int, float))
            else f"- {symbol} [{chain}] data={json.dumps(item, ensure_ascii=False)}"
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch token screener data from Nansen API.")
    parser.add_argument("--api-key", help="Override Nansen API key (otherwise read from NANSEN_API_KEY).")
    parser.add_argument("--chains", nargs="+", help="Chains to query; accepts comma-separated values.")
    parser.add_argument("--date-from", help="ISO8601 start time (e.g. 2025-01-01T00:00:00Z).")
    parser.add_argument("--date-to", help="ISO8601 end time (e.g. 2025-01-02T00:00:00Z).")
    parser.add_argument(
        "--lookback-hours",
        type=int,
        default=24,
        help="When date range not provided, fetch this many trailing hours (default: 24).",
    )
    parser.add_argument("--page", type=int, default=1, help="Pagination page index (default: 1).")
    parser.add_argument("--per-page", type=int, default=25, help="Pagination per page (default: 25).")
    parser.add_argument("--timeout", type=int, default=30, help="HTTP timeout in seconds (default: 30).")
    parser.add_argument("--min-token-age", type=int, help="Minimum token age in days filter.")
    parser.add_argument("--max-token-age", type=int, help="Maximum token age in days filter.")
    parser.add_argument(
        "--only-smart-money",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Filter for smart money activity (default: true).",
    )
    parser.add_argument(
        "--order-by",
        nargs="*",
        help="Sort order instructions like field[:ASC|DESC], e.g. price_change:DESC volume:ASC.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional path to write the full JSON response.",
    )
    parser.add_argument(
        "--preview-limit",
        type=int,
        default=5,
        help="Number of rows to include in stdout preview (default: 5).",
    )

    args = parser.parse_args()
    data = fetch_token_screener(args)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(data.get("pagination", {}), ensure_ascii=False))
    print(_render_preview(data, args.preview_limit))


if __name__ == "__main__":
    main()
