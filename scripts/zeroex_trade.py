from __future__ import annotations

import os
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional, Sequence

import typer
from dotenv import load_dotenv
from sqlalchemy.orm import sessionmaker
from web3 import Web3

try:
    from web3.middleware import geth_poa_middleware as _poa_middleware  # type: ignore[attr-defined]
except ImportError:  # Web3 v6 起改用 ExtraDataToPOAMiddleware
    try:
        from web3.middleware import ExtraDataToPOAMiddleware as _poa_middleware  # type: ignore[attr-defined]
    except ImportError:  # pragma: no cover - 理論上不會發生
        _poa_middleware = None


CHAIN_ID_ALIASES: dict[int, str] = {
    1: "ethereum",
    10: "optimism",
    56: "bsc",
    137: "polygon",
    42161: "arbitrum",
    8453: "base",
}

from nansen_sm_collector.data import schemas
from nansen_sm_collector.data.db import create_db_engine, create_session_factory, upgrade_schema
from nansen_sm_collector.trading import (
    SwapRequest,
    TradeResult,
    ZeroExAPIError,
    ZeroExSwapClient,
    ZeroExTradingService,
)

load_dotenv()

app = typer.Typer(help="使用 0x Swap API 進行模擬或真實交易。")


def _init_db() -> sessionmaker:
    db_url = os.environ.get("DB_URL", "sqlite:///./collector.db")
    engine = create_db_engine(db_url)
    schemas.Base.metadata.create_all(engine)
    upgrade_schema(engine)
    return create_session_factory(engine)


def _get_timezone() -> str:
    return os.environ.get("TZ", "UTC")


def _create_web3(rpc_url: str, chain_id: int) -> Web3:
    provider = Web3.HTTPProvider(rpc_url)
    web3 = Web3(provider)
    if chain_id in {10, 56, 137, 42161, 8453} and _poa_middleware is not None:
        web3.middleware_onion.inject(_poa_middleware, layer=0)
    try:
        remote_chain_id = web3.eth.chain_id
        if remote_chain_id != chain_id:
            typer.echo(
                f"[警告] RPC Chain ID ({remote_chain_id}) 與指定 Chain ID ({chain_id}) 不一致。",
                err=True,
            )
    except Exception:  # noqa: BLE001
        typer.echo("[警告] 無法從 RPC 取得 chain id，繼續執行。", err=True)
    return web3


def _lookup_rpc_url(chain_id: int, explicit: Optional[str]) -> Optional[str]:
    if explicit:
        return explicit

    env_keys = [f"ZEROEX_RPC_URL_{chain_id}"]
    alias = CHAIN_ID_ALIASES.get(chain_id)
    if alias:
        env_keys.append(f"ZEROEX_RPC_URL_{alias.upper()}")

    for key in env_keys:
        value = os.environ.get(key)
        if value:
            return value

    default_url = os.environ.get("ZEROEX_RPC_URL")
    if default_url:
        return default_url
    return None


def _build_swap_request(
    *,
    chain_id: int,
    taker_address: str,
    base_token_symbol: str,
    quote_token_address: str,
    quote_token_symbol: Optional[str],
    direction: str,
    amount: Optional[Decimal],
    amount_wei: Optional[int],
    quote_token_decimals: Optional[int],
    slippage_bps: int,
) -> SwapRequest:
    kwargs: dict = {
        "chain_id": chain_id,
        "taker_address": taker_address,
        "base_token_symbol": base_token_symbol,
        "quote_token_address": quote_token_address,
        "quote_token_symbol": quote_token_symbol,
        "direction": direction,
        "quote_token_decimals": quote_token_decimals,
        "slippage_bps": slippage_bps,
    }
    if amount is not None:
        kwargs["amount"] = amount
    if amount_wei is not None:
        kwargs["amount_wei"] = str(amount_wei)
    return SwapRequest(**kwargs)


