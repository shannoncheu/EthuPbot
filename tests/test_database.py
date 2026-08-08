from pathlib import Path

import pytest

from crypto_bot.database import Database


@pytest.mark.asyncio
async def test_guild_config_and_daily_snapshots(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.db")
    await db.initialize()
    config = await db.get_guild(123)
    assert config.timezone == "Asia/Shanghai"

    await db.update_guild(123, update_minutes=15, market_channel_id=456)
    config = await db.get_guild(123)
    assert config.update_minutes == 15
    assert config.market_channel_id == 456

    await db.save_snapshot(123, "2026-08-07", {"bitcoin": 100.0, "ethereum": 10.0})
    previous = await db.previous_snapshot(123, "2026-08-08")
    assert previous == ("2026-08-07", {"bitcoin": 100.0, "ethereum": 10.0})


@pytest.mark.asyncio
async def test_alert_lifecycle_and_chat_limit(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.db")
    await db.initialize()
    alert_id = await db.add_alert(1, 2, 3, "bitcoin", "BTC", "above", 100_000)
    alerts = await db.active_alerts()
    assert alerts[0].id == alert_id
    assert await db.deactivate_alert(alert_id, 1, 3)
    assert await db.active_alerts() == []

    for index in range(25):
        await db.add_chat_message(2, "user", f"message {index}")
    history = await db.chat_history(2, limit=30)
    assert len(history) == 20
    assert history[-1]["content"] == "message 24"


@pytest.mark.asyncio
async def test_position_lifecycle(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.db")
    await db.initialize()
    await db.update_guild(1, position_channel_id=99)
    position_id = await db.add_position(1, 2, "BTC", "futures", 64_000, 0.1, 10, "long")
    positions = await db.user_positions(1, 2)
    assert positions[0].id == position_id
    assert positions[0].leverage == 10
    assert (await db.get_guild(1)).position_channel_id == 99
    assert await db.delete_position(position_id, 1, 2)
    assert await db.user_positions(1, 2) == []
