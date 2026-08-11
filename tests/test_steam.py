from crypto_bot.steam import SteamDeal, parse_search_results


def test_parse_steam_search_result() -> None:
    source = """
    <a class="search_result_row" data-ds-appid="123">
      <div class="search_capsule"><img src="//cdn.example/game.jpg"></div>
      <span class="title">测试游戏</span>
      <span class="search_review_summary positive"
            data-tooltip-html="好评如潮&lt;br&gt;此游戏的 12,345 篇用户评测中有 97% 为好评。">
      </span>
      <div class="discount_pct">-75%</div>
      <div class="discount_original_price">¥100.00</div>
      <div class="discount_final_price">¥25.00</div>
    </a>
    """

    deals = parse_search_results(source)

    assert len(deals) == 1
    deal = deals[0]
    assert deal.app_id == 123
    assert deal.name == "测试游戏"
    assert deal.image_url == "https://cdn.example/game.jpg"
    assert deal.discount_percent == 75
    assert deal.original_price == "¥100.00"
    assert deal.final_price == "¥25.00"
    assert deal.review_score == 9
    assert deal.total_reviews == 12_345
    assert deal.positive_percent == 97
    assert deal.is_overwhelmingly_positive


def test_hot_deal_uses_public_review_volume() -> None:
    deal = SteamDeal(
        app_id=456,
        name="热门游戏",
        url="https://store.steampowered.com/app/456/",
        image_url=None,
        discount_percent=50,
        original_price="¥100.00",
        final_price="¥50.00",
        review_score=8,
        review_label="Very Positive",
        total_positive=19_000,
        total_reviews=20_000,
    )

    assert deal.is_hot()
    assert not deal.is_overwhelmingly_positive
