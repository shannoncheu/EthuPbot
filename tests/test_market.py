import pytest

from crypto_bot.market import CoinGeckoClient, MarketError


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