def _require_value(value: Optional[str], name: str) -> str:
    if not value:
        typer.echo(f"{name} 必須提供，請使用環境變數或參數設定。", err=True)
        raise typer.Exit(code=1)
    return value


def _resolve_taker_address(taker: Optional[str]) -> str:
    if taker:
        return taker
    env_value = os.environ.get("ZEROEX_TAKER_ADDRESS") or os.environ.get("ZEROEX_WALLET_ADDRESS")
    return _require_value(env_value, "taker address")


def _print_result(result: TradeResult) -> None:
    typer.echo(f"Trade ID: {result.trade_id}")
    typer.echo(f"Mode: {result.mode}")
    typer.echo(f"Status: {result.status}")
    if result.tx_hash:
        typer.echo(f"Tx Hash: {result.tx_hash}")
    if result.error_message:
        typer.echo(f"Error: {result.error_message}", err=True)


def _parse_decimal(value: Optional[str]) -> Optional[Decimal]:
    if value is None:
        return None
    try:
        return Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        typer.echo(f"{value!r} 不是有效的數值格式", err=True)
        raise typer.Exit(code=1) from exc


# --- Trade candidates planning helpers ---------------------------------------------------------

def _load_trade_candidates(path: Path) -> dict:
    if not path.exists():
        typer.echo(f"找不到 trade candidates 檔案：{path}", err=True)
        raise typer.Exit(code=1)
    try:
        content = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        typer.echo(f"解析 trade candidates 檔案失敗：{exc}", err=True)
        raise typer.Exit(code=1) from exc
    if not isinstance(content, dict):
        typer.echo("trade candidates 檔案格式不正確（須為 JSON 物件）。", err=True)
        raise typer.Exit(code=1)
    return content


def _normalize_candidates(data: dict, include_without: bool, top_n: int) -> list[dict]:
    with_smart = data.get("with_smart_money") or []
    without_smart = data.get("without_smart_money") or []
    overall = data.get("all")

    if not overall:
        combined: list[dict] = []
        combined.extend(with_smart[:top_n])
        if include_without:
            combined.extend(without_smart[:top_n])
    else:
        combined = []
        for item in overall:
            if not isinstance(item, dict):
                continue
            if item.get("has_smart_money"):
                if len([c for c in combined if c.get("has_smart_money")]) >= top_n:
                    continue
            else:
                if not include_without:
                    continue
                if len([c for c in combined if not c.get("has_smart_money")]) >= top_n:
                    continue
            combined.append(item)

        if not combined:
            combined.extend(with_smart[:top_n])
            if include_without:
                combined.extend(without_smart[:top_n])

    seen: set[tuple[str, str | None]] = set()
    normalized: list[dict] = []
    for item in combined:
        if not isinstance(item, dict):
            continue
        token_symbol = item.get("token_symbol")
        token_address = item.get("token_address")
        chain = item.get("chain")
        if not token_symbol or not chain:
            continue
        key = (chain.lower(), (token_address or "").lower() or None)
        if key in seen:
            continue
        seen.add(key)
        normalized.append(item)
    return normalized


def _to_decimal(value: Optional[float | str]) -> Decimal:
    if value is None:
        return Decimal("0")
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        typer.echo(f"無法轉換為數值：{value!r}", err=True)
        raise typer.Exit(code=1) from exc


def _meets_thresholds(
    item: dict,
    *,
    min_composite: Decimal,
    min_market: Decimal,
    min_smart: Decimal,
    min_liquidity: Decimal,
) -> bool:
    composite = _to_decimal(item.get("composite_score"))
    market = _to_decimal(item.get("market_score"))
    if composite < min_composite:
        return False
    if market < min_market:
        return False
    market_info = item.get("market") or {}
    liquidity = _to_decimal(market_info.get("liquidity"))
    if liquidity and liquidity < min_liquidity:
        return False
    if item.get("has_smart_money"):
        smart = _to_decimal(item.get("smart_money_score"))
        if smart < min_smart:
            return False
    return True


