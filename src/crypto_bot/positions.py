from __future__ import annotations

import re
from dataclasses import dataclass


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
    sign = 1 if direction == "long" else -1
    pnl = (current_price - entry_price) * quantity * sign
    margin = entry_price * quantity / leverage
    roi = pnl / margin * 100
    return pnl, margin, roi
