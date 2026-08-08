from pathlib import Path

import pytest

from crypto_bot.bot import CryptoBot
from crypto_bot.config import Settings


@pytest.mark.asyncio
async def test_bot_registers_expected_commands(tmp_path: Path) -> None:
    settings = Settings(
        discord_token="test-token",
        ai_api_key=None,
        ai_base_url="https://sub2api.example/v1",
        ai_model="gpt-5-mini",
        ai_api_mode="chat_completions",
        coingecko_api_key=None,
        etherscan_api_key=None,
        database_path=tmp_path / "test.db",
        log_level="INFO",
    )
    bot = CryptoBot(settings)
    names = {command.name for command in bot.tree.get_commands()}
    assert {
        "setup",
        "price",
        "market",
        "chart",
        "gas",
        "alert",
        "alerts",
        "alert_delete",
        "chat_clear",
        "bot_status",
    } <= names
    await bot.close()
