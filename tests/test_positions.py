from crypto_bot.positions import calculate_pnl, parse_position_message


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
