"""Tests for market thermometer and regime state machine."""

import time
from datetime import date

import pytest

from src.market import (
    _call_akshare_with_hard_timeout,
    _call_with_requests_timeout,
    classify_regime,
    compute_sector_stats,
    fetch_cn_indexes,
    fetch_cn_indexes_mootdx,
    parse_tencent_index_line,
    update_regime_history,
)
from src.models import DailyQuote, IndexQuote, MarketBreadth, StockConfig


def idx(name: str, change: float) -> IndexQuote:
    return IndexQuote(symbol="x", name=name, price=1000.0, change_pct=change)


def make_quote(symbol: str, change_pct: float) -> DailyQuote:
    return DailyQuote(
        symbol=symbol,
        date=date(2026, 7, 18),
        open=10.0,
        close=10.0,
        high=10.5,
        low=9.5,
        volume=100,
        turnover=1000.0,
        change_pct=change_pct,
    )


def blocked_source():
    time.sleep(2)
    return "late"


def immediate_source(value):
    return {"value": value}


class TestParseTencentIndexLine:
    def test_parses_valid_line(self):
        line = 'v_s_sh000001="1~上证指数~000001~3764.15~-118.61~-3.05~336059786~43046235~~475866.99~GP-A";'
        q = parse_tencent_index_line(line)
        assert q is not None
        assert q.name == "上证指数"
        assert q.price == 3764.15
        assert q.change_pct == -3.05

    def test_returns_none_for_garbage(self):
        assert parse_tencent_index_line('v_s_x="malformed";') is None
        assert parse_tencent_index_line("") is None


class TestCnIndexFallback:
    def test_uses_mootdx_when_tencent_request_fails(self, monkeypatch):
        def fail_get(*args, **kwargs):
            raise OSError("Tencent unavailable")

        expected = [
            IndexQuote("sh000001", "上证指数", 4100.0, 0.5, "mootdx（腾讯失败后降级）")
        ]
        monkeypatch.setattr("src.market.httpx.get", fail_get)
        monkeypatch.setattr("src.market.fetch_cn_indexes_mootdx", lambda: expected)

        assert fetch_cn_indexes() == expected

    def test_mootdx_fallback_computes_change_from_last_two_bars(self):
        import pandas as pd

        class FakeClient:
            def index(self, **kwargs):
                return pd.DataFrame({"close": [100.0, 102.0]})

        rows = fetch_cn_indexes_mootdx(client=FakeClient())

        assert len(rows) == 7
        assert rows[0].name == "上证指数"
        assert rows[0].price == 102.0
        assert rows[0].change_pct == 2.0
        assert rows[0].source == "mootdx（腾讯失败后降级）"


class TestSourceTimeout:
    def test_hard_timeout_terminates_blocked_akshare_call(self, monkeypatch):
        started = time.monotonic()
        with pytest.raises(TimeoutError, match="blocked_source"):
            _call_akshare_with_hard_timeout(
                "blocked_source", timeout=0.05, _module_name="tests.test_market"
            )
        assert time.monotonic() - started < 1.0

    def test_hard_timeout_returns_source_result(self, monkeypatch):
        assert _call_akshare_with_hard_timeout(
            "immediate_source", 7, timeout=1, _module_name="tests.test_market"
        ) == {"value": 7}

    def test_injects_default_timeout_into_akshare_requests(self, monkeypatch):
        seen = {}

        def fake_get(url, **kwargs):
            seen["timeout"] = kwargs.get("timeout")
            return "ok"

        monkeypatch.setattr("requests.get", fake_get)

        result = _call_with_requests_timeout(lambda: __import__("requests").get("https://example.test"), 12)

        assert result == "ok"
        assert seen["timeout"] == 12

    def test_preserves_source_specific_timeout(self, monkeypatch):
        seen = {}

        def fake_get(url, **kwargs):
            seen["timeout"] = kwargs.get("timeout")
            return "ok"

        monkeypatch.setattr("requests.get", fake_get)

        _call_with_requests_timeout(
            lambda: __import__("requests").get("https://example.test", timeout=3),
            12,
        )

        assert seen["timeout"] == 3


class TestClassifyRegime:
    def test_kill_when_growth_index_crashes(self):
        regime, reasons = classify_regime(
            [idx("上证指数", -1.0), idx("创业板指", -5.0)], None, {}
        )
        assert regime == "杀跌"

    def test_kill_when_limit_down_flood(self):
        breadth = MarketBreadth(
            up_count=500, down_count=4500, flat_count=100, limit_up=5, limit_down=176
        )
        regime, _ = classify_regime([idx("上证指数", -1.0)], breadth, {})
        assert regime == "杀跌"

    def test_shift_when_attack_sector_takes_over(self):
        regime, reasons = classify_regime(
            [idx("创业板指", -2.0)],
            None,
            {"有色/资源/煤炭/材料": 3.0, "半导体/算力硬件": -4.0},
        )
        assert regime == "换挡"
        assert any("有色" in r for r in reasons)

    def test_stall_when_only_defensive_holds(self):
        regime, _ = classify_regime(
            [idx("创业板指", -2.0)],
            None,
            {"半导体/算力硬件": -4.0, "银行/券商/金融信息": 0.5},
        )
        assert regime == "熄火"

    def test_attack_when_growth_leads_with_breadth(self):
        breadth = MarketBreadth(
            up_count=3500, down_count=1500, flat_count=100, limit_up=80, limit_down=2
        )
        regime, _ = classify_regime(
            [idx("上证指数", 0.8), idx("创业板指", 2.0)], breadth, {}
        )
        assert regime == "进攻"

    def test_default_is_neutral(self):
        regime, _ = classify_regime([idx("上证指数", 0.1)], None, {})
        assert regime == "震荡"

    def test_no_data_is_neutral(self):
        regime, _ = classify_regime([], None, {})
        assert regime == "震荡"


class TestSectorStats:
    def test_medians_by_sector_excluding_us(self):
        watchlist = [
            StockConfig("600519", "茅台", "消费", market="A股"),
            StockConfig("000568", "老窖", "消费", market="A股"),
            StockConfig("NVDA", "英伟达", "美股芯片", market="美股"),
        ]
        quotes = {
            "600519": [make_quote("600519", 1.0)],
            "000568": [make_quote("000568", 3.0)],
            "NVDA": [make_quote("NVDA", -5.0)],
        }
        stats = compute_sector_stats(quotes, watchlist)
        assert stats == {"消费": 2.0}  # 美股不参与, 单只板块(<2)不统计


class TestRegimeHistory:
    def test_history_persists_and_returns_trajectory(self, tmp_path):
        d1 = date(2026, 7, 17)
        d2 = date(2026, 7, 18)
        update_regime_history(str(tmp_path), d1, "熄火", ["r1"])
        traj = update_regime_history(str(tmp_path), d2, "杀跌", ["r2"])
        assert traj == ["07-17:熄火", "07-18:杀跌"]
