from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import discord
from discord import app_commands
from discord.ext import tasks

from .ai import AIUnavailable, ChatService
from .charts import render_price_chart
from .config import Settings
from .database import Database, GuildConfig
from .market import CoinGeckoClient, EtherscanClient, GateClient, MarketError, extract_gate_symbols
from .positions import ParsedPosition, calculate_pnl, parse_position_message

logger = logging.getLogger(__name__)


def money(value: float | None, currency: str = "usd") -> str:
    if value is None:
        return "—"
    symbols = {"usd": "$", "cny": "¥", "eur": "€"}
    prefix = symbols.get(currency.lower(), f"{currency.upper()} ")
    decimals = 2 if abs(value) >= 1 else 6
    return f"{prefix}{value:,.{decimals}f}"


def compact(value: float | None, currency: str = "usd") -> str:
    if value is None:
        return "—"
    for divisor, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if abs(value) >= divisor:
            return f"{money(value / divisor, currency)}{suffix}"
    return money(value, currency)


def percent(value: float | None) -> str:
    if value is None:
        return "—"
    icon = "🟢" if value >= 0 else "🔴"
    return f"{icon} {value:+.2f}%"


class CryptoBot(discord.Client):
    def __init__(self, settings: Settings) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self.settings = settings
        self.tree = app_commands.CommandTree(self)
        self.db = Database(settings.database_path)
        self.market = CoinGeckoClient(settings.coingecko_api_key)
        self.gate = GateClient()
        self.etherscan = EtherscanClient(settings.etherscan_api_key)
        self.chat = ChatService(
            settings.ai_api_key,
            settings.ai_base_url,
            settings.ai_model,
            settings.ai_api_mode,
            self.db,
        )
        self._synced = False
        self._chat_cooldowns: dict[tuple[int, int], datetime] = {}
        self._register_commands()

    async def setup_hook(self) -> None:
        await self.db.initialize()
        await self.market.start()
        await self.gate.start()
        self.scheduler.start()

    async def on_ready(self) -> None:
        if not self._synced:
            await self.tree.sync()
            self._synced = True
        logger.info("Bot 已登录：%s (%s)，服务器数：%s", self.user, self.user.id, len(self.guilds))

    async def close(self) -> None:
        if self.scheduler.is_running():
            self.scheduler.cancel()
        await self.market.close()
        await self.gate.close()
        await super().close()

    async def on_guild_join(self, guild: discord.Guild) -> None:
        await self.db.ensure_guild(guild.id)

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or not message.guild or not message.content.strip():
            return
        config = await self.db.get_guild(message.guild.id)
        if message.channel.id == config.position_channel_id and await self._handle_position_message(
            message
        ):
            return
        if message.channel.id != config.chat_channel_id:
            return

        key = (message.channel.id, message.author.id)
        now = datetime.now(UTC)
        if (last := self._chat_cooldowns.get(key)) and now - last < timedelta(seconds=5):
            return
        self._chat_cooldowns[key] = now

        content = message.content
        if self.user:
            content = content.replace(f"<@{self.user.id}>", "").replace(f"<@!{self.user.id}>", "")
        content = content.strip()[:3000]
        if not content:
            return

        try:
            async with message.channel.typing():
                requested = extract_gate_symbols(content)
                live = await self.gate.coin_markets(requested or ["BTC", "ETH"])
                answer = await self.chat.reply(
                    message.channel.id, message.author.display_name, content, live
                )
            await message.reply(answer[:2000], mention_author=False)
        except AIUnavailable as exc:
            await message.reply(str(exc), mention_author=False)
        except MarketError as exc:
            await message.reply(f"⚠️ {exc}", mention_author=False)
        except Exception:
            logger.exception("AI 聊天失败")
            await message.reply("AI 服务暂时不可用，请稍后再试。", mention_author=False)

    def _register_commands(self) -> None:
        @self.tree.command(name="setup", description="配置行情、日报及 AI 聊天频道（管理员）")
        @app_commands.describe(
            market_channel="自动更新行情的频道",
            chat_channel="AI 自动回复的聊天频道",
            position_channel="持仓收益查询频道",
            daily_channel="每日对比报告频道（留空则使用行情频道）",
            update_minutes="行情更新间隔，5-60 分钟",
            daily_hour="日报小时，按所选时区计算，0-23",
            timezone="IANA 时区，例如 Asia/Shanghai",
        )
        @app_commands.default_permissions(manage_guild=True)
        async def setup_command(
            interaction: discord.Interaction,
            market_channel: discord.TextChannel,
            chat_channel: discord.TextChannel,
            position_channel: discord.TextChannel | None = None,
            daily_channel: discord.TextChannel | None = None,
            update_minutes: app_commands.Range[int, 5, 60] = 10,
            daily_hour: app_commands.Range[int, 0, 23] = 8,
            timezone: str = "Asia/Shanghai",
        ) -> None:
            if not interaction.guild_id:
                await interaction.response.send_message(
                    "该指令只能在服务器中使用。", ephemeral=True
                )
                return
            try:
                ZoneInfo(timezone)
            except ZoneInfoNotFoundError:
                await interaction.response.send_message(
                    "无效时区。示例：Asia/Shanghai、UTC、America/New_York", ephemeral=True
                )
                return
            daily_channel = daily_channel or market_channel
            existing = await self.db.get_guild(interaction.guild_id)
            position_channel_id = (
                position_channel.id if position_channel else existing.position_channel_id
            )
            await self.db.update_guild(
                interaction.guild_id,
                market_channel_id=market_channel.id,
                chat_channel_id=chat_channel.id,
                position_channel_id=position_channel_id,
                daily_channel_id=daily_channel.id,
                update_minutes=update_minutes,
                daily_hour=daily_hour,
                timezone=timezone,
                market_message_id=None,
                last_market_at=None,
            )
            await interaction.response.send_message(
                f"✅ 配置完成\n行情：{market_channel.mention}\n日报：{daily_channel.mention}\n"
                f"聊天：{chat_channel.mention}\n"
                f"持仓：{self._channel_text(position_channel_id)}\n"
                f"更新：每 {update_minutes} 分钟\n"
                f"日报：{daily_hour:02d}:00（{timezone}）",
                ephemeral=True,
            )

        @self.tree.command(name="price", description="查询 Gate 现货或 USDT 永续合约价格")
        @app_commands.describe(coin="Gate 币种，例如 BTC、BLESS、KORU")
        async def price_command(interaction: discord.Interaction, coin: str) -> None:
            await interaction.response.defer()
            try:
                item = await self.gate.ticker(coin)
                embed = self._coin_embed(item, "usdt")
                await interaction.followup.send(embed=embed)
            except MarketError as exc:
                await interaction.followup.send(f"⚠️ {exc}", ephemeral=True)

        @self.tree.command(name="position_channel", description="设置持仓收益查询频道（管理员）")
        @app_commands.default_permissions(manage_guild=True)
        async def position_channel_command(
            interaction: discord.Interaction, channel: discord.TextChannel
        ) -> None:
            if not interaction.guild_id:
                await interaction.response.send_message(
                    "该指令只能在服务器中使用。", ephemeral=True
                )
                return
            await self.db.update_guild(interaction.guild_id, position_channel_id=channel.id)
            await interaction.response.send_message(
                f"✅ 持仓收益频道已设置为 {channel.mention}", ephemeral=True
            )

        @self.tree.command(name="position_add", description="记录一笔持仓")
        @app_commands.describe(
            coin="Gate 币种，例如 BTC、KORU",
            entry_price="开仓均价",
            quantity="标的实际数量，例如 0.1 BTC",
            leverage="杠杆倍数，1-125",
            direction="多单或空单",
            market="现货或 USDT 永续；自动会按杠杆和方向选择",
        )
        @app_commands.choices(
            direction=[
                app_commands.Choice(name="多单", value="long"),
                app_commands.Choice(name="空单", value="short"),
            ],
            market=[
                app_commands.Choice(name="USDT 永续合约", value="futures"),
                app_commands.Choice(name="现货", value="spot"),
            ],
        )
        async def position_add_command(
            interaction: discord.Interaction,
            coin: str,
            entry_price: app_commands.Range[float, 0.0000000001],
            quantity: app_commands.Range[float, 0.0000000001],
            direction: app_commands.Choice[str],
            leverage: app_commands.Range[float, 1, 125] = 1,
            market: app_commands.Choice[str] | None = None,
        ) -> None:
            if not interaction.guild_id:
                await interaction.response.send_message(
                    "该指令只能在服务器中使用。", ephemeral=True
                )
                return
            await interaction.response.defer(ephemeral=True)
            preferred = (
                market.value
                if market
                else ("futures" if leverage > 1 or direction.value == "short" else "auto")
            )
            try:
                parsed = ParsedPosition(
                    coin.upper(), entry_price, quantity, leverage, direction.value
                )
                position_id, item = await self._save_position(
                    interaction.guild_id, interaction.user.id, parsed, preferred
                )
                await interaction.followup.send(
                    self._position_confirmation(position_id, parsed, item), ephemeral=True
                )
            except MarketError as exc:
                await interaction.followup.send(f"⚠️ {exc}", ephemeral=True)

        @self.tree.command(name="positions", description="查询自己的实时持仓收益")
        async def positions_command(interaction: discord.Interaction) -> None:
            if not interaction.guild_id:
                await interaction.response.send_message(
                    "该指令只能在服务器中使用。", ephemeral=True
                )
                return
            await interaction.response.defer(ephemeral=True)
            embed = await self._position_report(interaction.guild_id, interaction.user.id)
            await interaction.followup.send(embed=embed, ephemeral=True)

        @self.tree.command(name="position_delete", description="删除自己记录的一笔持仓")
        async def position_delete_command(
            interaction: discord.Interaction, position_id: int
        ) -> None:
            if not interaction.guild_id:
                await interaction.response.send_message(
                    "该指令只能在服务器中使用。", ephemeral=True
                )
                return
            deleted = await self.db.delete_position(
                position_id, interaction.guild_id, interaction.user.id
            )
            await interaction.response.send_message(
                "✅ 持仓记录已删除。" if deleted else "没有找到属于你的这笔持仓。",
                ephemeral=True,
            )

        @self.tree.command(name="market", description="查看加密货币整体市场概况")
        async def market_command(interaction: discord.Interaction) -> None:
            await interaction.response.defer()
            try:
                data = await self.market.global_market()
                embed = discord.Embed(title="🌐 加密货币市场概况", color=0x5865F2)
                embed.add_field(
                    name="总市值", value=compact(data["total_market_cap"]["usd"]), inline=True
                )
                embed.add_field(
                    name="24h 成交量", value=compact(data["total_volume"]["usd"]), inline=True
                )
                embed.add_field(
                    name="市值 24h",
                    value=percent(data.get("market_cap_change_percentage_24h_usd")),
                    inline=True,
                )
                embed.add_field(
                    name="BTC 占比", value=f"{data['market_cap_percentage']['btc']:.2f}%"
                )
                embed.add_field(
                    name="ETH 占比", value=f"{data['market_cap_percentage']['eth']:.2f}%"
                )
                embed.set_footer(text="数据来源：CoinGecko · 不构成投资建议")
                await interaction.followup.send(embed=embed)
            except MarketError as exc:
                await interaction.followup.send(f"⚠️ {exc}", ephemeral=True)

        @self.tree.command(name="chart", description="生成币种价格走势图")
        @app_commands.describe(coin="币种，例如 BTC", days="1、7 或 30 天")
        async def chart_command(
            interaction: discord.Interaction,
            coin: str,
            days: app_commands.Range[int, 1, 30] = 7,
        ) -> None:
            await interaction.response.defer()
            try:
                if days not in {1, 7, 30}:
                    raise MarketError("天数只能选择 1、7 或 30。")
                resolved = await self.market.resolve_coin(coin)
                points = await self.market.chart(resolved.id, days)
                image = await asyncio.to_thread(render_price_chart, points, resolved.symbol, days)
                await interaction.followup.send(
                    content=f"{resolved.name} 最近 {days} 天走势 · 数据来源：CoinGecko",
                    file=discord.File(image, filename=f"{resolved.symbol.lower()}-{days}d.png"),
                )
            except (MarketError, ValueError) as exc:
                await interaction.followup.send(f"⚠️ {exc}", ephemeral=True)

        @self.tree.command(name="gas", description="查看以太坊主网 Gas 建议")
        async def gas_command(interaction: discord.Interaction) -> None:
            await interaction.response.defer()
            try:
                gas = await self.etherscan.gas_oracle()
                embed = discord.Embed(title="⛽ Ethereum Gas", color=0x627EEA)
                embed.add_field(name="🐢 低速", value=f"{gas['safe']:.3f} Gwei")
                embed.add_field(name="🚗 标准", value=f"{gas['standard']:.3f} Gwei")
                embed.add_field(name="🚀 快速", value=f"{gas['fast']:.3f} Gwei")
                embed.set_footer(text="数据来源：Etherscan")
                await interaction.followup.send(embed=embed)
            except MarketError as exc:
                await interaction.followup.send(f"⚠️ {exc}", ephemeral=True)

        @self.tree.command(name="alert", description="创建一次性价格提醒")
        @app_commands.describe(
            coin="币种，例如 BTC",
            direction="above=高于，below=低于",
            target="美元目标价格",
        )
        @app_commands.choices(
            direction=[
                app_commands.Choice(name="高于", value="above"),
                app_commands.Choice(name="低于", value="below"),
            ]
        )
        async def alert_command(
            interaction: discord.Interaction,
            coin: str,
            direction: app_commands.Choice[str],
            target: app_commands.Range[float, 0.00000001],
        ) -> None:
            if not interaction.guild_id or not interaction.channel_id:
                await interaction.response.send_message(
                    "该指令只能在服务器频道中使用。", ephemeral=True
                )
                return
            await interaction.response.defer(ephemeral=True)
            try:
                resolved = self.gate.resolve_symbol(coin)
                await self.gate.ticker(resolved.symbol)
                alert_id = await self.db.add_alert(
                    interaction.guild_id,
                    interaction.channel_id,
                    interaction.user.id,
                    resolved.id,
                    resolved.symbol,
                    direction.value,
                    target,
                )
                label = "≥" if direction.value == "above" else "≤"
                await interaction.followup.send(
                    f"✅ 提醒 #{alert_id} 已创建：{resolved.symbol} {label} {money(target)}",
                    ephemeral=True,
                )
            except MarketError as exc:
                await interaction.followup.send(f"⚠️ {exc}", ephemeral=True)

        @self.tree.command(name="alerts", description="查看自己的价格提醒")
        async def alerts_command(interaction: discord.Interaction) -> None:
            if not interaction.guild_id:
                await interaction.response.send_message(
                    "该指令只能在服务器中使用。", ephemeral=True
                )
                return
            alerts = await self.db.user_alerts(interaction.guild_id, interaction.user.id)
            if not alerts:
                text = "你目前没有生效中的价格提醒。"
            else:
                text = "\n".join(
                    f"`#{item.id}` {item.coin_symbol} {'≥' if item.direction == 'above' else '≤'} "
                    f"{money(item.target)}"
                    for item in alerts
                )
            await interaction.response.send_message(text, ephemeral=True)

        @self.tree.command(name="alert_delete", description="删除自己的价格提醒")
        async def alert_delete_command(interaction: discord.Interaction, alert_id: int) -> None:
            if not interaction.guild_id:
                await interaction.response.send_message(
                    "该指令只能在服务器中使用。", ephemeral=True
                )
                return
            deleted = await self.db.deactivate_alert(
                alert_id, interaction.guild_id, interaction.user.id
            )
            await interaction.response.send_message(
                "✅ 提醒已删除。" if deleted else "没有找到属于你的生效提醒。", ephemeral=True
            )

        @self.tree.command(name="chat_clear", description="清除当前频道的 AI 短期对话上下文")
        async def chat_clear_command(interaction: discord.Interaction) -> None:
            if not interaction.channel_id:
                return
            await self.db.clear_chat(interaction.channel_id)
            await interaction.response.send_message("✅ AI 对话上下文已清除。", ephemeral=True)

        @self.tree.command(name="bot_status", description="查看 Bot 配置状态")
        async def status_command(interaction: discord.Interaction) -> None:
            if not interaction.guild_id:
                await interaction.response.send_message(
                    "该指令只能在服务器中使用。", ephemeral=True
                )
                return
            config = await self.db.get_guild(interaction.guild_id)
            await interaction.response.send_message(
                f"行情频道：{self._channel_text(config.market_channel_id)}\n"
                f"日报频道：{self._channel_text(config.daily_channel_id)}\n"
                f"聊天频道：{self._channel_text(config.chat_channel_id)}\n"
                f"持仓频道：{self._channel_text(config.position_channel_id)}\n"
                f"行情间隔：{config.update_minutes} 分钟\n"
                f"日报时间：{config.daily_hour:02d}:00（{config.timezone}）\n"
                f"AI：{'已配置' if self.settings.ai_api_key else '未配置'}"
                f"（{self.settings.ai_api_mode}）\n"
                f"Gas：{'已配置' if self.settings.etherscan_api_key else '未配置'}",
                ephemeral=True,
            )

    def _channel_text(self, channel_id: int | None) -> str:
        return f"<#{channel_id}>" if channel_id else "未设置"

    async def _save_position(
        self,
        guild_id: int,
        user_id: int,
        parsed: ParsedPosition,
        market_type: str,
    ) -> tuple[int, dict[str, object]]:
        if not (parsed.entry_price > 0 and parsed.quantity > 0 and 1 <= parsed.leverage <= 125):
            raise MarketError("开仓价和数量必须大于 0，杠杆必须为 1-125 倍。")
        item = await self.gate.ticker(parsed.symbol, market_type)
        position_id = await self.db.add_position(
            guild_id,
            user_id,
            str(item["symbol"]),
            str(item["market_type"]),
            parsed.entry_price,
            parsed.quantity,
            parsed.leverage,
            parsed.direction,
        )
        return position_id, item

    def _position_confirmation(
        self, position_id: int, parsed: ParsedPosition, item: dict[str, object]
    ) -> str:
        side = "多单" if parsed.direction == "long" else "空单"
        market = "USDT 永续" if item["market_type"] == "futures" else "现货"
        return (
            f"✅ 已记录持仓 `#{position_id}`：{parsed.symbol} {side} · {market}\n"
            f"入场 {parsed.entry_price:g} · 数量 {parsed.quantity:g} · {parsed.leverage:g}x\n"
            "发送“收益多少”即可按 Gate 最新行情计算。"
        )

    async def _handle_position_message(self, message: discord.Message) -> bool:
        assert message.guild is not None
        content = message.content.strip()
        if any(word in content for word in ("收益", "盈亏", "持仓")):
            async with message.channel.typing():
                embed = await self._position_report(message.guild.id, message.author.id)
            await message.reply(embed=embed, mention_author=False)
            return True

        position_words = (
            "买",
            "开仓",
            "开多",
            "开空",
            "做多",
            "做空",
            "入场",
            "成本",
            "杠杆",
            "long",
            "short",
        )
        if any(word in content.lower() for word in position_words):
            parsed = parse_position_message(content)
            if parsed is None:
                await message.reply(
                    "我没能完整识别。请按示例发送：`我在 64000 买了 0.1 个 BTC，10 倍多单`，"
                    "或使用 `/position_add`。",
                    mention_author=False,
                )
                return True
            preferred = "futures" if parsed.leverage > 1 or parsed.direction == "short" else "auto"
            try:
                position_id, item = await self._save_position(
                    message.guild.id, message.author.id, parsed, preferred
                )
                await message.reply(
                    self._position_confirmation(position_id, parsed, item), mention_author=False
                )
            except MarketError as exc:
                await message.reply(f"⚠️ {exc}", mention_author=False)
            return True
        return False

    async def _position_report(self, guild_id: int, user_id: int) -> discord.Embed:
        positions = await self.db.user_positions(guild_id, user_id)
        embed = discord.Embed(title="📈 实时持仓收益", color=0x5865F2)
        if not positions:
            embed.description = "你还没有记录持仓。使用 `/position_add` 或直接发送开仓信息。"
            return embed
        for position in positions[:20]:
            try:
                item = await self.gate.ticker(position.symbol, position.asset_type)
                current = float(item["current_price"])
                pnl, margin, roi = calculate_pnl(
                    position.entry_price,
                    current,
                    position.quantity,
                    position.leverage,
                    position.direction,
                )
                side = "多" if position.direction == "long" else "空"
                market = "永续" if position.asset_type == "futures" else "现货"
                quote = str(item.get("quote_currency", "USDT"))
                value = (
                    f"{market} · {side} · {position.leverage:g}x · 数量 {position.quantity:g}\n"
                    f"入场 `{position.entry_price:g}` → 现价 `{current:g}` {quote}\n"
                    f"未实现盈亏 **{pnl:+,.4f} {quote}** · 保证金 `{margin:,.4f}`\n"
                    f"保证金收益率 **{roi:+.2f}%**"
                )
            except MarketError as exc:
                value = f"暂时无法取得行情：{exc}"
            embed.add_field(name=f"#{position.id} · {position.symbol}", value=value, inline=False)
        embed.set_footer(text="Gate 标记价/现货价 · 未计手续费、资金费与滑点 · 不构成投资建议")
        return embed

    def _coin_embed(self, item: dict[str, object], currency: str = "usd") -> discord.Embed:
        change = item.get("price_change_percentage_24h")
        color = 0x5865F2
        if isinstance(change, (int, float)):
            color = 0x16C784 if change >= 0 else 0xEA3943
        is_futures = item.get("market_type") == "futures"
        embed = discord.Embed(
            title=(
                f"{item['name']} ({str(item['symbol']).upper()}) · "
                f"{'USDT 永续合约' if is_futures else '现货'}"
            ),
            description=f"## {money(item.get('current_price'), currency)}",
            color=color,
        )
        if is_futures:
            embed.add_field(name="最新成交价", value=money(item.get("last_price"), currency))
            embed.add_field(name="指数价格", value=money(item.get("index_price"), currency))
            funding = item.get("funding_rate")
            embed.add_field(
                name="资金费率",
                value=f"{float(funding) * 100:+.4f}%" if funding is not None else "—",
            )
        embed.add_field(name="24h", value=percent(change))
        embed.add_field(name="24h 高", value=money(item.get("high_24h"), currency))
        embed.add_field(name="24h 低", value=money(item.get("low_24h"), currency))
        embed.add_field(name="成交量", value=compact(item.get("total_volume"), currency))
        if item.get("image"):
            embed.set_thumbnail(url=str(item["image"]))
        source = "Gate USDT 永续 · 主价格为标记价" if is_futures else "Gate 现货 USDT"
        embed.set_footer(text=f"数据来源：{source} · 不构成投资建议")
        return embed

    def _market_update_embed(
        self, data: dict[str, dict[str, object]], timezone: str
    ) -> discord.Embed:
        local_time = datetime.now(ZoneInfo(timezone))
        embed = discord.Embed(title="📊 BTC / ETH 实时行情", color=0xF3BA2F)
        for coin_id, label in (("bitcoin", "₿ BTC"), ("ethereum", "◆ ETH")):
            item = data[coin_id]
            embed.add_field(
                name=label,
                value=(
                    f"**{money(item.get('current_price'))}**\n"
                    f"24h {percent(item.get('price_change_percentage_24h'))}\n"
                    f"成交量 {compact(item.get('total_volume'))}"
                ),
                inline=True,
            )
        embed.set_footer(
            text=f"更新：{local_time:%Y-%m-%d %H:%M} · Gate 现货 USDT · 不构成投资建议"
        )
        return embed

    @tasks.loop(seconds=60)
    async def scheduler(self) -> None:
        for config in await self.db.list_guilds():
            try:
                await self._update_guild(config)
            except Exception:
                logger.exception("服务器 %s 的定时任务失败", config.guild_id)
        await self._check_alerts()

    @scheduler.before_loop
    async def before_scheduler(self) -> None:
        await self.wait_until_ready()

    async def _update_guild(self, config: GuildConfig) -> None:
        now_utc = datetime.now(UTC)
        due_market = config.market_channel_id and (
            not config.last_market_at
            or now_utc - datetime.fromisoformat(config.last_market_at)
            >= timedelta(minutes=config.update_minutes)
        )
        if due_market:
            await self._post_market(config)

        local_now = now_utc.astimezone(ZoneInfo(config.timezone))
        today = local_now.date().isoformat()
        if (
            config.daily_channel_id
            and local_now.hour >= config.daily_hour
            and config.last_daily_date != today
        ):
            await self._post_daily(config, today)

    async def _post_market(self, config: GuildConfig) -> None:
        channel = self.get_channel(config.market_channel_id or 0)
        if not isinstance(channel, discord.TextChannel):
            return
        data = await self.gate.coin_markets(["bitcoin", "ethereum"])
        if len(data) != 2:
            raise MarketError("BTC/ETH 行情数据不完整。")
        embed = self._market_update_embed(data, config.timezone)
        message: discord.Message | None = None
        if config.market_message_id:
            try:
                message = await channel.fetch_message(config.market_message_id)
                await message.edit(embed=embed)
            except discord.NotFound:
                message = None
        if message is None:
            message = await channel.send(embed=embed)
        await self.db.update_guild(
            config.guild_id,
            market_message_id=message.id,
            last_market_at=datetime.now(UTC).isoformat(),
        )

    async def _post_daily(self, config: GuildConfig, today: str) -> None:
        channel = self.get_channel(config.daily_channel_id or 0)
        if not isinstance(channel, discord.TextChannel):
            return
        data = await self.gate.coin_markets(["bitcoin", "ethereum"])
        prices = {coin_id: float(item["current_price"]) for coin_id, item in data.items()}
        previous = await self.db.previous_snapshot(config.guild_id, today)
        embed = discord.Embed(title="📅 每日行情对比", color=0x5865F2)
        for coin_id, symbol in (("bitcoin", "BTC"), ("ethereum", "ETH")):
            current = prices[coin_id]
            if previous and coin_id in previous[1]:
                old = previous[1][coin_id]
                change = (current / old - 1) * 100
                value = f"今日：**{money(current)}**\n上次：{money(old)}\n涨跌：{percent(change)}"
            else:
                value = f"今日：**{money(current)}**\n首次记录，明日起生成对比"
            embed.add_field(name=symbol, value=value, inline=True)
        comparison_date = previous[0] if previous else "无历史快照"
        embed.set_footer(text=f"对比基准：{comparison_date} · Gate 现货 USDT · 不构成投资建议")
        await channel.send(embed=embed)
        await self.db.save_snapshot(config.guild_id, today, prices)
        await self.db.update_guild(config.guild_id, last_daily_date=today)

    async def _check_alerts(self) -> None:
        alerts = await self.db.active_alerts()
        if not alerts:
            return
        coin_ids = list({item.coin_id for item in alerts})
        try:
            data = await self.gate.coin_markets(coin_ids)
        except MarketError:
            logger.warning("价格提醒行情查询失败", exc_info=True)
            return
        for alert in alerts:
            item = data.get(alert.coin_id)
            if not item:
                continue
            current = float(item["current_price"])
            triggered = (alert.direction == "above" and current >= alert.target) or (
                alert.direction == "below" and current <= alert.target
            )
            if not triggered:
                continue
            channel = self.get_channel(alert.channel_id)
            if isinstance(channel, discord.TextChannel):
                label = "已突破" if alert.direction == "above" else "已跌至"
                await channel.send(
                    f"🚨 <@{alert.user_id}> **{alert.coin_symbol} 价格提醒**\n"
                    f"{label} {money(alert.target)}，当前价格 **{money(current)}**\n"
                    "数据来源：Gate 现货 USDT · 不构成投资建议",
                    allowed_mentions=discord.AllowedMentions(users=True),
                )
            await self.db.deactivate_alert(alert.id, alert.guild_id)