def _compute_final_score(
    item: dict,
    *,
    weight_composite: Decimal,
    weight_smart: Decimal,
    weight_market: Decimal,
    discount_without_smart: Decimal,
) -> Decimal:
    composite = _to_decimal(item.get("composite_score"))
    smart = _to_decimal(item.get("smart_money_score"))
    market = _to_decimal(item.get("market_score"))
    final_score = (
        composite * weight_composite
        + smart * weight_smart
        + market * weight_market
    )
    if not item.get("has_smart_money"):
        final_score *= discount_without_smart
    return final_score


def _distribute_capital(
    candidates: Sequence[dict],
    *,
    final_scores: Sequence[Decimal],
    capital: Decimal,
    min_notional: Decimal,
    max_notional: Optional[Decimal],
    max_per_position_pct: Decimal,
) -> list[dict]:
    if not candidates:
        return []
    total_score = sum(final_scores)
    if total_score <= 0:
        return []

    max_position_amount = capital * max_per_position_pct
    if max_notional is not None and max_notional > 0:
        max_position_amount = min(max_position_amount, max_notional)

    remaining = capital
    plans: list[dict] = []

    for item, score in zip(candidates, final_scores):
        if remaining < min_notional:
            break
        share = score / total_score if total_score > 0 else Decimal("0")
        allocation = capital * share
        allocation = min(allocation, max_position_amount)
        if max_notional is not None and max_notional > 0:
            allocation = min(allocation, max_notional)
        if allocation < min_notional:
            continue
        if allocation > remaining:
            allocation = remaining
        remaining -= allocation
        plans.append(
            {
                "token_symbol": item.get("token_symbol"),
                "token_address": item.get("token_address"),
                "chain": item.get("chain"),
                "allocation": allocation,
                "final_score": score,
                "composite_score": _to_decimal(item.get("composite_score")),
                "smart_money_score": _to_decimal(item.get("smart_money_score")),
                "market_score": _to_decimal(item.get("market_score")),
                "has_smart_money": bool(item.get("has_smart_money")),
                "market": item.get("market"),
                "smart_money": item.get("smart_money"),
                "source": "with_smart_money" if item.get("has_smart_money") else "without_smart_money",
            }
        )
    return plans


