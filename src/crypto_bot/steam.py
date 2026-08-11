from __future__ import annotations

import asyncio
import html
import json
import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from typing import Any

import aiohttp


class SteamError(RuntimeError):
    pass


REVIEW_SCORES = {
    "差评如潮": 1,
    "特别差评": 2,
    "多半差评": 3,
    "褒贬不一": 5,
    "多半好评": 6,
    "特别好评": 8,
    "好评如潮": 9,
}


@dataclass(frozen=True, slots=True)
class SteamDeal:
    app_id: int
    name: str
    url: str
    image_url: str | None
    discount_percent: int
    original_price: str
    final_price: str
    review_score: int = 0
    review_label: str = "暂无评价"
    total_positive: int = 0
    total_reviews: int = 0

    @property
    def positive_percent(self) -> int | None:
        if self.total_reviews <= 0:
            return None
        return round(self.total_positive / self.total_reviews * 100)

    @property
    def is_overwhelmingly_positive(self) -> bool:
        return self.review_score == 9

    def is_hot(self, minimum_reviews: int = 20_000) -> bool:
        return self.total_reviews >= minimum_reviews


class _SteamSearchParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.deals: list[SteamDeal] = []
        self.current: dict[str, Any] | None = None
        self.capture_tag: str | None = None
        self.capture_key: str | None = None
        self.capture_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key: value or "" for key, value in attrs}
        classes = set(attributes.get("class", "").split())
        if tag == "a" and "search_result_row" in classes:
            app_id = attributes.get("data-ds-appid", "")
            if app_id.isdigit():
                self.current = {
                    "app_id": int(app_id),
                    "image_url": None,
                    "review_score": 0,
                    "review_label": "暂无评价",
                    "total_positive": 0,
                    "total_reviews": 0,
                }
            return
        if self.current is None:
            return
        if tag == "img" and "src" in attributes and not self.current.get("image_url"):
            source = attributes["src"]
            self.current["image_url"] = f"https:{source}" if source.startswith("//") else source
        capture_classes = {
            "title": "name",
            "discount_pct": "discount_percent_text",
            "discount_original_price": "original_price",
            "discount_final_price": "final_price",
        }
        for class_name, key in capture_classes.items():
            if class_name in classes:
                self.capture_tag = tag
                self.capture_key = key
                self.capture_text = []
                break
        if tag == "span" and "search_review_summary" in classes:
            self._parse_review_tooltip(attributes.get("data-tooltip-html", ""))

    def handle_data(self, data: str) -> None:
        if self.current is not None and self.capture_key:
            self.capture_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self.current is None:
            return
        if self.capture_tag == tag and self.capture_key:
            self.current[self.capture_key] = " ".join(self.capture_text).strip()
            self.capture_tag = None
            self.capture_key = None
            self.capture_text = []
        if tag == "a":
            self._finish_current()

    def _parse_review_tooltip(self, tooltip: str) -> None:
        assert self.current is not None
        decoded = html.unescape(tooltip)
        parts = re.split(r"<br\s*/?>", decoded, maxsplit=1, flags=re.IGNORECASE)
        label = re.sub(r"<[^>]+>", "", parts[0]).strip()
        if label:
            self.current["review_label"] = label
            self.current["review_score"] = REVIEW_SCORES.get(label, 0)
        match = re.search(r"([\d,]+)\s*篇.*?(\d+)%", decoded)
        if match:
            total = int(match.group(1).replace(",", ""))
            positive_percent = int(match.group(2))
            self.current["total_reviews"] = total
            self.current["total_positive"] = round(total * positive_percent / 100)

    def _finish_current(self) -> None:
        assert self.current is not None
        raw_discount = str(self.current.get("discount_percent_text", ""))
        discount_match = re.search(r"\d+", raw_discount)
        required = (
            self.current.get("name"),
            self.current.get("original_price"),
            self.current.get("final_price"),
        )
        if discount_match and all(required):
            app_id = int(self.current["app_id"])
            self.deals.append(
                SteamDeal(
                    app_id=app_id,
                    name=str(self.current["name"]),
                    url=f"https://store.steampowered.com/app/{app_id}/",
                    image_url=self.current.get("image_url"),
                    discount_percent=int(discount_match.group()),
                    original_price=str(self.current["original_price"]),
                    final_price=str(self.current["final_price"]),
                    review_score=int(self.current["review_score"]),
                    review_label=str(self.current["review_label"]),
                    total_positive=int(self.current["total_positive"]),
                    total_reviews=int(self.current["total_reviews"]),
                )
            )
        self.current = None
        self.capture_tag = None
        self.capture_key = None
        self.capture_text = []


