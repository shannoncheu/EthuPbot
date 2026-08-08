from __future__ import annotations

import re
from dataclasses import dataclass
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


class GateClient:
    """Gate public spot-market client. No API key is required."""

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

    async def ticker(self, query: str) -> dict[str, Any]:
        coin = self.resolve_symbol(query)
        pair = f"{coin.symbol}_USDT"
        data = await self._get("/spot/tickers", {"currency_pair": pair, "timezone": "utc0"})
        if not data:
            raise MarketError(f"Gate 现货没有找到 {pair} 交易对。")
        item = data[0]

        def number(key: str) -> float | None:
            value = item.get(key)
            return float(value) if value not in {None, ""} else None

        return {
            "id": coin.id,
            "symbol": coin.symbol,
            "name": coin.name,
            "currency_pair": pair,
            "quote_currency": "USDT",
            "current_price": number("last"),
            "price_change_percentage_24h": number("change_percentage"),
            "high_24h": number("high_24h"),
            "low_24h": number("low_24h"),
            "total_volume": number("quote_volume"),
        }

    async def coin_markets(self, coins: list[str]) -> dict[str, dict[str, Any]]:
        results: dict[str, dict[str, Any]] = {}
        for query in dict.fromkeys(coins):
            try:
                results[query] = await self.ticker(query)
            except MarketError:
                if len(coins) == 1:
                    raise
        return results


def extract_gate_symbols(message: str) -> list[str]:
    """Extract explicit symbols from natural-language price questions."""
    matches = re.findall(
        r"(?i)(?<![a-z0-9])([a-z][a-z0-9]{1,29})"
        r"(?=(?:(?:现在|目前)?的?)?(?:价格|行情|多少钱))",
        message,
    )
    return list(dict.fromkeys(symbol.upper() for symbol in matches))[:5]


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
