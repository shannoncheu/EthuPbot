import pytest

from crypto_bot.positions import (
    calculate_pnl,
    calculate_portfolio_totals,
    parse_position_message,
)


def test_parse_chinese_position_message() -> None:
    parsed = parse_position_message("我在 64000 买了 0.1 个 BTC，10 倍多单")
    assert parsed is not None
    assert parsed.symbol == "BTC"
    assert parsed.entry_price == 64_000
    assert parsed.quantity == 0.1
    assert parsed.leverage == 10
    assert parsed.direction == "long"


def test_calculate_linear_long_and_short_pnl() -> None:
    long_pnl, margin, long_roi = calculate_pnl(100, 110, 2, 5, "long")
    short_pnl, _, short_roi = calculate_pnl(100, 110, 2, 5, "short")
    assert (long_pnl, margin, long_roi) == (20, 40, 50)
    assert (short_pnl, short_roi) == (-20, -50)


def test_calculate_portfolio_total_uses_total_margin() -> None:
    pnl, margin, roi = calculate_portfolio_totals([(-41.5, 349.35), (-64, 294.4)])
    assert pnl == pytest.approx(-105.5)
    assert margin == pytest.approx(643.75)
    assert roi == pytest.approx(-16.3883495)


def test_calculate_pnl_rejects_invalid_numbers() -> None:
    with pytest.raises(ValueError):
        calculate_pnl(100, float("nan"), 2, 5, "long")
    with pytest.raises(ValueError):
        calculate_pnl(100, 110, 2, 0, "long")
