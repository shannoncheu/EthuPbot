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
            await self.db.update_guild(
                interaction.guild_id,
                market_channel_id=market_channel.id,
                chat_channel_id=chat_channel.id,
                daily_channel_id=daily_channel.id,
                update_minutes=update_minutes,
                daily_hour=daily_hour,
                timezone=timezone,
                market_message_id=None,
                last_market_at=None,
            )
            await interaction.response.send_message(
                f"✅ 配置完成\n行情：{market_channel.mention}\n日报：{daily_channel.mention}\n"
                f"聊天：{chat_channel.mention}\n更新：每 {update_minutes} 分钟\n"
                f"日报：{daily_hour:02d}:00（{timezone}）",
                ephemeral=True,
            )

        @self.tree.command(name="price", description="查询 Gate 现货 USDT 实时价格")
        @app_commands.describe(coin="Gate 币种，例如 BTC、BLESS、KORU")
        async def price_command(
            interaction: discord.Interaction, coin: str
        ) -> None:
            await interaction.response.defer()
            try:
                item = await self.gate.ticker(coin)
                embed = self._coin_embed(item, "usdt")
                await interaction.followup.send(embed=embed)
            except MarketError as exc:
                await interaction.followup.send(f"⚠️ {exc}", ephemeral=True)

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
                resolved = self.gate.resolve_symbol(coin)
                await self.gate.ticker(resolved.symbol)
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
                resolved = await self.market.resolve_coin(coin)
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
                f"行情间隔：{config.update_minutes} 分钟\n"
                f"日报时间：{config.daily_hour:02d}:00（{config.timezone}）\n"
                f"AI：{'已配置' if self.settings.ai_api_key else '未配置'}"
                f"（{self.settings.ai_api_mode}）\n"
                f"Gas：{'已配置' if self.settings.etherscan_api_key else '未配置'}",
                ephemeral=True,
            )

    def _channel_text(self, channel_id: int | None) -> str:
        return f"<#{channel_id}>" if channel_id else "未设置"

    def _coin_embed(self, item: dict[str, object], currency: str = "usd") -> discord.Embed:
        change = item.get("price_change_percentage_24h")
        color = 0x16C784 if isinstance(change, (int, float)) and change >= 0 else 0xEA3943
        embed = discord.Embed(
            title=f"{item['name']} ({str(item['symbol']).upper()})",
            description=f"## {money(item.get('current_price'), currency)}",
            color=color,
        )
        embed.add_field(name="24h", value=percent(change))
        embed.add_field(name="24h 高", value=money(item.get("high_24h"), currency))
        embed.add_field(name="24h 低", value=money(item.get("low_24h"), currency))
        embed.add_field(name="成交量", value=compact(item.get("total_volume"), currency))
        if item.get("image"):
            embed.set_thumbnail(url=str(item["image"]))
        embed.set_footer(text="数据来源：Gate 现货（USDT）· 不构成投资建议")
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
