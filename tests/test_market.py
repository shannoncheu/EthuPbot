import pytest

from crypto_bot.market import (
    CoinGeckoClient,
    GateClient,
    MarketError,
    extract_gate_symbols,
    is_market_question,
    summarize_candlesticks,
)


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
        return [
            {
                "currency_pair": "BLESS_USDT",
                "last": "0.031",
                "change_percentage": "2.5",
                "high_24h": "0.034",
                "low_24h": "0.029",
                "quote_volume": "123456",
            }
        ]

    monkeypatch.setattr(client, "_get", fake_get)
    item = await client.ticker("bless", "spot")
    assert item["currency_pair"] == "BLESS_USDT"
    assert item["current_price"] == 0.031
    assert item["price_change_percentage_24h"] == 2.5


@pytest.mark.asyncio
async def test_gate_uses_usdt_futures_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    client = GateClient()

    async def fake_get(path: str, *args: object, **kwargs: object) -> list[dict[str, str]]:
        if path == "/spot/tickers":
            raise MarketError("Gate 返回 HTTP 400")
        return [
            {
                "contract": "KORU_USDT",
                "last": "0.0201",
                "mark_price": "0.0200",
                "index_price": "0.0199",
                "change_percentage": "3.2",
                "funding_rate": "0.0001",
                "high_24h": "0.022",
                "low_24h": "0.018",
                "volume_24h_quote": "100000",
            }
        ]

    monkeypatch.setattr(client, "_get", fake_get)
    item = await client.ticker("KORU")
    assert item["market_type"] == "futures"
    assert item["current_price"] == 0.0200
    assert item["last_price"] == 0.0201


def test_extract_gate_symbols_from_chinese_question() -> None:
    assert extract_gate_symbols("bless现在的价格是多少") == ["BLESS"]
    assert extract_gate_symbols("那KORU的行情呢") == ["KORU"]
    assert extract_gate_symbols("KORU和BLESS的价格分别是多少") == ["KORU", "BLESS"]
    assert extract_gate_symbols("Lab/USDT 是否适合进多单") == ["LAB"]
    assert extract_gate_symbols("lab_usdt 的 OI 是多少") == ["LAB"]
    assert extract_gate_symbols("AI/USDT 的价格") == ["AI"]
    assert extract_gate_symbols("$A 的价格") == ["A"]
    assert extract_gate_symbols("LAB怎么样") == ["LAB"]
    assert extract_gate_symbols("分析一下 LAB 现在能不能进") == ["LAB"]
    assert extract_gate_symbols("那 ETH 呢？") == ["ETH"]
    assert extract_gate_symbols("那 AI 呢？") == ["AI"]


def test_market_follow_up_is_detected_without_guessing_a_symbol() -> None:
    assert is_market_question("你查询一下相关的交易量和持仓呢")
    assert extract_gate_symbols("你查询一下相关的交易量和持仓呢") == []


def test_candlestick_summary_calculates_indicators() -> None:
    candles = [
        {
            "time": float(index),
            "open": float(index),
            "high": float(index) + 0.5,
            "low": float(index) - 0.5,
            "close": float(index),
            "quote_volume": 200.0 if index == 21 else 100.0,
        }
        for index in range(21, 0, -1)
    ]
    summary = summarize_candlesticks(candles)
    assert summary["bars"] == 21
    assert summary["sma20"] == pytest.approx(11.5)
    assert summary["rsi14"] == pytest.approx(100)
    assert summary["volume_ratio_20"] == pytest.approx(2)
    assert summary["support_20"] == pytest.approx(1.5)
    assert summary["resistance_20"] == pytest.approx(21.5)


@pytest.mark.asyncio
async def test_gate_market_analysis_includes_volume_oi_and_klines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = GateClient()

    async def fake_get(
        path: str, params: dict[str, object] | None = None
    ) -> list[dict[str, str]]:
        assert params is not None
        assert params["contract"] == "LAB_USDT"
        if path.endswith("/tickers"):
            return [
                {
                    "last": "0.073",
                    "mark_price": "0.07303",
                    "index_price": "0.07301",
                    "change_percentage": "-0.57",
                    "funding_rate": "0.0001",
                    "high_24h": "0.075",
                    "low_24h": "0.071",
                    "volume_24h_quote": "1234567.89",
                    "volume_24h_base": "17000000",
                    "volume_24h": "900000",
                    "total_size": "987654",
                }
            ]
        if path.endswith("/candlesticks"):
            return [
                {
                    "t": str(index),
                    "o": str(index / 1000),
                    "h": str(index / 1000 + 0.001),
                    "l": str(index / 1000 - 0.001),
                    "c": str(index / 1000),
                    "v": "100",
                    "sum": "2500",
                }
                for index in range(1, 61)
            ]
        return [
            {
                "time": str(index * 3600),
                "open_interest": str(900000 + index * 1000),
                "open_interest_usd": str(65000 + index * 100),
                "lsr_account": "1.1",
                "lsr_taker": "0.9",
            }
            for index in range(25)
        ]

    monkeypatch.setattr(client, "_get", fake_get)
    item = await client.market_analysis("LAB")
    assert item["current_price"] == pytest.approx(0.07303)
    assert item["quote_volume_24h"] == pytest.approx(1_234_567.89)
    assert item["funding_rate"] == pytest.approx(0.0001)
    assert item["open_interest_contracts"] == pytest.approx(924000)
    assert item["open_interest_usd"] == pytest.approx(67_400)
    assert item["open_interest_change_percentage"] == pytest.approx(24_000 / 900_000 * 100)
    assert item["technical_15m"]["bars"] == 59
    assert item["technical_15m"]["latest_bar_excluded"] is True
    assert item["technical_4h"]["bars"] == 59


@pytest.mark.asyncio
async def test_gate_market_analysis_keeps_ticker_when_oi_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = GateClient()

    async def fake_get(
        path: str, params: dict[str, object] | None = None
    ) -> list[dict[str, str]]:
        if path.endswith("/tickers"):
            return [{"mark_price": "1", "volume_24h_quote": "100"}]
        if path.endswith("/contract_stats"):
            raise MarketError("OI 暂不可用")
        return [
            {"t": str(index), "o": "1", "h": "2", "l": "0.5", "c": "1", "sum": "10"}
            for index in range(1, 3)
        ]

    monkeypatch.setattr(client, "_get", fake_get)
    item = await client.market_analysis("LAB")
    assert item["current_price"] == 1
    assert item["technical_15m"]
    assert item["partial_errors"] == {"open_interest": "OI 暂不可用"}
