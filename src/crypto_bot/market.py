from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from statistics import fmean
from typing import Any

import aiohttp


class MarketError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Coin:
    id: str
    symbol: str
    name: str


COMMON_COINS: dict[str, Coin] = {
    "btc": Coin("bitcoin", "BTC", "Bitcoin"),
    "bitcoin": Coin("bitcoin", "BTC", "Bitcoin"),
    "eth": Coin("ethereum", "ETH", "Ethereum"),
    "ethereum": Coin("ethereum", "ETH", "Ethereum"),
    "sol": Coin("solana", "SOL", "Solana"),
    "solana": Coin("solana", "SOL", "Solana"),
    "bnb": Coin("binancecoin", "BNB", "BNB"),
    "xrp": Coin("ripple", "XRP", "XRP"),
    "doge": Coin("dogecoin", "DOGE", "Dogecoin"),
    "ada": Coin("cardano", "ADA", "Cardano"),
}

GATE_SYMBOL_ALIASES = {
    "bitcoin": "BTC",
    "ethereum": "ETH",
    "solana": "SOL",
    "binancecoin": "BNB",
    "ripple": "XRP",
    "dogecoin": "DOGE",
    "cardano": "ADA",
}

MARKET_QUESTION_WORDS = (
    "价格",
    "现价",
    "多少钱",
    "行情",
    "走势",
    "k线",
    "k-line",
    "kline",
    "成交量",
    "交易量",
    "持仓量",
    "持仓",
    "oi",
    "open interest",
    "资金费率",
    "funding",
    "支撑",
    "压力",
    "阻力",
    "趋势",
    "多单",
    "空单",
    "做多",
    "做空",
    "进场",
    "入场",
    "适合",
    "建议",
    "分析",
    "怎么看",
    "怎么样",
    "能买吗",
    "买吗",
    "能不能进",
    "可以进",
    "买入",
    "卖出",
    "price",
    "volume",
    "long",
    "short",
    "analyze",
    "analysis",
    "outlook",
)

GATE_SYMBOL_STOP_WORDS = {
    "A",
    "ABOUT",
    "AI",
    "AND",
    "ANALYZE",
    "ANALYSIS",
    "ARE",
    "BUY",
    "CAN",
    "FUNDING",
    "GATE",
    "HOW",
    "H",
    "I",
    "INTEREST",
    "IS",
    "KLINE",
    "K",
    "LONG",
    "MARKET",
    "M",
    "NOW",
    "OF",
    "OI",
    "OPEN",
    "OR",
    "OUTLOOK",
    "PLEASE",
    "PRICE",
    "RATE",
    "SELL",
    "SHORT",
    "SHOULD",
    "THE",
    "USDT",
    "USD",
    "VOLUME",
    "WHAT",
    "YOU",
}


def is_market_question(message: str) -> bool:
    lowered = message.lower()
    return any(word in lowered for word in MARKET_QUESTION_WORDS)


def _valid_gate_symbol(token: str, *, explicit: bool = False) -> bool:
    return bool(
        token
        and (explicit or token not in GATE_SYMBOL_STOP_WORDS)
        and not token.isdigit()
        and re.fullmatch(r"[A-Z0-9]{1,30}", token)
        and re.search(r"[A-Z]", token)
    )


def extract_gate_symbols(message: str) -> list[str]:
    """Extract Gate symbols from pairs, tickers and natural-language market questions."""
    upper = message.upper()
    symbols: list[str] = []

    def add(token: str, *, explicit: bool = False) -> None:
        normalized = token.upper().removesuffix("_USDT")
        if _valid_gate_symbol(normalized, explicit=explicit) and normalized not in symbols:
            symbols.append(normalized)

    for match in re.finditer(
        r"(?<![A-Z0-9])([A-Z0-9]{1,30})\s*[/_-]\s*USDT(?![A-Z0-9])",
        upper,
    ):
        add(match.group(1), explicit=True)
    for match in re.finditer(r"(?<![A-Z0-9])\$([A-Z0-9]{1,30})(?![A-Z0-9])", upper):
        add(match.group(1), explicit=True)
    for match in re.finditer(
        r"(?:那|那么|再看|换成)?\s*([A-Z][A-Z0-9]{0,29})\s*(?:呢|怎么样|如何)[?？]?",
        upper,
    ):
        add(match.group(1), explicit=True)

    if is_market_question(message):
        for match in re.finditer(r"(?<![A-Z0-9])([A-Z][A-Z0-9]{0,29})(?![A-Z0-9])", upper):
            add(match.group(1))
    return symbols[:3]


