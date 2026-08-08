from __future__ import annotations

from typing import Any

from openai import AsyncOpenAI

from .database import Database


class AIUnavailable(RuntimeError):
    pass


class ChatService:
    def __init__(
        self,
        api_key: str | None,
        base_url: str | None,
        model: str,
        api_mode: str,
        database: Database,
    ) -> None:
        client_options = {"api_key": api_key}
        if base_url:
            client_options["base_url"] = base_url.rstrip("/")
        self.client = AsyncOpenAI(**client_options) if api_key else None
        self.model = model
        self.api_mode = api_mode
        self.database = database

    async def reply(
        self,
        channel_id: int,
        user_name: str,
        message: str,
        market_context: dict[str, dict[str, Any]],
    ) -> str:
        if self.client is None:
            raise AIUnavailable("管理员尚未配置 AI_API_KEY，AI 聊天暂不可用。")

        history = await self.database.chat_history(channel_id, limit=10)
        prices = []
        for item in market_context.values():
            prices.append(
                f"{item.get('symbol', '').upper()}: ${item.get('current_price', 0):,.2f}, "
                f"24h {item.get('price_change_percentage_24h', 0):+.2f}%"
            )

        instructions = (
            "你是 Discord 加密货币社区里的友好中文助手。回答简洁、自然，不刷屏；优先"
            "使用短句和紧凑列表，但不得为了简短而省略关键数据、统计周期、数据时间、来源、"
            "触发条件、限制或风险边界。"
            "行情问题只能使用下面提供的实时数据；没有数据时明确说不知道，不得编造价格、"
            "新闻、监管事件或收益保证。区分事实、推测和个人观点。涉及投资决策时提醒用户"
            "自行研究，且不构成投资建议。不要索要助记词、私钥或交易所密码。\n"
            f"当前行情数据（来自 CoinGecko）：{'; '.join(prices)}"
        )
        inputs = [*history, {"role": "user", "content": f"{user_name}: {message}"}]

        if self.api_mode == "responses":
            response = await self.client.responses.create(
                model=self.model,
                instructions=instructions,
                input=inputs,
                max_output_tokens=500,
                store=False,
            )
            answer = response.output_text.strip()
        else:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "system", "content": instructions}, *inputs],
                max_completion_tokens=500,
            )
            answer = (response.choices[0].message.content or "").strip()

        if not answer:
            answer = "抱歉，我这次没有生成有效回复，请稍后再试。"

        await self.database.add_chat_message(channel_id, "user", f"{user_name}: {message}")
        await self.database.add_chat_message(channel_id, "assistant", answer)
        return answer
