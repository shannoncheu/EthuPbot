from __future__ import annotations

import re
from dataclasses import dataclass
from math import isfinite


@dataclass(frozen=True, slots=True)
class ParsedPosition:
    symbol: str
    entry_price: float
    quantity: float
    leverage: float
    direction: str


def parse_position_message(message: str) -> ParsedPosition | None:
    """Parse concise Chinese position descriptions without asking the AI to guess numbers."""
    text = message.replace(",", "").replace("，", " ")
    entry_match = re.search(
        r"(?:在|入场价?|成本价?|买入价?|开仓价?)\s*(?:是|为|=|:|：)?\s*\$?"
        r"(\d+(?:\.\d+)?)",
        text,
        re.IGNORECASE,
    )
    quantity_match = re.search(
        r"(\d+(?:\.\d+)?)\s*(?:个|枚|只|股)?\s*([a-z][a-z0-9]{0,29})",
        text,
        re.IGNORECASE,
    )
    if not entry_match or not quantity_match:
        return None

    leverage_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:倍|x)", text, re.IGNORECASE)
    direction = "short" if re.search(r"做?空|short", text, re.IGNORECASE) else "long"
    return ParsedPosition(
        symbol=quantity_match.group(2).upper(),
        entry_price=float(entry_match.group(1)),
        quantity=float(quantity_match.group(1)),
        leverage=float(leverage_match.group(1)) if leverage_match else 1.0,
        direction=direction,
    )


def calculate_pnl(
    entry_price: float,
    current_price: float,
    quantity: float,
    leverage: float,
    direction: str,
) -> tuple[float, float, float]:
    values = (entry_price, current_price, quantity, leverage)
    if not all(isfinite(value) for value in values):
        raise ValueError("持仓数值必须是有限数字。")
    if entry_price <= 0 or current_price <= 0 or quantity <= 0 or leverage <= 0:
        raise ValueError("价格、数量和杠杆必须大于 0。")
    if direction not in {"long", "short"}:
        raise ValueError("持仓方向必须是 long 或 short。")
    sign = 1 if direction == "long" else -1
    pnl = (current_price - entry_price) * quantity * sign
    margin = entry_price * quantity / leverage
    if not isfinite(pnl) or not isfinite(margin) or margin <= 0:
        raise ValueError("持仓计算结果超出有效范围。")
    roi = pnl / margin * 100
    if not isfinite(roi):
        raise ValueError("持仓收益率超出有效范围。")
    return pnl, margin, roi


def calculate_portfolio_totals(
    valuations: list[tuple[float, float]],
) -> tuple[float, float, float]:
    """Return total unrealized PnL, total margin and margin-weighted ROI."""
    total_pnl = sum(pnl for pnl, _ in valuations)
    total_margin = sum(margin for _, margin in valuations)
    total_roi = total_pnl / total_margin * 100 if total_margin > 0 else 0.0
    return total_pnl, total_margin, total_roi