def _rsi(closes: list[float], period: int = 14) -> float | None:
    if len(closes) <= period:
        return None
    changes = [
        current - previous
        for previous, current in zip(closes, closes[1:], strict=False)
    ]
    initial = changes[:period]
    average_gain = fmean(max(change, 0.0) for change in initial)
    average_loss = fmean(max(-change, 0.0) for change in initial)
    for change in changes[period:]:
        average_gain = (average_gain * (period - 1) + max(change, 0.0)) / period
        average_loss = (average_loss * (period - 1) + max(-change, 0.0)) / period
    if average_loss == 0:
        return 50.0 if average_gain == 0 else 100.0
    relative_strength = average_gain / average_loss
    return 100 - 100 / (1 + relative_strength)


def summarize_candlesticks(candles: list[dict[str, float]]) -> dict[str, float | str | int]:
    """Build compact, deterministic indicators for the AI prompt."""
    ordered = sorted(candles, key=lambda item: item["time"])
    if len(ordered) < 2:
        return {}
    closes = [item["close"] for item in ordered]
    recent = ordered[-20:]
    sma20 = fmean(closes[-20:]) if len(closes) >= 20 else None
    sma50 = fmean(closes[-50:]) if len(closes) >= 50 else None
    latest = closes[-1]
    trend = "方向混合"
    if sma20 is not None and sma50 is not None:
        if latest > sma20 > sma50:
            trend = "多头排列"
        elif latest < sma20 < sma50:
            trend = "空头排列"
        elif latest >= sma20:
            trend = "价格位于SMA20上方，但均线未形成多头排列"
        else:
            trend = "价格位于SMA20下方，但均线未形成空头排列"
    elif sma20 is not None:
        trend = "价格位于SMA20上方" if latest >= sma20 else "价格位于SMA20下方"

    quote_volumes = [item.get("quote_volume", 0.0) for item in ordered]
    previous_volumes = quote_volumes[-21:-1]
    average_volume = fmean(previous_volumes) if previous_volumes else 0.0
    volume_ratio = quote_volumes[-1] / average_volume if average_volume > 0 else None
    return {
        "bars": len(ordered),
        "last_timestamp": int(ordered[-1]["time"]),
        "window_change_percentage": (latest / closes[0] - 1) * 100,
        "sma20": sma20,
        "sma50": sma50,
        "rsi14": _rsi(closes),
        "support_20": min(item["low"] for item in recent),
        "resistance_20": max(item["high"] for item in recent),
        "latest_quote_volume": quote_volumes[-1],
        "volume_ratio_20": volume_ratio,
        "trend": trend,
    }


