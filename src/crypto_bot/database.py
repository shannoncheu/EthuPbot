from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite


@dataclass(slots=True)
class GuildConfig:
    guild_id: int
    market_channel_id: int | None = None
    chat_channel_id: int | None = None
    daily_channel_id: int | None = None
    update_minutes: int = 10
    daily_hour: int = 8
    timezone: str = "Asia/Shanghai"
    market_message_id: int | None = None
    last_market_at: str | None = None
    last_daily_date: str | None = None


@dataclass(slots=True)
class PriceAlert:
    id: int
    guild_id: int
    channel_id: int
    user_id: int
    coin_id: str
    coin_symbol: str
    direction: str
    target: float
    active: bool


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path

    async def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        async with self.connect() as db:
            await db.executescript(
                """
                PRAGMA journal_mode=WAL;
                PRAGMA foreign_keys=ON;

                CREATE TABLE IF NOT EXISTS guild_config (
                    guild_id INTEGER PRIMARY KEY,
                    market_channel_id INTEGER,
                    chat_channel_id INTEGER,
                    daily_channel_id INTEGER,
                    update_minutes INTEGER NOT NULL DEFAULT 10,
                    daily_hour INTEGER NOT NULL DEFAULT 8,
                    timezone TEXT NOT NULL DEFAULT 'Asia/Shanghai',
                    market_message_id INTEGER,
                    last_market_at TEXT,
                    last_daily_date TEXT
                );

                CREATE TABLE IF NOT EXISTS daily_snapshot (
                    guild_id INTEGER NOT NULL,
                    snapshot_date TEXT NOT NULL,
                    coin_id TEXT NOT NULL,
                    price REAL NOT NULL,
                    PRIMARY KEY (guild_id, snapshot_date, coin_id)
                );

                CREATE TABLE IF NOT EXISTS price_alert (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    coin_id TEXT NOT NULL,
                    coin_symbol TEXT NOT NULL,
                    direction TEXT NOT NULL CHECK(direction IN ('above', 'below')),
                    target REAL NOT NULL CHECK(target > 0),
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    triggered_at TEXT
                );

                CREATE TABLE IF NOT EXISTS chat_message (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_id INTEGER NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_alert_active ON price_alert(active);
                CREATE INDEX IF NOT EXISTS idx_chat_channel ON chat_message(channel_id, id DESC);
                """
            )
            await db.commit()

    def connect(self) -> aiosqlite.Connection:
        return aiosqlite.connect(self.path)

    async def ensure_guild(self, guild_id: int) -> None:
        async with self.connect() as db:
            await db.execute("INSERT OR IGNORE INTO guild_config(guild_id) VALUES (?)", (guild_id,))
            await db.commit()

    async def get_guild(self, guild_id: int) -> GuildConfig:
        await self.ensure_guild(guild_id)
        async with self.connect() as db:
            db.row_factory = aiosqlite.Row
            row = await (
                await db.execute("SELECT * FROM guild_config WHERE guild_id = ?", (guild_id,))
            ).fetchone()
        assert row is not None
        return GuildConfig(**dict(row))

    async def list_guilds(self) -> list[GuildConfig]:
        async with self.connect() as db:
            db.row_factory = aiosqlite.Row
            rows = await (await db.execute("SELECT * FROM guild_config")).fetchall()
        return [GuildConfig(**dict(row)) for row in rows]

    async def update_guild(self, guild_id: int, **fields: object) -> None:
        allowed = {
            "market_channel_id",
            "chat_channel_id",
            "daily_channel_id",
            "update_minutes",
            "daily_hour",
            "timezone",
            "market_message_id",
            "last_market_at",
            "last_daily_date",
        }
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"不允许更新字段: {unknown}")
        if not fields:
            return
        await self.ensure_guild(guild_id)
        assignments = ", ".join(f"{key} = ?" for key in fields)
        async with self.connect() as db:
            await db.execute(
                f"UPDATE guild_config SET {assignments} WHERE guild_id = ?",  # noqa: S608
                (*fields.values(), guild_id),
            )
            await db.commit()

    async def save_snapshot(
        self, guild_id: int, snapshot_date: str, prices: dict[str, float]
    ) -> None:
        async with self.connect() as db:
            await db.executemany(
                """INSERT OR REPLACE INTO daily_snapshot
                   (guild_id, snapshot_date, coin_id, price) VALUES (?, ?, ?, ?)""",
                [(guild_id, snapshot_date, coin_id, price) for coin_id, price in prices.items()],
            )
            await db.commit()

    async def previous_snapshot(
        self, guild_id: int, before_date: str
    ) -> tuple[str, dict[str, float]] | None:
        async with self.connect() as db:
            db.row_factory = aiosqlite.Row
            date_row = await (
                await db.execute(
                    """SELECT MAX(snapshot_date) AS snapshot_date FROM daily_snapshot
                   WHERE guild_id = ? AND snapshot_date < ?""",
                    (guild_id, before_date),
                )
            ).fetchone()
            if not date_row or not date_row["snapshot_date"]:
                return None
            date = date_row["snapshot_date"]
            rows = await (
                await db.execute(
                    """SELECT coin_id, price FROM daily_snapshot
                   WHERE guild_id = ? AND snapshot_date = ?""",
                    (guild_id, date),
                )
            ).fetchall()
        return date, {row["coin_id"]: row["price"] for row in rows}

    async def add_alert(
        self,
        guild_id: int,
        channel_id: int,
        user_id: int,
        coin_id: str,
        coin_symbol: str,
        direction: str,
        target: float,
    ) -> int:
        async with self.connect() as db:
            cursor = await db.execute(
                """INSERT INTO price_alert
                   (guild_id, channel_id, user_id, coin_id, coin_symbol,
                    direction, target, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    guild_id,
                    channel_id,
                    user_id,
                    coin_id,
                    coin_symbol,
                    direction,
                    target,
                    datetime.now(UTC).isoformat(),
                ),
            )
            await db.commit()
            return int(cursor.lastrowid)

    async def active_alerts(self) -> list[PriceAlert]:
        async with self.connect() as db:
            db.row_factory = aiosqlite.Row
            rows = await (
                await db.execute(
                    """SELECT id, guild_id, channel_id, user_id, coin_id, coin_symbol,
                          direction, target, active
                   FROM price_alert WHERE active = 1"""
                )
            ).fetchall()
        return [PriceAlert(**{**dict(row), "active": bool(row["active"])}) for row in rows]

    async def user_alerts(self, guild_id: int, user_id: int) -> list[PriceAlert]:
        async with self.connect() as db:
            db.row_factory = aiosqlite.Row
            rows = await (
                await db.execute(
                    """SELECT id, guild_id, channel_id, user_id, coin_id, coin_symbol,
                          direction, target, active
                   FROM price_alert WHERE guild_id = ? AND user_id = ? AND active = 1
                   ORDER BY id""",
                    (guild_id, user_id),
                )
            ).fetchall()
        return [PriceAlert(**{**dict(row), "active": bool(row["active"])}) for row in rows]

    async def deactivate_alert(
        self, alert_id: int, guild_id: int, user_id: int | None = None
    ) -> bool:
        query = "UPDATE price_alert SET active = 0, triggered_at = ? WHERE id = ? AND guild_id = ?"
        params: list[object] = [datetime.now(UTC).isoformat(), alert_id, guild_id]
        if user_id is not None:
            query += " AND user_id = ?"
            params.append(user_id)
        async with self.connect() as db:
            cursor = await db.execute(query, params)
            await db.commit()
            return cursor.rowcount > 0

    async def chat_history(self, channel_id: int, limit: int = 10) -> list[dict[str, str]]:
        async with self.connect() as db:
            db.row_factory = aiosqlite.Row
            rows = await (
                await db.execute(
                    """SELECT role, content FROM chat_message WHERE channel_id = ?
                   ORDER BY id DESC LIMIT ?""",
                    (channel_id, limit),
                )
            ).fetchall()
        return [dict(row) for row in reversed(rows)]

    async def add_chat_message(self, channel_id: int, role: str, content: str) -> None:
        async with self.connect() as db:
            await db.execute(
                """INSERT INTO chat_message(channel_id, role, content, created_at)
                   VALUES (?, ?, ?, ?)""",
                (channel_id, role, content, datetime.now(UTC).isoformat()),
            )
            await db.execute(
                """DELETE FROM chat_message WHERE channel_id = ? AND id NOT IN (
                       SELECT id FROM chat_message WHERE channel_id = ? ORDER BY id DESC LIMIT 20
                   )""",
                (channel_id, channel_id),
            )
            await db.commit()

    async def clear_chat(self, channel_id: int) -> None:
        async with self.connect() as db:
            await db.execute("DELETE FROM chat_message WHERE channel_id = ?", (channel_id,))
            await db.commit()
