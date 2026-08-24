from pathlib import Path

import pytest

from crypto_bot.bot import CryptoBot
from crypto_bot.config import Settings
from crypto_bot.database import Position
from crypto_bot.market import MarketError


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
        "position_channel",
        "position_add",
        "positions",
        "position_delete",
        "steam_setup",
        "steam_now",
        "steam_disable",
    } <= names
    await bot.close()


@pytest.mark.asyncio
async def test_position_report_shows_prominent_total_pnl(tmp_path: Path) -> None:
    settings = Settings(
        discord_token="test-token",
        ai_api_key=None,
        ai_base_url=None,
        ai_model="test-model",
        ai_api_mode="chat_completions",
        coingecko_api_key=None,
        etherscan_api_key=None,
        database_path=tmp_path / "test.db",
        log_level="INFO",
    )
    bot = CryptoBot(settings)
    positions = [
        Position(2, 1, 7, "LAB", "futures", 0.06987, 50_000, 10, "long"),
        Position(3, 1, 7, "ESPORTS", "futures", 0.01472, 200_000, 10, "long"),
    ]

    class FakeDatabase:
        async def user_positions(self, guild_id: int, user_id: int) -> list[Position]:
            assert (guild_id, user_id) == (1, 7)
            return positions

    class FakeGate:
        fail_esports = False

        async def ticker(self, symbol: str, market_type: str) -> dict[str, object]:
            if self.fail_esports and symbol == "ESPORTS":
                raise MarketError("测试行情失败")
            prices = {"LAB": 0.06904, "ESPORTS": 0.0144}
            return {
                "symbol": symbol,
                "market_type": market_type,
                "current_price": prices[symbol],
                "quote_currency": "USDT",
            }

        async def close(self) -> None:
            return None

    gate = FakeGate()
    bot.db = FakeDatabase()  # type: ignore[assignment]
    bot.gate = gate  # type: ignore[assignment]
    try:
        embed = await bot._position_report(1, 7)
        assert "总计" in (embed.title or "")
        assert "总未实现盈亏" in (embed.description or "")
        assert "-105.5000 USDT" in (embed.description or "")
        assert "643.7500 USDT" in (embed.description or "")
        assert "-16.39%" in (embed.description or "")
        assert embed.colour.value == 0xEA3943

        gate.fail_esports = True
        partial = await bot._position_report(1, 7)
        assert "已计价" in (partial.title or "")
        assert "已计价 **1/2**" in (partial.description or "")
        assert "合计不含无法取价的仓位" in (partial.description or "")
    finally:
        await bot.close()
