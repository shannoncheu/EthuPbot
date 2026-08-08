from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True, slots=True)
class Settings:
    discord_token: str
    ai_api_key: str | None
    ai_base_url: str | None
    ai_model: str
    ai_api_mode: str
    coingecko_api_key: str | None
    etherscan_api_key: str | None
    database_path: Path
    log_level: str

    @classmethod
    def from_env(cls) -> Settings:
        load_dotenv()
        discord_token = os.getenv("DISCORD_TOKEN", "").strip()
        if not discord_token:
            raise ValueError("缺少 DISCORD_TOKEN，请复制 .env.example 为 .env 后填写。")

        ai_api_mode = os.getenv("AI_API_MODE", "chat_completions").strip().lower()
        if ai_api_mode not in {"chat_completions", "responses"}:
            raise ValueError("AI_API_MODE 只能是 chat_completions 或 responses。")

        return cls(
            discord_token=discord_token,
            ai_api_key=os.getenv("AI_API_KEY") or os.getenv("OPENAI_API_KEY") or None,
            ai_base_url=os.getenv("AI_BASE_URL") or os.getenv("OPENAI_BASE_URL") or None,
            ai_model=os.getenv("AI_MODEL", os.getenv("OPENAI_MODEL", "gpt-5-mini")).strip(),
            ai_api_mode=ai_api_mode,
            coingecko_api_key=os.getenv("COINGECKO_API_KEY") or None,
            etherscan_api_key=os.getenv("ETHERSCAN_API_KEY") or None,
            database_path=Path(os.getenv("DATABASE_PATH", "data/crypto_bot.db")),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        )