class GateClient:
    """Gate public spot and USDT perpetual market client."""

    def __init__(self) -> None:
        self.base_url = "https://api.gateio.ws/api/v4"
        self.session: aiohttp.ClientSession | None = None

    async def start(self) -> None:
        if self.session is None or self.session.closed:
            timeout = aiohttp.ClientTimeout(total=15)
            self.session = aiohttp.ClientSession(
                headers={"accept": "application/json"}, timeout=timeout
            )

    async def close(self) -> None:
        if self.session and not self.session.closed:
            await self.session.close()

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        await self.start()
        assert self.session is not None
        try:
            async with self.session.get(f"{self.base_url}{path}", params=params) as response:
                if response.status == 429:
                    raise MarketError("Gate 请求过于频繁，请稍后再试。")
                if response.status >= 400:
                    raise MarketError(f"Gate 返回 HTTP {response.status}")
                return await response.json()
        except TimeoutError as exc:
            raise MarketError("Gate 行情服务响应超时。") from exc
        except aiohttp.ClientError as exc:
            raise MarketError("暂时无法连接 Gate 行情服务。") from exc

    def resolve_symbol(self, query: str) -> Coin:
        normalized = query.strip().lower()
        symbol = GATE_SYMBOL_ALIASES.get(normalized, normalized.upper())
        if symbol.endswith("_USDT"):
            symbol = symbol[:-5]
        if not re.fullmatch(r"[A-Z0-9]{1,30}", symbol):
            raise MarketError("币种格式不正确，请输入 Gate 交易代码，例如 BTC 或 BLESS。")
        name = COMMON_COINS.get(normalized, Coin(symbol, symbol, symbol)).name
        return Coin(symbol, symbol, name)

    @staticmethod
    def _number(item: dict[str, Any], key: str) -> float | None:
        value = item.get(key)
        if value in {None, ""}:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    async def _spot_ticker(self, coin: Coin) -> dict[str, Any]:
        pair = f"{coin.symbol}_USDT"
        data = await self._get("/spot/tickers", {"currency_pair": pair, "timezone": "utc0"})
        if not data:
            raise MarketError(f"Gate 现货没有找到 {pair} 交易对。")
        item = data[0]
        return {
            "id": coin.id,
            "symbol": coin.symbol,
            "name": coin.name,
            "currency_pair": pair,
            "market_type": "spot",
            "quote_currency": "USDT",
            "current_price": self._number(item, "last"),
            "last_price": self._number(item, "last"),
            "price_change_percentage_24h": self._number(item, "change_percentage"),
            "high_24h": self._number(item, "high_24h"),
            "low_24h": self._number(item, "low_24h"),
            "quote_volume_24h": self._number(item, "quote_volume"),
            "base_volume_24h": self._number(item, "base_volume"),
            "total_volume": self._number(item, "quote_volume"),
        }

    async def _futures_ticker(self, coin: Coin) -> dict[str, Any]:
        contract = f"{coin.symbol}_USDT"
        data = await self._get("/futures/usdt/tickers", {"contract": contract})
        if not data:
            raise MarketError(f"Gate 合约没有找到 {contract}。")
        item = data[0]
        mark_price = self._number(item, "mark_price")
        last_price = self._number(item, "last")
        return {
            "id": coin.id,
            "symbol": coin.symbol,
            "name": coin.name,
            "currency_pair": contract,
            "market_type": "futures",
            "quote_currency": "USDT",
            "current_price": mark_price or last_price,
            "mark_price": mark_price,
            "last_price": last_price,
            "index_price": self._number(item, "index_price"),
            "funding_rate": self._number(item, "funding_rate"),
            "price_change_percentage_24h": self._number(item, "change_percentage"),
            "high_24h": self._number(item, "high_24h"),
            "low_24h": self._number(item, "low_24h"),
            "quote_volume_24h": (
                self._number(item, "volume_24h_quote")
                or self._number(item, "volume_24h_settle")
            ),
            "base_volume_24h": self._number(item, "volume_24h_base"),
            "contract_volume_24h": self._number(item, "volume_24h"),
            "contract_total_size": self._number(item, "total_size"),
            "best_bid": self._number(item, "highest_bid"),
            "best_ask": self._number(item, "lowest_ask"),
            "total_volume": self._number(item, "volume_24h_quote"),
        }

    async def _futures_candles(
        self, coin: Coin, interval: str, limit: int = 60
    ) -> list[dict[str, float]]:
        contract = f"{coin.symbol}_USDT"
        data = await self._get(
            "/futures/usdt/candlesticks",
            {"contract": contract, "interval": interval, "limit": limit},
        )
        if not isinstance(data, list):
            raise MarketError(f"Gate 返回的 {contract} {interval} K 线格式不正确。")
        candles: list[dict[str, float]] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            try:
                candles.append(
                    {
                        "time": float(item["t"]),
                        "open": float(item["o"]),
                        "high": float(item["h"]),
                        "low": float(item["l"]),
                        "close": float(item["c"]),
                        "contract_volume": float(item.get("v") or 0),
                        "quote_volume": float(item.get("sum") or 0),
                    }
                )
            except (KeyError, TypeError, ValueError):
                continue
        candles.sort(key=lambda item: item["time"])
        if len(candles) < 2:
            raise MarketError(f"Gate 暂无足够的 {contract} {interval} K 线。")
        return candles

    async def _futures_statistics(self, coin: Coin) -> dict[str, Any]:
        contract = f"{coin.symbol}_USDT"
        data: Any = None
        interval = "1h"
        last_error: MarketError | None = None
        for interval, limit in (("1h", 25), ("5m", 13)):
            try:
                data = await self._get(
                    "/futures/usdt/contract_stats",
                    {"contract": contract, "interval": interval, "limit": limit},
                )
                if isinstance(data, list) and data:
                    break
            except MarketError as exc:
                last_error = exc
        if not isinstance(data, list) or not data:
            if last_error is not None:
                raise last_error
            raise MarketError(f"Gate 暂无 {contract} 持仓量统计。")

        samples: list[dict[str, Any]] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            timestamp = self._number(item, "time")
            open_interest = self._number(item, "open_interest")
            if timestamp is not None and open_interest is not None:
                samples.append(item)
        samples.sort(key=lambda item: float(item["time"]))
        if not samples:
            raise MarketError(f"Gate 返回的 {contract} 持仓量统计格式不正确。")

        latest = samples[-1]
        open_interest_usd = self._number(latest, "open_interest_usd")
        result: dict[str, Any] = {
            "open_interest_contracts": self._number(latest, "open_interest"),
            "open_interest_usd": open_interest_usd,
            "open_interest_quote": open_interest_usd,
            "open_interest_timestamp": int(float(latest["time"])),
            "open_interest_interval": interval,
            "long_short_account_ratio": self._number(latest, "lsr_account"),
            "long_short_taker_ratio": self._number(latest, "lsr_taker"),
            "top_long_short_account_ratio": self._number(latest, "top_lsr_account"),
            "top_long_short_size_ratio": self._number(latest, "top_lsr_size"),
        }
        if len(samples) >= 2:
            baseline = samples[0]
            first_oi = self._number(baseline, "open_interest")
            latest_oi = result["open_interest_contracts"]
            if first_oi is not None and first_oi > 0 and latest_oi is not None:
                result["open_interest_change_percentage"] = (
                    (latest_oi - first_oi) / first_oi * 100
                )
                result["open_interest_span_seconds"] = int(
                    float(latest["time"]) - float(baseline["time"])
                )
        return result

    async def market_analysis(self, query: str) -> dict[str, Any]:
        """Fetch a ticker plus optional OI and multi-timeframe technical context."""
        coin = self.resolve_symbol(query)
        ticker = await self._futures_ticker(coin)
        components = await asyncio.gather(
            self._futures_candles(coin, "15m"),
            self._futures_candles(coin, "4h"),
            self._futures_statistics(coin),
            return_exceptions=True,
        )
        errors: dict[str, str] = {}
        for name, value in zip(
            ("kline_15m", "kline_4h", "open_interest"), components, strict=True
        ):
            if isinstance(value, BaseException):
                errors[name] = str(value)
                continue
            if name == "open_interest":
                ticker.update(value)
            else:
                completed = value[:-1] if len(value) > 2 else value
                technical = summarize_candlesticks(completed)
                technical["latest_bar_excluded"] = len(value) > 2
                ticker[f"technical_{name.removeprefix('kline_')}"] = technical
        ticker["data_timestamp"] = datetime.now(UTC).isoformat()
        if errors:
            ticker["partial_errors"] = errors
        return ticker

    async def market_analyses(self, coins: list[str]) -> dict[str, dict[str, Any]]:
        results: dict[str, dict[str, Any]] = {}
        first_error: MarketError | None = None
        unique = list(dict.fromkeys(coins))[:3]
        for query in unique:
            try:
                item = await self.market_analysis(query)
                results[str(item["symbol"])] = item
            except MarketError as exc:
                first_error = first_error or exc
        if not results and first_error is not None:
            raise first_error
        return results

    async def ticker(self, query: str, market_type: str = "futures") -> dict[str, Any]:
        coin = self.resolve_symbol(query)
        if market_type == "spot":
            return await self._spot_ticker(coin)
        if market_type == "futures":
            return await self._futures_ticker(coin)
        if market_type != "auto":
            raise ValueError("market_type must be auto, spot or futures")
        try:
            return await self._spot_ticker(coin)
        except MarketError as spot_error:
            if "HTTP 400" not in str(spot_error) and "没有找到" not in str(spot_error):
                raise
            try:
                return await self._futures_ticker(coin)
            except MarketError as exc:
                raise MarketError(f"Gate 现货和 USDT 永续合约都没有找到 {coin.symbol}。") from exc

    async def coin_markets(self, coins: list[str]) -> dict[str, dict[str, Any]]:
        results: dict[str, dict[str, Any]] = {}
        for query in dict.fromkeys(coins):
            try:
                results[query] = await self.ticker(query)
            except MarketError:
                if len(coins) == 1:
                    raise
        return results
