from pathlib import Path

import aiosqlite
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


@pytest.mark.asyncio
async def test_steam_config_and_notification_deduplication(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.db")
    await db.initialize()
    await db.update_guild(
        1,
        steam_channel_id=10,
        steam_highlight_channel_id=11,
        steam_interval_hours=4,
        steam_min_discount=40,
        steam_pin_highlights=False,
    )
    config = await db.get_guild(1)
    assert config.steam_channel_id == 10
    assert config.steam_highlight_channel_id == 11
    assert config.steam_interval_hours == 4
    assert config.steam_min_discount == 40
    assert config.steam_pin_highlights is False

    deal = (123, 50, "¥25.00")
    assert await db.steam_deals_to_notify(1, [deal]) == {123}
    await db.mark_steam_deals_notified(1, [deal])
    assert await db.steam_deals_to_notify(1, [deal]) == set()
    assert await db.steam_deals_to_notify(1, [(123, 60, "¥20.00")]) == {123}


@pytest.mark.asyncio
async def test_existing_database_is_migrated_for_steam(tmp_path: Path) -> None:
    path = tmp_path / "existing.db"
    async with aiosqlite.connect(path) as connection:
        await connection.execute(
            """CREATE TABLE guild_config (
                guild_id INTEGER PRIMARY KEY,
                market_channel_id INTEGER,
                chat_channel_id INTEGER,
                daily_channel_id INTEGER,
                position_channel_id INTEGER,
                update_minutes INTEGER NOT NULL DEFAULT 10,
                daily_hour INTEGER NOT NULL DEFAULT 8,
                timezone TEXT NOT NULL DEFAULT 'Asia/Shanghai',
                market_message_id INTEGER,
                last_market_at TEXT,
                last_daily_date TEXT
            )"""
        )
        await connection.execute("INSERT INTO guild_config(guild_id) VALUES (99)")
        await connection.commit()

    db = Database(path)
    await db.initialize()
    config = await db.get_guild(99)

    assert config.steam_channel_id is None
    assert config.steam_interval_hours == 6
    assert config.steam_min_discount == 30
    assert config.steam_pin_highlights is True
