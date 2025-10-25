from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from typing import Any, Dict, Literal, Optional
from zoneinfo import ZoneInfo

import httpx
from httpx import Response
from sqlalchemy.orm import sessionmaker
from web3 import Web3
from web3.contract import Contract
from web3.exceptions import ContractLogicError

from ..core.utils import utc_now
from ..data.db import session_scope
from ..data.repos import ExecutedTradeRepository

TradeMode = Literal["SIMULATION", "LIVE"]
TradeDirection = Literal["BASE_TO_QUOTE", "QUOTE_TO_BASE"]


@dataclass(frozen=True, slots=True)
class TokenInfo:
    """基本代幣資訊。"""

    symbol: str
    address: str
    decimals: int


# 主要鏈的 USDC / WETH 對應資料。可依需求擴充。
BASE_TOKEN_REGISTRY: dict[int, dict[str, TokenInfo]] = {
    1: {
        "USDC": TokenInfo("USDC", "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eb48", 6),
        "WETH": TokenInfo("WETH", "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2", 18),
    },
    10: {
        "USDC": TokenInfo("USDC", "0x7F5c764cBc14f9669B88837ca1490cCa17c31607", 6),
        "WETH": TokenInfo("WETH", "0x4200000000000000000000000000000000000006", 18),
    },
    56: {
        "USDC": TokenInfo("USDC", "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d", 18),
        "WBNB": TokenInfo("WBNB", "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c", 18),
    },
    137: {
        "USDC": TokenInfo("USDC", "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174", 6),
        "WETH": TokenInfo("WETH", "0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619", 18),
    },
    42161: {
        "USDC": TokenInfo("USDC", "0xaf88d065e77c8cc2239327c5edb3a432268e5831", 6),
        "WETH": TokenInfo("WETH", "0x82af49447d8a07e3bd95bd0d56f35241523fbab1", 18),
    },
    8453: {
        "USDC": TokenInfo("USDC", "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", 6),
        "WETH": TokenInfo("WETH", "0x4200000000000000000000000000000000000006", 18),
    },
}


