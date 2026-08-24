from pathlib import Path
from types import SimpleNamespace

import pytest

from crypto_bot.ai import ChatService, format_market_context
from crypto_bot.database import Database


class FakeCompletions:
    def __init__(self) -> None:
        self.request: dict[str, object] = {}

    async def create(self, **kwargs: object) -> SimpleNamespace:
        self.request = kwargs
        message = SimpleNamespace(content="简洁但完整的测试回复")
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


@pytest.mark.asyncio
async def test_sub2api_chat_completions_mode(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.db")
    await db.initialize()
    service = ChatService(
        "test-key", "https://sub2api.example/v1", "test-model", "chat_completions", db
    )
    completions = FakeCompletions()
    service.client = SimpleNamespace(chat=SimpleNamespace(completions=completions))

    answer = await service.reply(
        1,
        "用户",
        "BTC 现在怎么样？",
        {
            "bitcoin": {
                "symbol": "btc",
                "quote_currency": "USDT",
                "current_price": 100_000,
                "price_change_percentage_24h": 2.5,
                "quote_volume_24h": 1_234_567.89,
                "funding_rate": 0.0001,
                "open_interest_contracts": 987_654,
                "open_interest_quote": 98_765_400,
                "technical_15m": {
                    "bars": 60,
                    "trend": "多头排列",
                    "sma20": 99_500,
                    "sma50": 98_000,
                    "rsi14": 61.2,
                    "support_20": 97_000,
                    "resistance_20": 101_000,
                    "volume_ratio_20": 1.3,
                },
            }
        },
    )

    assert answer == "简洁但完整的测试回复"
    assert completions.request["model"] == "test-model"
    assert completions.request["max_completion_tokens"] == 650
    messages = completions.request["messages"]
    assert messages[0]["role"] == "system"
    assert "不得为了简短而省略" in messages[0]["content"]
    assert "24h成交额：1,234,567.89 USDT" in messages[0]["content"]
    assert "全市场未平仓量（OI）：987,654.00 张" in messages[0]["content"]
    assert "当前资金费率：+0.0100%" in messages[0]["content"]
    assert "15m K线摘要" in messages[0]["content"]
    assert "不得声称自己无法查询" in messages[0]["content"]


def test_missing_market_values_are_not_rendered_as_zero() -> None:
    rendered = format_market_context(
        {"LAB": {"symbol": "LAB", "quote_currency": "USDT", "current_price": None}}
    )
    assert "当前标记价：暂不可用 USDT" in rendered
    assert "当前标记价：0" not in rendered
