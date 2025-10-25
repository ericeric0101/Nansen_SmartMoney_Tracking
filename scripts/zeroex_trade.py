from __future__ import annotations

import os
from decimal import Decimal, InvalidOperation
from typing import Optional

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
