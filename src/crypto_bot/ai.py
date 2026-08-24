from __future__ import annotations

from typing import Any

from openai import AsyncOpenAI

from .database import Database


class AIUnavailable(RuntimeError):
    pass


def _display_number(value: object, decimals: int = 4) -> str:
    if not isinstance(value, (int, float)):
        return "暂不可用"
    return f"{value:,.{decimals}f}"


def _display_price(value: object) -> str:
    if not isinstance(value, (int, float)):
        return "暂不可用"
    return f"{value:,.8g}"


def _format_technical(interval: str, technical: dict[str, Any]) -> str:
    parts = [
        f"趋势={technical.get('trend', '样本不足')}",
        f"样本={technical.get('bars', 0)}根",
    ]
    if isinstance(technical.get("window_change_percentage"), (int, float)):
        parts.append(f"样本区间涨跌={technical['window_change_percentage']:+.2f}%")
    if isinstance(technical.get("sma20"), (int, float)):
        parts.append(f"SMA20={_display_price(technical['sma20'])}")
    if isinstance(technical.get("sma50"), (int, float)):
        parts.append(f"SMA50={_display_price(technical['sma50'])}")
    if isinstance(technical.get("rsi14"), (int, float)):
        parts.append(f"RSI14={technical['rsi14']:.2f}")
    if isinstance(technical.get("volume_ratio_20"), (int, float)):
        parts.append(f"最新K线成交额量比={technical['volume_ratio_20']:.2f}x")
    if isinstance(technical.get("support_20"), (int, float)):
        parts.append(f"近20根低点={_display_price(technical['support_20'])}")
    if isinstance(technical.get("resistance_20"), (int, float)):
        parts.append(f"近20根高点={_display_price(technical['resistance_20'])}")
    note = (
        "已排除接口最新一根可能未收线K线"
        if technical.get("latest_bar_excluded")
        else "最新一根可能尚未收线"
    )
    return f"{interval} K线摘要（{note}）：" + "；".join(parts)


