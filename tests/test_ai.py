from pathlib import Path
from types import SimpleNamespace

import pytest

from crypto_bot.ai import ChatService
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
                "current_price": 100_000,
                "price_change_percentage_24h": 2.5,
            }
        },
    )

    assert answer == "简洁但完整的测试回复"
    assert completions.request["model"] == "test-model"
    assert completions.request["max_tokens"] == 500
    messages = completions.request["messages"]
    assert messages[0]["role"] == "system"
    assert "不得为了简短而省略" in messages[0]["content"]
