# EthuPbot

一个按频道分工的 Discord Bot：使用 Gate USDT 永续合约价格自动更新 BTC/ETH 行情、发送每日同一时刻对比、提供价格提醒和走势图，并在指定聊天频道中进行 AI 对话。

## 已实现功能

- 行情频道：定时编辑同一条 BTC/ETH 消息，避免刷屏。
- 日报频道：按服务器时区生成固定时刻价格对比。
- AI 频道：只在指定频道自动回复；保留最多 10 轮本地短期上下文。
- `/price`：查询 Gate USDT 永续合约（例如 BTC、ETH、KORU、BLESS）。
- `/market`：总市值、成交量、市场涨跌和 BTC/ETH 市占率。
- `/chart`：1、7、30 日 PNG 走势图。
- `/gas`：Ethereum 主网低速、标准和快速 Gas。
- `/alert`、`/alerts`、`/alert_delete`：一次性美元价格提醒。
- `/setup`：管理员配置频道、频率、日报时间和时区。
- 持仓频道：记录多单/空单、数量、入场价和杠杆，按 Gate 标记价查询实时盈亏。
- `/position_channel`、`/position_add`、`/positions`、`/position_delete`：管理持仓收益。
- `/bot_status`、`/chat_clear`：查看状态与清理 AI 上下文。
- SQLite 持久化；密钥仅从环境变量读取。

## 1. 创建 Discord Bot

1. 打开 [Discord Developer Portal](https://discord.com/developers/applications)，创建 Application。
2. 进入 **Bot** 页面，创建 Bot 并复制 Token。
3. 在 **Privileged Gateway Intents** 启用 **Message Content Intent**。
4. 在 **OAuth2 → URL Generator** 选择：
   - Scopes：`bot`、`applications.commands`
   - Bot Permissions：`View Channels`、`Send Messages`、`Embed Links`、`Attach Files`、`Read Message History`
5. 用生成的 URL 将 Bot 邀请进服务器。

不要在聊天、截图或 Git 仓库中公开 Token。若 Token 泄露，立即在 Developer Portal 重置。

## 2. 本地运行

需要 Python 3.11 或更高版本。

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

编辑 `.env`：

```dotenv
DISCORD_TOKEN=你的Discord Bot Token
AI_API_KEY=你的Sub2API Key
AI_BASE_URL=https://你的Sub2API域名/v1
AI_MODEL=你在Sub2API中可用的模型名
AI_API_MODE=chat_completions
COINGECKO_API_KEY=你的CoinGecko Demo API Key
ETHERSCAN_API_KEY=你的Etherscan API Key
```

启动：

```powershell
python -m crypto_bot.main
```

首次启动后，在 Discord 服务器中执行：

```text
/setup market_channel:#行情更新 chat_channel:#聊天频道 position_channel:#持仓收益 daily_channel:#每日行情 update_minutes:10 daily_hour:8 timezone:Asia/Shanghai
```

Bot 会立即开始定时检查。第一份日报只建立基准快照；从第二天开始显示同一播报时刻的日涨跌。

## 3. Docker 运行

```powershell
Copy-Item .env.example .env
docker compose up -d --build
docker compose logs -f bot
```

数据库保存在本地 `data/`，容器重建后仍保留配置、价格提醒和日报快照。

## 指令示例

```text
/price coin:btc
/chart coin:eth days:7
/market
/gas
/alert coin:btc direction:高于 target:120000
/alerts
/alert_delete alert_id:3
/bot_status
/position_channel channel:#持仓收益
/position_add coin:KORU entry_price:0.02 quantity:1000 direction:多单 leverage:10 market:USDT永续合约
/positions
/position_delete position_id:1
```

设置持仓频道后，也可以直接发送：

```text
我在 64000 买了 0.1 个 BTC，10 倍多单
收益多少
```

## 数据与行为说明

- 实时价格、BTC/ETH 日报和默认提醒：Gate USDT 永续合约公开 API（无需 Gate API Key）。
- 整体市场与走势图：CoinGecko Demo API。
- Gas 数据：Etherscan API V2。
- AI：Sub2API，支持 OpenAI 兼容的 Chat Completions 和 Responses 两种模式。
- Discord 短期聊天上下文保存在本地 SQLite，每个频道最多保留 20 条消息。
- 行情播报会编辑原消息；若原消息被删除，Bot 会创建新消息。
- 价格提醒触发一次后自动停用，避免价格反复穿越目标造成刷屏。
- 合约持仓使用标记价计算；实际数量已确定时，杠杆只影响保证金和收益率，不会再次放大盈亏。
- 持仓收益不包含手续费、资金费和滑点；不同用户的持仓记录相互隔离。
- `/positions` 是仅本人可见的临时回复；在持仓频道直接询问时，回复会被频道成员看到。
- Bot 不保存钱包、助记词或私钥，不执行交易，所有内容均不构成投资建议。

## 测试

```powershell
pytest -q
ruff check .
```

不需要真实 API Key 即可运行单元测试。
