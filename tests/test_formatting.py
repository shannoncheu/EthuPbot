from crypto_bot.bot import compact, money, percent


def test_money_formats_large_and_small_values() -> None:
    assert money(1234.567) == "$1,234.57"
    assert money(0.000012345) == "$0.000012"
    assert money(100, "cny") == "¥100.00"


def test_compact_formats_market_values() -> None:
    assert compact(2_500_000_000_000) == "$2.50T"
    assert compact(42_500_000) == "$42.50M"


def test_percent_contains_direction_and_sign() -> None:
    assert percent(2.345) == "🟢 +2.35%"
    assert percent(-1.2) == "🔴 -1.20%"