class CoinGeckoClient:
    def __init__(self, api_key: str | None = None) -> None:
        self.base_url = "https://api.coingecko.com/api/v3"
        self.headers = {"accept": "application/json"}
        if api_key:
            self.headers["x-cg-demo-api-key"] = api_key
        self.session: aiohttp.ClientSession | None = None

    async def __aenter__(self) -> CoinGeckoClient:
        await self.start()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def start(self) -> None:
        if self.session is None or self.session.closed:
            timeout = aiohttp.ClientTimeout(total=15)
            self.session = aiohttp.ClientSession(headers=self.headers, timeout=timeout)

    async def close(self) -> None:
        if self.session and not self.session.closed:
            await self.session.close()

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        await self.start()
        assert self.session is not None
        try:
            async with self.session.get(f"{self.base_url}{path}", params=params) as response:
                if response.status == 429:
                    raise MarketError("CoinGecko 请求过于频繁，请稍后再试。")
                if response.status >= 400:
                    raise MarketError(f"CoinGecko 返回 HTTP {response.status}")
                return await response.json()
        except TimeoutError as exc:
            raise MarketError("行情服务响应超时。") from exc
        except aiohttp.ClientError as exc:
            raise MarketError("暂时无法连接行情服务。") from exc

    async def resolve_coin(self, query: str) -> Coin:
        normalized = query.strip().lower()
        if normalized in COMMON_COINS:
            return COMMON_COINS[normalized]
        if not re.fullmatch(r"[a-zA-Z0-9._-]{1,40}", normalized):
            raise MarketError("币种名称格式不正确。")
        data = await self._get("/search", {"query": normalized})
        coins = data.get("coins", [])
        if not coins:
            raise MarketError(f"找不到币种：{query}")
        exact = next(
            (item for item in coins if item.get("symbol", "").lower() == normalized),
            coins[0],
        )
        return Coin(exact["id"], exact["symbol"].upper(), exact["name"])

    async def coin_markets(
        self, coin_ids: list[str], currency: str = "usd"
    ) -> dict[str, dict[str, Any]]:
        if not coin_ids:
            return {}
        data = await self._get(
            "/coins/markets",
            {
                "vs_currency": currency.lower(),
                "ids": ",".join(dict.fromkeys(coin_ids)),
                "price_change_percentage": "1h,24h,7d",
                "sparkline": "false",
            },
        )
        return {item["id"]: item for item in data}

    async def global_market(self) -> dict[str, Any]:
        data = await self._get("/global")
        return data["data"]

    async def chart(self, coin_id: str, days: int, currency: str = "usd") -> list[list[float]]:
        if days not in {1, 7, 30}:
            raise MarketError("走势图仅支持 1、7 或 30 天。")
        data = await self._get(
            f"/coins/{coin_id}/market_chart",
            {"vs_currency": currency.lower(), "days": days},
        )
        return data["prices"]


class EtherscanClient:
    def __init__(self, api_key: str | None) -> None:
        self.api_key = api_key

    async def gas_oracle(self) -> dict[str, float]:
        if not self.api_key:
            raise MarketError("管理员尚未配置 ETHERSCAN_API_KEY。")
        params = {
            "chainid": "1",
            "module": "gastracker",
            "action": "gasoracle",
            "apikey": self.api_key,
        }
        timeout = aiohttp.ClientTimeout(total=15)
        try:
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.get("https://api.etherscan.io/v2/api", params=params) as response,
            ):
                data = await response.json()
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise MarketError("暂时无法连接 Etherscan。") from exc
        if response.status >= 400 or data.get("status") != "1":
            raise MarketError(f"Etherscan 查询失败：{data.get('message', response.status)}")
        result = data["result"]
        return {
            "safe": float(result["SafeGasPrice"]),
            "standard": float(result["ProposeGasPrice"]),
            "fast": float(result["FastGasPrice"]),
            "base": float(result["suggestBaseFee"]),
        }