ERC20_ABI: list[dict[str, Any]] = [
    {
        "constant": True,
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [],
        "name": "symbol",
        "outputs": [{"name": "", "type": "string"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [{"name": "owner", "type": "address"}, {"name": "spender", "type": "address"}],
        "name": "allowance",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "constant": False,
        "inputs": [{"name": "spender", "type": "address"}, {"name": "value", "type": "uint256"}],
        "name": "approve",
        "outputs": [{"name": "", "type": "bool"}],
        "stateMutability": "nonpayable",
        "type": "function",
    },
]


INTEGRATOR_FEE_RATE = Decimal("0.0015")


class ZeroExAPIError(RuntimeError):
    """0x API 呼叫失敗。"""


class Web3NotConfiguredError(RuntimeError):
    """需要 Web3 提供者時未設定。"""


@dataclass(slots=True)
class SwapRequest:
    """描述一次 Swap 需求。"""

    chain_id: int
    taker_address: str
    base_token_symbol: str
    quote_token_address: str
    quote_token_symbol: Optional[str] = None
    direction: TradeDirection = "BASE_TO_QUOTE"
    amount: Optional[Decimal | str | float] = None
    amount_wei: Optional[str | int] = None
    quote_token_decimals: Optional[int] = None
    slippage_bps: int = 100

    def __post_init__(self) -> None:
        if self.amount is None and self.amount_wei is None:
            raise ValueError("SwapRequest 必須提供 amount 或 amount_wei")
        if self.amount is not None and self.amount_wei is not None:
            raise ValueError("SwapRequest 不可同時提供 amount 與 amount_wei")
        self.direction = self.direction.upper()  # type: ignore[assignment]
        if self.direction not in {"BASE_TO_QUOTE", "QUOTE_TO_BASE"}:
            raise ValueError("direction 需為 BASE_TO_QUOTE 或 QUOTE_TO_BASE")
        self.base_token_symbol = self.base_token_symbol.upper()
        if self.quote_token_symbol is not None:
            self.quote_token_symbol = self.quote_token_symbol.upper()


@dataclass(slots=True)
class SwapContext:
    """內部使用的 Swap 執行資訊。"""

    base_token: TokenInfo
    quote_token_symbol: str
    quote_token_address: str
    quote_token_decimals: Optional[int]
    sell_token_address: str
    sell_token_decimals: int
    buy_token_address: str
    buy_token_decimals: Optional[int]
    sell_amount: str
    sell_amount_decimal: Optional[Decimal]
    direction: TradeDirection


@dataclass(slots=True)
class TradeResult:
    """回傳最終交易結果。"""

    trade_id: int
    mode: TradeMode
    status: str
    tx_hash: Optional[str]
    price_response: Optional[dict]
    quote_response: Optional[dict]
    error_message: Optional[str] = None


class ZeroExSwapClient:
    """簡化 0x Swap API 的 HTTP 呼叫。"""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://api.0x.org",
        version: str = "v2",
        timeout: float = 20.0,
        client: Optional[httpx.Client] = None,
    ) -> None:
        self._api_key = api_key
        self._version = version
        self._client = client or httpx.Client(base_url=base_url, timeout=timeout)
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "ZeroExSwapClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def get_price(
        self,
        *,
        chain_id: int,
        sell_token: str,
        buy_token: str,
        sell_amount: str,
        taker: str,
        slippage_bps: Optional[int] = None,
    ) -> dict:
        params = {
            "chainId": str(chain_id),
            "sellToken": sell_token,
            "buyToken": buy_token,
            "sellAmount": sell_amount,
            "taker": taker,
        }
        if slippage_bps is not None:
            params["slippageBps"] = str(slippage_bps)
        return self._request("GET", "/swap/allowance-holder/price", params=params)

    def get_quote(
        self,
        *,
        chain_id: int,
        sell_token: str,
        buy_token: str,
        sell_amount: str,
        taker: str,
        slippage_bps: Optional[int] = None,
    ) -> dict:
        params = {
            "chainId": str(chain_id),
            "sellToken": sell_token,
            "buyToken": buy_token,
            "sellAmount": sell_amount,
            "taker": taker,
        }
        if slippage_bps is not None:
            params["slippageBps"] = str(slippage_bps)
        return self._request("GET", "/swap/allowance-holder/quote", params=params)

    def _request(self, method: str, path: str, *, params: Optional[dict] = None) -> dict:
        headers = {
            "0x-api-key": self._api_key,
            "0x-version": self._version,
        }
        try:
            response: Response = self._client.request(method, path, params=params, headers=headers)
        except httpx.HTTPError as exc:  # pragma: no cover - 網路錯誤
            raise ZeroExAPIError(f"0x API request failed: {exc}") from exc

        if response.status_code >= 400:
            message = response.text
            try:
                payload = response.json()
                message = payload.get("message", message)
            except ValueError:
                pass
            raise ZeroExAPIError(
                f"0x API returned {response.status_code}: {message}",
            )
        return response.json()