def parse_search_results(results_html: str) -> list[SteamDeal]:
    parser = _SteamSearchParser()
    parser.feed(results_html)
    parser.close()
    return parser.deals


class SteamClient:
    """Steam Store specials and public review summary client."""

    search_url = "https://store.steampowered.com/search/results/"
    reviews_url = "https://store.steampowered.com/appreviews/{app_id}"

    def __init__(self) -> None:
        self.session: aiohttp.ClientSession | None = None
        self._cache: dict[str, tuple[datetime, list[SteamDeal]]] = {}

    async def start(self) -> None:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=25),
                headers={
                    "accept": "application/json,text/plain,*/*",
                    "accept-language": "zh-CN,zh;q=0.9,en;q=0.7",
                    "user-agent": "EthuPbot/0.2 Steam deal notifier",
                },
            )

    async def close(self) -> None:
        if self.session and not self.session.closed:
            await self.session.close()

    async def deals(
        self, minimum_discount: int = 30, limit: int = 20, country: str = "CN"
    ) -> list[SteamDeal]:
        country = country.strip().upper()[:2] or "CN"
        cached = self._cache.get(country)
        if cached and datetime.now(UTC) - cached[0] < timedelta(minutes=15):
            deals = cached[1]
        else:
            deals = await self._fetch_deals(country)
            self._cache[country] = (datetime.now(UTC), deals)
        return [deal for deal in deals if deal.discount_percent >= minimum_discount][:limit]

    async def _fetch_deals(self, country: str) -> list[SteamDeal]:
        await self.start()
        assert self.session is not None
        params = {
            "query": "",
            "start": "0",
            "count": "50",
            "dynamic_data": "",
            "sort_by": "_ASC",
            "specials": "1",
            "infinite": "1",
            "category1": "998",
            "ignore_preferences": "1",
            "cc": country.lower(),
            "l": "schinese",
        }
        try:
            async with self.session.get(self.search_url, params=params) as response:
                if response.status == 429:
                    raise SteamError("Steam 请求过于频繁，请稍后再试。")
                if response.status >= 400:
                    raise SteamError(f"Steam 商店返回 HTTP {response.status}")
                payload = json.loads(await response.text())
        except TimeoutError as exc:
            raise SteamError("Steam 商店响应超时。") from exc
        except (aiohttp.ClientError, json.JSONDecodeError) as exc:
            raise SteamError("暂时无法读取 Steam 优惠列表。") from exc

        deals = parse_search_results(str(payload.get("results_html", "")))
        if not deals:
            raise SteamError("Steam 暂未返回可解析的折扣游戏。")
        semaphore = asyncio.Semaphore(6)

        async def enrich(deal: SteamDeal) -> SteamDeal:
            async with semaphore:
                summary = await self._review_summary(deal.app_id)
            return replace(deal, **summary) if summary else deal

        enriched = await asyncio.gather(*(enrich(deal) for deal in deals[:30]))
        return [*enriched, *deals[30:]]

    async def _review_summary(self, app_id: int) -> dict[str, int | str] | None:
        assert self.session is not None
        params = {
            "json": "1",
            "language": "all",
            "purchase_type": "all",
            "num_per_page": "0",
        }
        try:
            async with self.session.get(
                self.reviews_url.format(app_id=app_id), params=params
            ) as response:
                if response.status >= 400:
                    return None
                payload = await response.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError):
            return None
        summary = payload.get("query_summary", {})
        try:
            return {
                "review_score": int(summary.get("review_score", 0)),
                "review_label": str(summary.get("review_score_desc", "暂无评价")),
                "total_positive": int(summary.get("total_positive", 0)),
                "total_reviews": int(summary.get("total_reviews", 0)),
            }
        except (TypeError, ValueError):
            return None
