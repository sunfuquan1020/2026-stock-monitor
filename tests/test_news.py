"""Tests for 新闻获取 (统一 WebSearch 补充, 不再调用 akshare)。"""

from src.news import build_websearch_queries, fetch_news


class TestFetchNews:
    def test_a_share_returns_empty_without_akshare(self):
        # A股新闻不再调用 akshare, 直接返回空 (由 WebSearch 补充)
        assert fetch_news("600519", market="A股") == []

    def test_us_returns_empty(self):
        assert fetch_news("AAPL", market="美股") == []

    def test_hk_returns_empty(self):
        assert fetch_news("00700", market="港股") == []

    def test_auto_detect_market(self):
        # 不传 market 也能正常返回 (自动检测)
        assert fetch_news("000001") == []

    def test_news_module_does_not_import_akshare(self):
        # 确保 news 模块已彻底去掉 akshare 依赖
        import src.news as news_mod
        assert not hasattr(news_mod, "ak")
        assert "akshare" not in dir(news_mod)


class TestBuildWebsearchQueries:
    def test_a_share_query(self):
        qs = build_websearch_queries("000001", "平安银行", "A股")
        assert qs == ["平安银行 000001 股票 最新消息"]

    def test_us_query(self):
        qs = build_websearch_queries("AAPL", "苹果", "美股")
        assert any("stock news today" in q for q in qs)

    def test_hk_query(self):
        qs = build_websearch_queries("00700", "腾讯", "港股")
        assert qs == ["腾讯 00700 港股 最新消息"]
