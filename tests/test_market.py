import pytest

from crypto_bot.market import CoinGeckoClient, GateClient, MarketError, extract_gate_symbols


@pytest.mark.asyncio
async def test_common_coin_resolution_needs_no_network() -> None:
    client = CoinGeckoClient()
    coin = await client.resolve_coin("BTC")
    assert coin.id == "bitcoin"
    assert coin.symbol == "BTC"


@pytest.mark.asyncio
async def test_invalid_coin_query_is_rejected_before_network() -> None:
    client = CoinGeckoClient()
    with pytest.raises(MarketError):
        await client.resolve_coin("<invalid coin>")


@pytest.mark.asyncio
async def test_gate_ticker_is_normalized(monkeypatch: pytest.MonkeyPatch) -> None:
    client = GateClient()

    async def fake_get(*args: object, **kwargs: object) -> list[dict[str, str]]:
        return [{
            "currency_pair": "BLESS_USDT",
            "last": "0.031",
            "change_percentage": "2.5",
            "high_24h": "0.034",
            "low_24h": "0.029",
            "quote_volume": "123456",
        }]

    monkeypatch.setattr(client, "_get", fake_get)
    item = await client.ticker("bless")
    assert item["currency_pair"] == "BLESS_USDT"
    assert item["current_price"] == 0.031
    assert item["price_change_percentage_24h"] == 2.5


def test_extract_gate_symbols_from_chinese_question() -> None:
    assert extract_gate_symbols("bless现在的价格是多少") == ["BLESS"]
    assert extract_gate_symbols("那KORU的行情呢") == ["KORU"]
