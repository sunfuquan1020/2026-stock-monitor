"""Tests for 美股/港股增强数据模块 (Yahoo K线 + 基本面)。"""

from datetime import datetime

import pytest

from src.global_stock import (
    _parse_yahoo_chart,
    fetch_global_basics,
    fetch_kline_yahoo,
    to_yahoo_symbol,
)


class TestToYahooSymbol:
    def test_us_plain(self):
        assert to_yahoo_symbol("AAPL", "美股") == "AAPL"
        assert to_yahoo_symbol("nvda", "美股") == "NVDA"

    def test_us_dotted_to_dash(self):
        assert to_yahoo_symbol("BRK.B", "美股") == "BRK-B"

    def test_hk_zero_pad(self):
        assert to_yahoo_symbol("00700", "港股") == "0700.HK"
        assert to_yahoo_symbol("09988", "港股") == "9988.HK"
        assert to_yahoo_symbol("03690", "港股") == "3690.HK"


def make_chart(closes: list[float]) -> dict:
    base = int(datetime(2026, 6, 15).timestamp())
    n = len(closes)
    timestamps = [base + i * 86400 for i in range(n)]
    return {
        "chart": {
            "result": [{
                "timestamp": timestamps,
                "indicators": {"quote": [{
                    "open": [c - 1 for c in closes],
                    "high": [c + 2 for c in closes],
                    "low": [c - 2 for c in closes],
                    "close": list(closes),
                    "volume": [1_000_000 + i * 1000 for i in range(n)],
                }]},
            }]
        }
    }


class TestParseYahooChart:
    def test_parses_ohlcv(self):
        quotes = _parse_yahoo_chart(make_chart([100.0, 102.0, 101.0]), "AAPL")
        assert len(quotes) == 3
        assert quotes[0].symbol == "AAPL"
        assert quotes[0].close == pytest.approx(100.0)
        assert quotes[1].volume == 1_001_000

    def test_change_pct_day_over_day(self):
        quotes = _parse_yahoo_chart(make_chart([100.0, 110.0]), "AAPL")
        assert quotes[0].change_pct == pytest.approx(0.0)       # 首日无前收
        assert quotes[1].change_pct == pytest.approx(10.0)      # (110-100)/100

    def test_sorted_by_date(self):
        quotes = _parse_yahoo_chart(make_chart([100.0, 101.0, 102.0, 103.0]), "AAPL")
        assert [q.date for q in quotes] == sorted(q.date for q in quotes)

    def test_skips_none_close(self):
        chart = make_chart([100.0, 101.0])
        chart["chart"]["result"][0]["indicators"]["quote"][0]["close"][1] = None
        quotes = _parse_yahoo_chart(chart, "AAPL")
        assert len(quotes) == 1

    def test_malformed_returns_empty(self):
        assert _parse_yahoo_chart({}, "AAPL") == []
        assert _parse_yahoo_chart({"chart": {"result": []}}, "AAPL") == []


class TestFetchKlineYahoo:
    def test_parses_response(self, monkeypatch):
        class FakeResp:
            def __init__(self, data):
                self._data = data

            def raise_for_status(self):
                pass

            def json(self):
                return self._data

        monkeypatch.setattr(
            "src.global_stock.httpx.get",
            lambda *a, **k: FakeResp(make_chart([200.0, 204.0])),
        )
        quotes = fetch_kline_yahoo("AAPL", market="美股")
        assert len(quotes) == 2
        assert quotes[1].change_pct == pytest.approx(2.0)

    def test_request_failure_returns_empty(self, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("network down")

        monkeypatch.setattr("src.global_stock.httpx.get", boom)
        assert fetch_kline_yahoo("AAPL", market="美股") == []


def make_quote_summary(price: float = 190.5) -> dict:
    return {
        "quoteSummary": {
            "result": [{
                "financialData": {
                    "currentPrice": {"raw": price},
                    "returnOnEquity": {"raw": 0.45},
                    "targetMeanPrice": {"raw": 210.0},
                    "recommendationKey": "buy",
                },
                "defaultKeyStatistics": {
                    "forwardPE": {"raw": 25.3},
                    "priceToBook": {"raw": 12.1},
                    "pegRatio": {"raw": 1.8},
                    "profitMargins": {"raw": 0.24},
                },
                "summaryDetail": {
                    "trailingPE": {"raw": 30.5},
                    "marketCap": {"raw": 3.5e12},
                },
            }]
        }
    }


class TestFetchGlobalBasics:
    def test_empty_items(self):
        assert fetch_global_basics([]) == {}

    def test_parses_basics(self, monkeypatch):
        class FakeClient:
            def get(self, url, params=None):
                class R:
                    def raise_for_status(self):
                        pass

                    def json(self):
                        return make_quote_summary(190.5)
                return R()

        monkeypatch.setattr(
            "src.global_stock._get_yahoo_crumb_client",
            lambda: (FakeClient(), "fake-crumb"),
        )
        result = fetch_global_basics([("AAPL", "苹果", "美股")])
        assert "AAPL" in result
        info = result["AAPL"]
        assert info.name == "苹果"
        assert info.market == "美股"
        assert info.price == pytest.approx(190.5)
        assert info.pe_ttm == pytest.approx(30.5)
        assert info.forward_pe == pytest.approx(25.3)
        assert info.pb == pytest.approx(12.1)
        assert info.peg == pytest.approx(1.8)
        assert info.market_cap == pytest.approx(3.5e12)
        assert info.roe == pytest.approx(0.45)
        assert info.target_mean == pytest.approx(210.0)
        assert info.recommendation == "buy"

    def test_crumb_unavailable_returns_empty(self, monkeypatch):
        monkeypatch.setattr(
            "src.global_stock._get_yahoo_crumb_client",
            lambda: (None, ""),
        )
        assert fetch_global_basics([("AAPL", "苹果", "美股")]) == {}