@app.command()
def simulate(
    chain_id: int = typer.Option(..., help="鏈 ID（例如 Ethereum=1, Base=8453）"),
    base_token: str = typer.Option(..., help="Base token symbol，只允許 USDC、WETH 或 WBNB (視鏈而定)"),
    quote_token_address: str = typer.Option(..., help="欲交易代幣的合約地址"),
    quote_token_symbol: Optional[str] = typer.Option(None, help="代幣 symbol（可選）"),
    quote_token_decimals: Optional[int] = typer.Option(None, help="代幣小數位數（可選）"),
    taker_address: Optional[str] = typer.Option(None, help="執行交易的錢包地址"),
    direction: str = typer.Option(
        "BASE_TO_QUOTE",
        help="BASE_TO_QUOTE: 用 base token 買 quote token；QUOTE_TO_BASE: 賣 quote token 換 base token",
    ),
    amount: Optional[str] = typer.Option(
        None, help="賣出的 token 數量（十進位表示），不可與 amount-wei 同時使用"
    ),
    amount_wei: Optional[int] = typer.Option(
        None, help="賣出的 token 數量（整數最小單位），不可與 amount 同時使用"
    ),
    slippage_bps: int = typer.Option(100, help="允許滑價，單位 Bps (1% = 100)"),
    rpc_url: Optional[str] = typer.Option(
        None, help="指定 RPC URL（若提供則可自動查詢 quote token decimals）"
    ),
) -> None:
    """測試 0x Swap API 是否可正常取得報價。"""

    api_key = _require_value(os.environ.get("ZEROEX_API_KEY"), "ZEROEX_API_KEY")
    taker = _resolve_taker_address(taker_address)
    session_factory = _init_db()

    amount_decimal = _parse_decimal(amount)

    request = _build_swap_request(
        chain_id=chain_id,
        taker_address=taker,
        base_token_symbol=base_token,
        quote_token_address=quote_token_address,
        quote_token_symbol=quote_token_symbol,
        direction=direction,
        amount=amount_decimal,
        amount_wei=amount_wei,
        quote_token_decimals=quote_token_decimals,
        slippage_bps=slippage_bps,
    )

    rpc_endpoint = _lookup_rpc_url(chain_id, rpc_url)
    web3 = _create_web3(rpc_endpoint, chain_id) if rpc_endpoint else None

    client = ZeroExSwapClient(api_key)
    service = ZeroExTradingService(
        client,
        session_factory,
        timezone=_get_timezone(),
        web3=web3,
    )

    try:
        result = service.simulate_swap(request)
    except ZeroExAPIError as exc:
        typer.echo(f"0x API 呼叫失敗: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    finally:
        client.close()

    _print_result(result)


PLAN_ENV_PREFIX = "TRADE_PLAN"


def _env_option(name: str, default: Optional[str] = None) -> Optional[str]:
    env_var = f"{PLAN_ENV_PREFIX}_{name}"
    return os.environ.get(env_var, default)


@app.command("plan-from-candidates")
def plan_from_candidates(
    capital: str = typer.Option(
        ..., help="可動用的總資金（例如 50000，單位 USD）",
        envvar=f"{PLAN_ENV_PREFIX}_CAPITAL",
    ),
    candidates_path: Path = typer.Option(
        Path("reports/trade_candidates_latest.json"),
        help="trade_candidates_latest.json 的路徑",
        envvar=f"{PLAN_ENV_PREFIX}_CANDIDATES_PATH",
    ),
    include_without_smart: bool = typer.Option(
        True,
        help="是否納入無智慧錢包的候選",
        envvar=f"{PLAN_ENV_PREFIX}_INCLUDE_WITHOUT_SMART",
    ),
    top_n: int = typer.Option(
        5,
        help="每一組最多考慮的候選數量",
        envvar=f"{PLAN_ENV_PREFIX}_TOP_N",
    ),
    min_composite: float = typer.Option(
        0.6,
        help="最低 composite_score",
        envvar=f"{PLAN_ENV_PREFIX}_MIN_COMPOSITE",
    ),
    min_market: float = typer.Option(
        0.5,
        help="最低 market_score",
        envvar=f"{PLAN_ENV_PREFIX}_MIN_MARKET",
    ),
    min_smart: float = typer.Option(
        0.5,
        help="最低 smart_money_score（只對有智慧錢包的標的生效）",
        envvar=f"{PLAN_ENV_PREFIX}_MIN_SMART",
    ),
    min_liquidity: float = typer.Option(
        200_000.0,
        help="最低流動性（USD）",
        envvar=f"{PLAN_ENV_PREFIX}_MIN_LIQUIDITY",
    ),
    min_notional: float = typer.Option(
        2_000.0,
        help="單筆最小下單金額（USD）",
        envvar=f"{PLAN_ENV_PREFIX}_MIN_NOTIONAL",
    ),
    max_notional: Optional[float] = typer.Option(
        10_000.0,
        help="單筆最大下單金額（USD），設為 0 表示不限",
        envvar=f"{PLAN_ENV_PREFIX}_MAX_NOTIONAL",
    ),
    max_per_position_pct: float = typer.Option(
        0.2,
        help="單一標的最大資金佔比（0-1 之間）",
        envvar=f"{PLAN_ENV_PREFIX}_MAX_PER_POSITION_PCT",
    ),
    discount_without_smart: float = typer.Option(
        0.6,
        help="無智慧錢包時的分數折扣（0-1 之間，用來降低配置權重）",
        envvar=f"{PLAN_ENV_PREFIX}_DISCOUNT_WITHOUT_SMART",
    ),
    output: Optional[Path] = typer.Option(
        None,
        help="若指定，輸出規劃結果成 JSON 檔案",
        envvar=f"{PLAN_ENV_PREFIX}_OUTPUT",
    ),
) -> None:
    """根據 trade_candidates 計算下單建議（僅輸出規劃，不會實際下單）。"""

    candidates_data = _load_trade_candidates(candidates_path)
    selected = _normalize_candidates(candidates_data, include_without_smart, top_n)
    if not selected:
        typer.echo("沒有找到可用的候選標的。", err=True)
        raise typer.Exit(code=1)

    capital_amount = _to_decimal(capital)
    if capital_amount <= 0:
        typer.echo("capital 需為正數。", err=True)
        raise typer.Exit(code=1)

    min_composite_dec = _to_decimal(min_composite)
    min_market_dec = _to_decimal(min_market)
    min_smart_dec = _to_decimal(min_smart)
    min_liquidity_dec = _to_decimal(min_liquidity)
    min_notional_dec = _to_decimal(min_notional)
    max_notional_dec = _to_decimal(max_notional) if (max_notional and max_notional > 0) else None
    max_pct_dec = _to_decimal(max_per_position_pct)
    discount_dec = _to_decimal(discount_without_smart)

    filtered: list[dict] = []
    final_scores: list[Decimal] = []
    for item in selected:
        if not _meets_thresholds(
            item,
            min_composite=min_composite_dec,
            min_market=min_market_dec,
            min_smart=min_smart_dec,
            min_liquidity=min_liquidity_dec,
        ):
            continue
        score = _compute_final_score(
            item,
            weight_composite=Decimal("0.5"),
            weight_smart=Decimal("0.3"),
            weight_market=Decimal("0.2"),
            discount_without_smart=discount_dec,
        )
        if score <= 0:
            continue
        filtered.append(item)
        final_scores.append(score)

    if not filtered:
        typer.echo("候選標的皆未達自訂門檻，請調整條件後再試。", err=True)
        raise typer.Exit(code=1)

    plans = _distribute_capital(
        filtered,
        final_scores=final_scores,
        capital=capital_amount,
        min_notional=min_notional_dec,
        max_notional=max_notional_dec,
        max_per_position_pct=max_pct_dec,
    )

    if not plans:
        typer.echo("配置後沒有任何標的符合資金與門檻限制。", err=True)
        raise typer.Exit(code=1)

    total_alloc = sum(plan["allocation"] for plan in plans)

    typer.echo("=== Trade Plan Preview ===")
    typer.echo(f"可動用資金: {capital_amount:.2f} USD")
    typer.echo(f"預計使用: {total_alloc:.2f} USD ({(total_alloc / capital_amount * 100):.2f}%)")
    typer.echo("")

    for idx, plan in enumerate(plans, start=1):
        allocation = plan["allocation"]
        has_smart = "Yes" if plan["has_smart_money"] else "No"
        typer.echo(
            f"{idx}. {plan['token_symbol']} ({plan['token_address']})"
            f" [chain: {plan['chain']}] allocation={allocation:.2f} "
            f"final_score={plan['final_score']:.4f} composite={plan['composite_score']:.4f} "
            f"market={plan['market_score']:.4f} smart={plan['smart_money_score']:.4f} "
            f"smart_money={has_smart}"
        )

    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        serializable = [
            {
                **plan,
                "allocation": float(plan["allocation"]),
                "final_score": float(plan["final_score"]),
                "composite_score": float(plan["composite_score"]),
                "smart_money_score": float(plan["smart_money_score"]),
                "market_score": float(plan["market_score"]),
            }
            for plan in plans
        ]
        payload = {
            "capital": float(capital_amount),
            "allocated": float(total_alloc),
            "remaining": float(capital_amount - total_alloc),
            "plans": serializable,
        }
        output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        typer.echo(f"已輸出規劃結果至 {output}")


@app.command()
def trade(
    chain_id: int = typer.Option(..., help="鏈 ID（例如 Ethereum=1, Base=8453）"),
    base_token: str = typer.Option(..., help="Base token symbol，只允許 USDC、WETH 或 WBNB (視鏈而定)"),
    quote_token_address: str = typer.Option(..., help="欲交易代幣的合約地址"),
    quote_token_symbol: Optional[str] = typer.Option(None, help="代幣 symbol（可選）"),
    quote_token_decimals: Optional[int] = typer.Option(None, help="代幣小數位數（可選）"),
    taker_address: Optional[str] = typer.Option(None, help="執行交易的錢包地址"),
    direction: str = typer.Option(
        "BASE_TO_QUOTE",
        help="BASE_TO_QUOTE: 用 base token 買 quote token；QUOTE_TO_BASE: 賣 quote token 換 base token",
    ),
    amount: Optional[str] = typer.Option(
        None, help="賣出的 token 數量（十進位表示），不可與 amount-wei 同時使用"
    ),
    amount_wei: Optional[int] = typer.Option(
        None, help="賣出的 token 數量（整數最小單位），不可與 amount 同時使用"
    ),
    slippage_bps: int = typer.Option(100, help="允許滑價，單位 Bps (1% = 100)"),
    wait_for_receipt: bool = typer.Option(
        True, help="是否等待交易上鏈結果"
    ),
    receipt_timeout: int = typer.Option(600, help="等待交易上鏈的逾時秒數"),
    rpc_url: Optional[str] = typer.Option(None, help="指定 RPC URL（若未提供則依環境變數自動選擇）"),
) -> None:
    """實際送出 0x Swap 交易。"""

    api_key = _require_value(os.environ.get("ZEROEX_API_KEY"), "ZEROEX_API_KEY")
    rpc_endpoint = _lookup_rpc_url(chain_id, rpc_url)
    if rpc_endpoint is None:
        env_keys = [f"ZEROEX_RPC_URL_{chain_id}"]
        alias = CHAIN_ID_ALIASES.get(chain_id)
        if alias:
            env_keys.append(f"ZEROEX_RPC_URL_{alias.upper()}")
        env_keys.append("ZEROEX_RPC_URL")
        typer.echo(
            f"找不到 Chain ID {chain_id} 對應的 RPC URL，請設定 "
            f"{' 或 '.join(env_keys)}，或在指令加入 --rpc-url。",
            err=True,
        )
        raise typer.Exit(code=1)
    private_key = _require_value(os.environ.get("ZEROEX_PRIVATE_KEY"), "ZEROEX_PRIVATE_KEY")
    taker = _resolve_taker_address(taker_address)

    session_factory = _init_db()
    web3 = _create_web3(rpc_endpoint, chain_id)

    amount_decimal = _parse_decimal(amount)

    request = _build_swap_request(
        chain_id=chain_id,
        taker_address=taker,
        base_token_symbol=base_token,
        quote_token_address=quote_token_address,
        quote_token_symbol=quote_token_symbol,
        direction=direction,
        amount=amount_decimal,
        amount_wei=amount_wei,
        quote_token_decimals=quote_token_decimals,
        slippage_bps=slippage_bps,
    )

    client = ZeroExSwapClient(api_key)
    service = ZeroExTradingService(
        client,
        session_factory,
        timezone=_get_timezone(),
        web3=web3,
    )

    try:
        result = service.execute_live_swap(
            request,
            private_key=private_key,
            wait_for_receipt=wait_for_receipt,
            receipt_timeout=receipt_timeout,
        )
    except ZeroExAPIError as exc:
        typer.echo(f"0x API 呼叫失敗: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    finally:
        client.close()

    _print_result(result)


if __name__ == "__main__":
    app()