def format_market_context(
    market_context: dict[str, dict[str, Any]], market_error: str | None = None
) -> str:
    """Render Gate facts with units and preserve the difference between missing and zero."""
    blocks: list[str] = []
    for item in market_context.values():
        symbol = str(item.get("symbol", "")).upper()
        quote = str(item.get("quote_currency", "USDT")).upper()
        lines = [f"### {symbol}/{quote} · Gate USDT 永续合约"]
        if item.get("data_timestamp"):
            lines.append(f"数据采集时间（UTC）：{item['data_timestamp']}")
        lines.append(f"当前标记价：{_display_price(item.get('current_price'))} {quote}")
        if isinstance(item.get("last_price"), (int, float)):
            lines.append(f"最新成交价：{_display_price(item['last_price'])} {quote}")
        if isinstance(item.get("price_change_percentage_24h"), (int, float)):
            lines.append(f"24h涨跌：{item['price_change_percentage_24h']:+.2f}%")
        if isinstance(item.get("high_24h"), (int, float)) and isinstance(
            item.get("low_24h"), (int, float)
        ):
            lines.append(
                f"24h高/低：{_display_price(item['high_24h'])} / "
                f"{_display_price(item['low_24h'])} {quote}"
            )
        if isinstance(item.get("quote_volume_24h"), (int, float)):
            lines.append(
                f"24h成交额：{_display_number(item['quote_volume_24h'], 2)} {quote}"
            )
        if isinstance(item.get("base_volume_24h"), (int, float)):
            lines.append(
                f"24h基础币成交量：{_display_number(item['base_volume_24h'], 2)} {symbol}"
            )
        if isinstance(item.get("funding_rate"), (int, float)):
            lines.append(f"当前资金费率：{item['funding_rate'] * 100:+.4f}%")

        open_interest = item.get("open_interest_contracts")
        open_interest_usd = item.get("open_interest_usd")
        open_interest_quote = (
            open_interest_usd
            if isinstance(open_interest_usd, (int, float))
            else item.get("open_interest_quote")
        )
        if isinstance(open_interest, (int, float)):
            oi_line = f"全市场未平仓量（OI）：{_display_number(open_interest, 2)} 张"
            if isinstance(open_interest_quote, (int, float)):
                oi_unit = "USD" if isinstance(open_interest_usd, (int, float)) else quote
                oi_line += f"；名义价值={_display_number(open_interest_quote, 2)} {oi_unit}"
            if isinstance(item.get("open_interest_change_percentage"), (int, float)):
                span = int(item.get("open_interest_span_seconds") or 0)
                oi_line += (
                    f"；约{span / 3600:g}小时变化="
                    f"{item['open_interest_change_percentage']:+.2f}%"
                )
            lines.append(oi_line)
        elif isinstance(item.get("contract_total_size"), (int, float)):
            lines.append(
                "合约总规模（ticker total_size，不等同于严格 OI）："
                f"{_display_number(item['contract_total_size'], 2)} 张"
            )
        if isinstance(item.get("long_short_account_ratio"), (int, float)):
            lines.append(f"多空账户比：{item['long_short_account_ratio']:.4f}")
        if isinstance(item.get("long_short_taker_ratio"), (int, float)):
            lines.append(f"主动买卖比：{item['long_short_taker_ratio']:.4f}")

        for interval in ("15m", "4h"):
            technical = item.get(f"technical_{interval}")
            if isinstance(technical, dict) and technical:
                lines.append(_format_technical(interval, technical))
        errors = item.get("partial_errors")
        if isinstance(errors, dict) and errors:
            lines.append("局部缺失项：" + "、".join(str(name) for name in errors))
        blocks.append("\n".join(lines))

    if market_error:
        blocks.append(f"Gate 实时数据警告：{market_error}")
    if not blocks:
        return "本轮没有请求或取得实时行情数据。"
    return "\n\n".join(blocks)


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
        market_error: str | None = None,
    ) -> str:
        if self.client is None:
            raise AIUnavailable("管理员尚未配置 AI_API_KEY，AI 聊天暂不可用。")

        history = await self.database.chat_history(channel_id, limit=10)
        instructions = (
            "你是 Discord 加密货币社区里的友好中文助手。回答简洁、自然，不刷屏；优先"
            "使用短句和紧凑列表，但不得为了简短而省略关键数据、统计周期、数据时间、来源、"
            "触发条件、限制或风险边界。"
            "行情问题只能使用下面由程序刚刚查询的 Gate 实时数据；不得编造价格、新闻、"
            "监管事件或收益保证。已经提供的字段（包括成交额、OI、资金费率、K线摘要）"
            "必须直接用于分析，不得声称自己无法查询；只可具体指出标记为缺失的项目。"
            "用户询问多空或进场建议时，给出条件式技术判断：明确写偏多、偏空或观望，"
            "列出数据依据、触发条件、失效/止损参考和主要风险；不要给保证盈利或无条件"
            "下单指令。区分事实、推测和观点，提醒用户自行研究且不构成投资建议。"
            "不要索要助记词、私钥或交易所密码。\n\n"
            "程序提供的 Gate 行情上下文：\n"
            f"{format_market_context(market_context, market_error)}"
        )
        inputs = [*history, {"role": "user", "content": f"{user_name}: {message}"}]

        if self.api_mode == "responses":
            response = await self.client.responses.create(
                model=self.model,
                instructions=instructions,
                input=inputs,
                max_output_tokens=650,
                store=False,
            )
            answer = response.output_text.strip()
        else:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "system", "content": instructions}, *inputs],
                max_completion_tokens=650,
            )
            answer = (response.choices[0].message.content or "").strip()

        if not answer:
            answer = "抱歉，我这次没有生成有效回复，请稍后再试。"

        await self.database.add_chat_message(channel_id, "user", f"{user_name}: {message}")
        await self.database.add_chat_message(channel_id, "assistant", answer)
        return answer
