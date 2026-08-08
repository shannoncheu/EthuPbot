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