class ZeroExTradingService:
    """封裝 0x Swap 交易流程（模擬與實際）。"""

    def __init__(
        self,
        swap_client: ZeroExSwapClient,
        session_factory: sessionmaker,
        *,
        timezone: str = "UTC",
        web3: Optional[Web3] = None,
    ) -> None:
        self._swap_client = swap_client
        self._session_factory = session_factory
        self._tz = ZoneInfo(timezone)
        self._web3 = web3

    def simulate_swap(self, request: SwapRequest) -> TradeResult:
        """執行虛擬交易，僅取得價格與路徑資訊。"""

        context = self._prepare_context(request)
        price_response = self._swap_client.get_price(
            chain_id=request.chain_id,
            sell_token=context.sell_token_address,
            buy_token=context.buy_token_address,
            sell_amount=context.sell_amount,
            taker=request.taker_address,
            slippage_bps=request.slippage_bps,
        )
        timestamps = self._current_timestamps()

        buy_amount_str = price_response.get("buyAmount")
        buy_amount_decimal = self._calculate_decimal(buy_amount_str, context.buy_token_decimals)
        price_value = self._calculate_price(
            sell_amount=context.sell_amount,
            sell_decimals=context.sell_token_decimals,
            buy_amount=buy_amount_str,
            buy_decimals=context.buy_token_decimals,
        )

        (
            buy_amount_str,
            buy_amount_decimal,
            fee_usdc,
        ) = self._apply_integrator_fee(
            request=request,
            context=context,
            buy_amount_str=buy_amount_str,
            buy_amount_decimal=buy_amount_decimal,
        )

        with session_scope(self._session_factory) as session:
            repo = ExecutedTradeRepository(session)
            record = repo.create_record(
                mode="SIMULATION",
                status="COMPLETED",
                side=context.direction,
                chain_id=request.chain_id,
                base_token_symbol=context.base_token.symbol,
                base_token_address=context.base_token.address.lower(),
                quote_token_symbol=context.quote_token_symbol,
                quote_token_address=context.quote_token_address.lower(),
                sell_token_address=context.sell_token_address.lower(),
                buy_token_address=context.buy_token_address.lower(),
                sell_amount=context.sell_amount,
                sell_amount_decimal=self._decimal_to_float(context.sell_amount_decimal),
                buy_amount=buy_amount_str,
                buy_amount_decimal=self._decimal_to_float(buy_amount_decimal),
                price=price_value,
                slippage_bps=request.slippage_bps,
                integrator_fee_usdc=self._decimal_to_float(fee_usdc),
                allowance_target=self._extract_allowance_target(price_response),
                quote_id=price_response.get("zid"),
                tx_hash=None,
                error_message=None,
                price_response=price_response,
                quote_response=None,
                transaction_payload=None,
                executed_at=timestamps["utc"],
                executed_at_local=timestamps["local"],
            )
            trade_id = record.id

        return TradeResult(
            trade_id=trade_id,
            mode="SIMULATION",
            status="COMPLETED",
            tx_hash=None,
            price_response=price_response,
            quote_response=None,
        )

    def execute_live_swap(
        self,
        request: SwapRequest,
        *,
        private_key: str,
        wait_for_receipt: bool = True,
        receipt_timeout: int = 600,
    ) -> TradeResult:
        """真正送出交易到鏈上，並記錄結果。"""

        if self._web3 is None:
            raise Web3NotConfiguredError("執行實際交易需要設定 Web3 提供者")

        context = self._prepare_context(request)
        price_response = self._swap_client.get_price(
            chain_id=request.chain_id,
            sell_token=context.sell_token_address,
            buy_token=context.buy_token_address,
            sell_amount=context.sell_amount,
            taker=request.taker_address,
            slippage_bps=request.slippage_bps,
        )

        timestamps_initial = self._current_timestamps()
        price_buy_amount_str = price_response.get("buyAmount")
        price_buy_amount_decimal = self._calculate_decimal(price_buy_amount_str, context.buy_token_decimals)
        price_value = self._calculate_price(
            sell_amount=context.sell_amount,
            sell_decimals=context.sell_token_decimals,
            buy_amount=price_buy_amount_str,
            buy_decimals=context.buy_token_decimals,
        )

        (
            price_buy_amount_str,
            price_buy_amount_decimal,
            fee_usdc,
        ) = self._apply_integrator_fee(
            request=request,
            context=context,
            buy_amount_str=price_buy_amount_str,
            buy_amount_decimal=price_buy_amount_decimal,
        )

        with session_scope(self._session_factory) as session:
            repo = ExecutedTradeRepository(session)
            record = repo.create_record(
                mode="LIVE",
                status="PENDING",
                side=context.direction,
                chain_id=request.chain_id,
                base_token_symbol=context.base_token.symbol,
                base_token_address=context.base_token.address.lower(),
                quote_token_symbol=context.quote_token_symbol,
                quote_token_address=context.quote_token_address.lower(),
                sell_token_address=context.sell_token_address.lower(),
                buy_token_address=context.buy_token_address.lower(),
                sell_amount=context.sell_amount,
                sell_amount_decimal=self._decimal_to_float(context.sell_amount_decimal),
                buy_amount=price_buy_amount_str,
                buy_amount_decimal=self._decimal_to_float(price_buy_amount_decimal),
                price=price_value,
                slippage_bps=request.slippage_bps,
                integrator_fee_usdc=self._decimal_to_float(fee_usdc),
                allowance_target=self._extract_allowance_target(price_response),
                quote_id=price_response.get("zid"),
                tx_hash=None,
                error_message=None,
                price_response=price_response,
                quote_response=None,
                transaction_payload=None,
                executed_at=timestamps_initial["utc"],
                executed_at_local=timestamps_initial["local"],
            )
            trade_id = record.id

        allowance_tx_hash: Optional[str] = None
        quote_response: Optional[dict] = None
        tx_hash_hex: Optional[str] = None
        error_message: Optional[str] = None
        final_status: str = "PENDING"
        buy_amount_str: Optional[str] = price_buy_amount_str
        buy_amount_decimal = price_buy_amount_decimal
        price_final = price_value
        allowance_target = self._extract_allowance_target(price_response)
        transaction_payload: Dict[str, Any] | None = None
        fee_usdc: Optional[Decimal] = fee_usdc

        try:
            if allowance_target is not None:
                allowance_tx_hash = self._ensure_allowance(
                    owner=request.taker_address,
                    token=context.sell_token_address,
                    spender=allowance_target,
                    amount=int(context.sell_amount),
                    chain_id=request.chain_id,
                    private_key=private_key,
                )

            quote_response = self._swap_client.get_quote(
                chain_id=request.chain_id,
                sell_token=context.sell_token_address,
                buy_token=context.buy_token_address,
                sell_amount=context.sell_amount,
                taker=request.taker_address,
                slippage_bps=request.slippage_bps,
            )
            transaction_payload = {"quote_transaction": quote_response.get("transaction")}
            if allowance_tx_hash:
                transaction_payload["allowance_tx_hash"] = allowance_tx_hash

            buy_amount_str = quote_response.get("buyAmount", buy_amount_str)
            buy_amount_decimal = self._calculate_decimal(buy_amount_str, context.buy_token_decimals)
            price_final = self._calculate_price(
                sell_amount=context.sell_amount,
                sell_decimals=context.sell_token_decimals,
                buy_amount=buy_amount_str,
                buy_decimals=context.buy_token_decimals,
            )

            (
                buy_amount_str,
                buy_amount_decimal,
                fee_usdc_quote,
            ) = self._apply_integrator_fee(
                request=request,
                context=context,
                buy_amount_str=buy_amount_str,
                buy_amount_decimal=buy_amount_decimal,
            )
            if fee_usdc_quote is not None:
                fee_usdc = fee_usdc_quote

            tx_params = self._prepare_transaction_params(
                quote_response.get("transaction", {}),
                chain_id=request.chain_id,
                from_address=request.taker_address,
            )
            tx_hash_hex = self._send_transaction(tx_params, private_key=private_key)
            final_status = "SUBMITTED"

            if wait_for_receipt:
                receipt = self._web3.eth.wait_for_transaction_receipt(tx_hash_hex, timeout=receipt_timeout)
                receipt_info = {
                    "blockNumber": receipt.blockNumber,
                    "status": receipt.status,
                }
                if transaction_payload is not None:
                    transaction_payload["receipt"] = receipt_info
                if receipt.status == 1:
                    final_status = "COMPLETED"
                else:
                    final_status = "FAILED"
                    error_message = "Transaction reverted on-chain"

        except Exception as exc:  # noqa: BLE001
            error_message = str(exc)
            if final_status != "FAILED":
                final_status = "FAILED"

        finally:
            timestamps_final = self._current_timestamps()
            with session_scope(self._session_factory) as session:
                repo = ExecutedTradeRepository(session)
                trade = repo.get_by_id(trade_id)
                if trade is not None:
                    trade.buy_amount = buy_amount_str
                    trade.buy_amount_decimal = self._decimal_to_float(buy_amount_decimal)
                    trade.price = price_final
                    trade.quote_id = (quote_response or {}).get("zid", trade.quote_id)
                    trade.allowance_target = allowance_target or trade.allowance_target
                    trade.transaction_payload = transaction_payload
                    repo.update_status(
                        trade,
                        status=final_status,
                        tx_hash=tx_hash_hex,
                        error_message=error_message,
                        executed_at=timestamps_final["utc"],
                        executed_at_local=timestamps_final["local"],
                        quote_response=quote_response,
                        integrator_fee_usdc=self._decimal_to_float(fee_usdc),
                    )

        return TradeResult(
            trade_id=trade_id,
            mode="LIVE",
            status=final_status,
            tx_hash=tx_hash_hex,
            price_response=price_response,
            quote_response=quote_response,
            error_message=error_message,
        )

    # --- Helpers ---------------------------------------------------------

    def _apply_integrator_fee(
        self,
        *,
        request: SwapRequest,
        context: SwapContext,
        buy_amount_str: Optional[str],
        buy_amount_decimal: Optional[Decimal],
    ) -> tuple[Optional[str], Optional[Decimal], Optional[Decimal]]:
        if buy_amount_str is None or buy_amount_decimal is None:
            return buy_amount_str, buy_amount_decimal, None
        if buy_amount_decimal == 0:
            return buy_amount_str, buy_amount_decimal, None

        sell_amount_decimal = context.sell_amount_decimal
        if sell_amount_decimal is None:
            sell_amount_decimal = Decimal(context.sell_amount) / (Decimal(10) ** context.sell_token_decimals)
            context.sell_amount_decimal = sell_amount_decimal

        sell_usdc_value = self._convert_to_usdc_value(
            chain_id=request.chain_id,
            token_address=context.sell_token_address,
            amount_raw=context.sell_amount,
            taker=request.taker_address,
        )
        if sell_usdc_value <= 0:
            return buy_amount_str, buy_amount_decimal, None

        fee_usdc = (sell_usdc_value * INTEGRATOR_FEE_RATE).quantize(Decimal("0.00000001"), rounding=ROUND_DOWN)
        if fee_usdc <= 0:
            return buy_amount_str, buy_amount_decimal, None

        tokens_per_usdc = buy_amount_decimal / sell_usdc_value if sell_usdc_value > 0 else Decimal(0)
        fee_token_decimal = (fee_usdc * tokens_per_usdc)
        buy_amount_decimal_after = buy_amount_decimal - fee_token_decimal
        if buy_amount_decimal_after < 0:
            buy_amount_decimal_after = Decimal(0)

        if context.buy_token_decimals is None:
            raise ValueError("缺少 buy token decimals，無法扣除手續費")
        scale = Decimal(10) ** context.buy_token_decimals
        raw_after = int((buy_amount_decimal_after * scale).to_integral_value(rounding=ROUND_DOWN))

        return str(raw_after), buy_amount_decimal_after, fee_usdc

    def _prepare_context(self, request: SwapRequest) -> SwapContext:
        base_token = self._resolve_base_token(request.chain_id, request.base_token_symbol)
        quote_address = self._normalize_address(request.quote_token_address)
        quote_symbol = request.quote_token_symbol or self._fetch_token_symbol(quote_address) or "UNKNOWN"
        quote_decimals = request.quote_token_decimals

        direction = request.direction  # already normalized in SwapRequest
        if direction == "BASE_TO_QUOTE":
            sell_token_address = base_token.address
            sell_decimals = base_token.decimals
            buy_token_address = quote_address
            if quote_decimals is None:
                quote_decimals = self._fetch_token_decimals(quote_address)
            buy_decimals = quote_decimals
        else:
            sell_token_address = quote_address
            if quote_decimals is None:
                quote_decimals = self._fetch_token_decimals(quote_address)
            if quote_decimals is None:
                raise ValueError("無法取得 quote 代幣的小數位數，請在 SwapRequest 提供 quote_token_decimals")
            sell_decimals = quote_decimals
            buy_token_address = base_token.address
            buy_decimals = base_token.decimals

        sell_amount_decimal, sell_amount_raw = self._resolve_sell_amount(
            request, decimals=sell_decimals, token_address=sell_token_address
        )

        return SwapContext(
            base_token=base_token,
            quote_token_symbol=quote_symbol,
            quote_token_address=quote_address,
            quote_token_decimals=quote_decimals,
            sell_token_address=self._normalize_address(sell_token_address),
            sell_token_decimals=sell_decimals,
            buy_token_address=self._normalize_address(buy_token_address),
            buy_token_decimals=buy_decimals,
            sell_amount=sell_amount_raw,
            sell_amount_decimal=sell_amount_decimal,
            direction=direction,
        )

    def _convert_to_usdc_value(
        self,
        *,
        chain_id: int,
        token_address: str,
        amount_raw: str,
        taker: str,
    ) -> Decimal:
        normalized_token = self._normalize_address(token_address)
        usdc_info = self._get_chain_usdc(chain_id)
        if normalized_token == self._normalize_address(usdc_info.address):
            return Decimal(amount_raw) / (Decimal(10) ** usdc_info.decimals)

        response = self._swap_client.get_price(
            chain_id=chain_id,
            sell_token=normalized_token,
            buy_token=usdc_info.address,
            sell_amount=amount_raw,
            taker=taker,
        )
        buy_amount_str = response.get("buyAmount")
        if buy_amount_str is None:
            raise ZeroExAPIError("無法取得 USDC 估價以計算手續費")
        return Decimal(buy_amount_str) / (Decimal(10) ** usdc_info.decimals)

    def _get_chain_usdc(self, chain_id: int) -> TokenInfo:
        registry = BASE_TOKEN_REGISTRY.get(chain_id)
        if not registry or "USDC" not in registry:
            raise ValueError(f"Chain {chain_id} 缺少 USDC 設定，無法計算手續費")
        return registry["USDC"]

    def _resolve_base_token(self, chain_id: int, symbol: str) -> TokenInfo:
        registry = BASE_TOKEN_REGISTRY.get(chain_id)
        if not registry or symbol not in registry:
            raise ValueError(f"Chain {chain_id} 不支援 base token {symbol}")
        return registry[symbol]

    def _resolve_sell_amount(
        self,
        request: SwapRequest,
        *,
        decimals: int,
        token_address: str,
    ) -> tuple[Optional[Decimal], str]:
        if request.amount_wei is not None:
            raw_int = int(str(request.amount_wei), 10)
            if raw_int <= 0:
                raise ValueError("sell amount 必須為正整數")
            decimal_value = Decimal(raw_int) / Decimal(10**decimals)
            return decimal_value, str(raw_int)

        amount_decimal = self._normalize_decimal(request.amount)
        if amount_decimal is None or amount_decimal <= 0:
            raise ValueError("sell amount 必須大於 0")
        scaled = (amount_decimal * Decimal(10**decimals)).quantize(Decimal("1"), rounding=ROUND_DOWN)
        if scaled <= 0:
            raise ValueError("sell amount 小於最小單位")
        return amount_decimal, str(int(scaled))

    def _normalize_decimal(self, value: Optional[Decimal | str | float]) -> Optional[Decimal]:
        if value is None:
            return None
        if isinstance(value, Decimal):
            return value
        try:
            return Decimal(str(value))
        except InvalidOperation as exc:  # pragma: no cover - 無效輸入
            raise ValueError(f"無法解析 amount: {value}") from exc

    def _calculate_decimal(self, amount: Optional[str], decimals: Optional[int]) -> Optional[Decimal]:
        if amount is None or decimals is None:
            return None
        return Decimal(amount) / Decimal(10**decimals)

    def _calculate_price(
        self,
        *,
        sell_amount: str,
        sell_decimals: int,
        buy_amount: Optional[str],
        buy_decimals: Optional[int],
    ) -> Optional[float]:
        if buy_amount is None or buy_decimals is None:
            return None
        sell_value = Decimal(sell_amount) / Decimal(10**sell_decimals)
        if sell_value == 0:
            return None
        buy_value = Decimal(buy_amount) / Decimal(10**buy_decimals)
        return float(buy_value / sell_value)

    def _decimal_to_float(self, value: Optional[Decimal]) -> Optional[float]:
        if value is None:
            return None
        return float(value)

    def _extract_allowance_target(self, response: dict) -> Optional[str]:
        if response is None:
            return None
        allowance_target = response.get("allowanceTarget")
        if allowance_target:
            return self._normalize_address(allowance_target)
        issues = response.get("issues") or {}
        allowance_issue = issues.get("allowance") or {}
        spender = allowance_issue.get("spender")
        if spender:
            return self._normalize_address(spender)
        return None

    def _normalize_address(self, address: str) -> str:
        return Web3.to_checksum_address(address)

    def _fetch_token_symbol(self, address: str) -> Optional[str]:
        if self._web3 is None:
            return None
        try:
            contract = self._erc20_contract(address)
            symbol = contract.functions.symbol().call()
            if isinstance(symbol, bytes):
                symbol = symbol.decode("utf-8").strip("\x00")
            return symbol.upper()
        except ContractLogicError:  # pragma: no cover - 少見情境
            return None

    def _fetch_token_decimals(self, address: str) -> Optional[int]:
        if self._web3 is None:
            return None
        try:
            contract = self._erc20_contract(address)
            return contract.functions.decimals().call()
        except ContractLogicError:  # pragma: no cover - 少見情境
            return None

    def _erc20_contract(self, address: str) -> Contract:
        if self._web3 is None:
            raise Web3NotConfiguredError("需要 Web3 提供者來操作 ERC20 合約")
        return self._web3.eth.contract(address=self._normalize_address(address), abi=ERC20_ABI)

    def _ensure_allowance(
        self,
        *,
        owner: str,
        token: str,
        spender: str,
        amount: int,
        chain_id: int,
        private_key: str,
    ) -> Optional[str]:
        contract = self._erc20_contract(token)
        owner_address = self._normalize_address(owner)
        spender_address = self._normalize_address(spender)
        current = contract.functions.allowance(owner_address, spender_address).call()
        if current >= amount:
            return None

        nonce = self._web3.eth.get_transaction_count(owner_address, block_identifier="pending")
        tx = contract.functions.approve(spender_address, amount).build_transaction(
            {
                "chainId": chain_id,
                "from": owner_address,
                "nonce": nonce,
            }
        )
        if "gas" not in tx:
            tx["gas"] = int(self._web3.eth.estimate_gas(tx) * 1.2)
        if "gasPrice" not in tx and "maxFeePerGas" not in tx:
            tx["gasPrice"] = self._web3.eth.gas_price

        return self._send_transaction(tx, private_key=private_key)

    def _prepare_transaction_params(
        self,
        transaction: dict,
        *,
        chain_id: int,
        from_address: str,
    ) -> dict:
        if not transaction:
            raise ValueError("quote 回傳的 transaction 資料為空")

        params = dict(transaction)
        params["chainId"] = chain_id
        params["from"] = self._normalize_address(from_address)
        params["nonce"] = self._web3.eth.get_transaction_count(params["from"], block_identifier="pending")
        if "to" in params and params["to"]:
            params["to"] = self._normalize_address(params["to"])

        for key in ("gas", "gasPrice", "maxFeePerGas", "maxPriorityFeePerGas", "value"):
            if key in params and params[key] is not None:
                params[key] = int(params[key])
        return params

    def _send_transaction(self, tx_params: dict, *, private_key: str) -> str:
        if self._web3 is None:
            raise Web3NotConfiguredError("需要 Web3 提供者才能送出交易")
        signed = self._web3.eth.account.sign_transaction(tx_params, private_key=private_key)
        raw_tx = getattr(signed, "rawTransaction", None)
        if raw_tx is None:
            raw_tx = getattr(signed, "raw_transaction", None)
        if raw_tx is None:
            raise ValueError("Signed transaction 缺少 raw transaction 資料")
        tx_hash = self._web3.eth.send_raw_transaction(raw_tx)
        return tx_hash.hex()

    def _current_timestamps(self) -> dict[str, datetime]:
        now_utc = utc_now()
        return {
            "utc": now_utc,
            "local": now_utc.astimezone(self._tz),
        }
