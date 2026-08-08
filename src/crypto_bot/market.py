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
