from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from typing import Optional
from unittest.mock import MagicMock

import httpx
import pytest
from sqlalchemy.orm import sessionmaker
from web3 import Web3

from nansen_sm_collector.data import schemas
from nansen_sm_collector.data.db import create_db_engine, create_session_factory
from nansen_sm_collector.trading import (
    SwapRequest,
    Web3NotConfiguredError,
    ZeroExSwapClient,
    ZeroExTradingService,
)


ALLOWANCE_TARGET = "0x0000000000001fF3684F28C67538d4d072c22734"


def _make_session_factory(tmp_path) -> sessionmaker:
    db_path = tmp_path / "collector.db"
    engine = create_db_engine(f"sqlite:///{db_path}")
    schemas.Base.metadata.create_all(engine)
    return create_session_factory(engine)


def _make_swap_client(price_response: dict, quote_response: Optional[dict] = None) -> tuple[ZeroExSwapClient, httpx.Client]:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/price"):
            return httpx.Response(status_code=200, json=price_response)
        if request.url.path.endswith("/quote"):
            if quote_response is None:
                pytest.fail("Unexpected quote request during test")
            return httpx.Response(status_code=200, json=quote_response)
        pytest.fail(f"Unexpected request path: {request.url.path}")

    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(base_url="https://api.0x.org", transport=transport)
    swap_client = ZeroExSwapClient("test-key", client=http_client)
    return swap_client, http_client


def test_simulate_swap_records_trade(tmp_path) -> None:
    price_response = {
        "sellAmount": "100000000",
        "buyAmount": "250000000",
        "allowanceTarget": ALLOWANCE_TARGET,
        "zid": "price-test",
    }

    swap_client, http_client = _make_swap_client(price_response)
    session_factory = _make_session_factory(tmp_path)
    service = ZeroExTradingService(swap_client, session_factory, timezone="UTC")

    request = SwapRequest(
        chain_id=1,
        taker_address="0x5A384227B65FA093DEC03Ec34E111Db80A040615",
        base_token_symbol="USDC",
        quote_token_address="0xdAC17F958D2ee523a2206206994597C13D831ec7",
        quote_token_symbol="USDT",
        amount=Decimal("100"),
        quote_token_decimals=6,
    )

    try:
        result = service.simulate_swap(request)
    finally:
        swap_client.close()
        http_client.close()

    assert result.status == "COMPLETED"
    session = session_factory()
    try:
        trade = session.query(schemas.ExecutedTradeModel).one()
        assert trade.mode == "SIMULATION"
        assert trade.status == "COMPLETED"
        assert trade.sell_amount == "100000000"
        assert trade.sell_amount_decimal == pytest.approx(100.0)
        assert trade.buy_amount == "249625000"
        assert trade.buy_amount_decimal == pytest.approx(249.625)
        assert trade.integrator_fee_usdc == pytest.approx(0.15)
        assert trade.price == pytest.approx(2.5)
        expected_allowance = Web3.to_checksum_address(ALLOWANCE_TARGET)
        assert trade.allowance_target == expected_allowance
    finally:
        session.close()


def test_execute_live_swap_records_success(tmp_path) -> None:
    price_response = {
        "sellAmount": "100000000",
        "buyAmount": "250000000",
        "allowanceTarget": ALLOWANCE_TARGET,
        "zid": "price-test",
    }
    quote_response = {
        "sellAmount": "100000000",
        "buyAmount": "300000000",
        "allowanceTarget": ALLOWANCE_TARGET,
        "zid": "quote-test",
        "transaction": {
            "to": ALLOWANCE_TARGET,
            "data": "0x1234",
            "gas": "850000",
            "gasPrice": "1000000000",
            "value": "0",
        },
    }

    swap_client, http_client = _make_swap_client(price_response, quote_response)
    session_factory = _make_session_factory(tmp_path)

    wait_receipt_mock = MagicMock(return_value=SimpleNamespace(blockNumber=123, status=1))
    web3_stub = SimpleNamespace(eth=SimpleNamespace(wait_for_transaction_receipt=wait_receipt_mock))

    service = ZeroExTradingService(swap_client, session_factory, timezone="UTC", web3=web3_stub)
    service._ensure_allowance = MagicMock(return_value="0xallowancetx")  # type: ignore[method-assign]
    service._prepare_transaction_params = MagicMock(return_value={"nonce": 1})  # type: ignore[method-assign]
    service._send_transaction = MagicMock(return_value="0xdeadbeef")  # type: ignore[method-assign]

    request = SwapRequest(
        chain_id=1,
        taker_address="0x5A384227B65FA093DEC03Ec34E111Db80A040615",
        base_token_symbol="USDC",
        quote_token_address="0xdAC17F958D2ee523a2206206994597C13D831ec7",
        quote_token_symbol="USDT",
        amount=Decimal("100"),
        quote_token_decimals=6,
    )

    try:
        result = service.execute_live_swap(
            request,
            private_key="0xabc123",
            wait_for_receipt=True,
            receipt_timeout=30,
        )
    finally:
        swap_client.close()
        http_client.close()

    assert result.status == "COMPLETED"
    assert result.tx_hash == "0xdeadbeef"
    assert result.error_message is None
    assert result.quote_response == quote_response

    session = session_factory()
    try:
        trade = session.query(schemas.ExecutedTradeModel).one()
        assert trade.mode == "LIVE"
        assert trade.status == "COMPLETED"
        assert trade.tx_hash == "0xdeadbeef"
        assert trade.quote_id == "quote-test"
        assert trade.buy_amount == "299550000"
        assert trade.buy_amount_decimal == pytest.approx(299.55)
        assert trade.price == pytest.approx(3.0)
        assert trade.integrator_fee_usdc == pytest.approx(0.15)
        assert trade.transaction_payload["allowance_tx_hash"] == "0xallowancetx"
        assert trade.transaction_payload["receipt"]["status"] == 1
    finally:
        session.close()


def test_execute_live_swap_requires_web3(tmp_path) -> None:
    price_response = {
        "sellAmount": "100000000",
        "buyAmount": "250000000",
        "allowanceTarget": ALLOWANCE_TARGET,
    }
    swap_client, http_client = _make_swap_client(price_response)
    session_factory = _make_session_factory(tmp_path)
    service = ZeroExTradingService(swap_client, session_factory, timezone="UTC", web3=None)

    request = SwapRequest(
        chain_id=1,
        taker_address="0x5A384227B65FA093DEC03Ec34E111Db80A040615",
        base_token_symbol="USDC",
        quote_token_address="0xdAC17F958D2ee523a2206206994597C13D831ec7",
        quote_token_symbol="USDT",
        amount=Decimal("1"),
        quote_token_decimals=6,
    )

    with pytest.raises(Web3NotConfiguredError):
        service.execute_live_swap(request, private_key="0xabc")

    swap_client.close()
    http_client.close()
