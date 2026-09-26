"""备用数据源与代理修正的单元测试（纯逻辑，不打网络）。"""

import json
import os
from datetime import date
from unittest.mock import patch

import pytest

from src import fallback_sources as fb
from src.http_urllib import DOMESTIC_PROXY_HOSTS, ensure_domestic_via_proxy


class TestEnsureDomesticViaProxy:
    """no_proxy 修正 —— 东财走直连必挂，必须强制走代理。"""

    def test_removes_eastmoney_from_no_proxy(self):
        env = {"no_proxy": "localhost,127.0.0.1,eastmoney.com,push2his.eastmoney.com"}
        with patch.dict(os.environ, env, clear=True):
            removed = ensure_domestic_via_proxy()

            assert removed == ["eastmoney.com", "push2his.eastmoney.com"]
            assert os.environ["no_proxy"] == "localhost,127.0.0.1"

    def test_removes_from_uppercase_variant_too(self):
        with patch.dict(os.environ, {"NO_PROXY": "eastmoney.com,example.com"}, clear=True):
            ensure_domestic_via_proxy()

            assert os.environ["NO_PROXY"] == "example.com"

    def test_matches_subdomains_and_leading_dot(self):
        with patch.dict(os.environ, {"no_proxy": ".gtimg.cn,qt.gtimg.cn,foo.com"}, clear=True):
            removed = ensure_domestic_via_proxy()

            assert set(removed) == {".gtimg.cn", "qt.gtimg.cn"}
            assert os.environ["no_proxy"] == "foo.com"

    def test_leaves_unrelated_hosts_untouched(self):
        with patch.dict(os.environ, {"no_proxy": "localhost,example.com"}, clear=True):
            removed = ensure_domestic_via_proxy()

            assert removed == []
            assert os.environ["no_proxy"] == "localhost,example.com"

    def test_escape_hatch_disables_correction(self):
        env = {"no_proxy": "eastmoney.com", "STOCK_ALLOW_DIRECT_CN": "1"}
        with patch.dict(os.environ, env, clear=True):
            removed = ensure_domestic_via_proxy()

            assert removed == []
            assert os.environ["no_proxy"] == "eastmoney.com"

    def test_no_env_set_is_noop(self):
        with patch.dict(os.environ, {}, clear=True):
            assert ensure_domestic_via_proxy() == []

    def test_all_domestic_hosts_are_bare_domains(self):
        # 前缀点会让 lstrip 比较出错, 这里锁住约定
        assert all(not h.startswith(".") for h in DOMESTIC_PROXY_HOSTS)


class TestSinaPrefix:
    """新浪代码前缀 —— 认错市场会静默拿到空数据。"""

    @pytest.mark.parametrize(
        "code,expected",
        [
            ("600519", "sh600519"),
            ("688012", "sh688012"),
            ("000001", "sz000001"),
            ("300364", "sz300364"),
            ("920138", "bj920138"),
            ("921001", "bj921001"),
            ("830799", "bj830799"),
            ("400001", "bj400001"),
        ],
    )
    def test_prefix_by_market(self, code, expected):
        assert fb._sina_prefix(code) == expected


class TestRestrictedRelease:
    """解禁: 东财 LIFT_MARKET_CAP 单位是万元, FREE_RATIO 是小数。"""

    def test_converts_units(self):
        raw = [
            {
                "SECURITY_CODE": "603119",
                "SECURITY_NAME_ABBR": "浙江荣泰",
                "FREE_DATE": "2026-08-03 00:00:00",
                "LIFT_MARKET_CAP": 1013753.116026,  # 万元
                "FREE_RATIO": 0.439498966003,       # 小数
            }
        ]
        with patch.object(fb, "em_datacenter", return_value=raw):
            rows = fb.restricted_release(date(2026, 8, 3), date(2026, 8, 17))

        assert rows[0]["lift_market_cap_yi"] == pytest.approx(101.38, abs=0.01)
        assert rows[0]["free_ratio_pct"] == pytest.approx(43.95, abs=0.01)
        assert rows[0]["free_date"] == "2026-08-03"

    def test_handles_null_fields(self):
        raw = [
            {
                "SECURITY_CODE": "1",
                "SECURITY_NAME_ABBR": "X",
                "FREE_DATE": "2026-08-05 00:00:00",
                "LIFT_MARKET_CAP": None,
                "FREE_RATIO": None,
            }
        ]
        with patch.object(fb, "em_datacenter", return_value=raw):
            rows = fb.restricted_release(date(2026, 8, 3), date(2026, 8, 17))

        assert rows[0]["lift_market_cap_yi"] is None
        assert rows[0]["free_ratio_pct"] is None
        assert rows[0]["code"] == "000001"  # 补零到 6 位


class TestLhbDetail:
    def test_converts_net_amount_to_yi(self):
        raw = [
            {
                "SECURITY_CODE": "603986",
                "SECURITY_NAME_ABBR": "兆易创新",
                "TRADE_DATE": "2026-08-03 00:00:00",
                "EXPLANATION": "跌幅偏离值达7%",
                "BILLBOARD_NET_AMT": -217000000.0,
            }
        ]
        with patch.object(fb, "em_datacenter", return_value=raw):
            rows = fb.lhb_detail(date(2026, 8, 1), date(2026, 8, 3))

        assert rows[0]["net_buy_yi"] == pytest.approx(-2.17, abs=0.01)
        assert rows[0]["trade_date"] == "2026-08-03"


class TestSinaUsKline:
    """新浪美股 num 参数上游不生效，必须客户端截断。"""

    def _payload(self, n):
        rows = [
            {"d": f"2026-01-{i + 1:02d}", "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 100 + i}
            for i in range(n)
        ]
        return f"var x=({json.dumps(rows)});"

    def test_truncates_to_requested_count(self):
        with patch.object(fb, "urllib_get_text", return_value=self._payload(20)):
            rows = fb.sina_us_kline("AAPL", 5)

        assert len(rows) == 5
        assert rows[-1]["date"] == "2026-01-20"  # 保留最近的
        assert rows[0]["date"] == "2026-01-16"

    def test_sorted_ascending_with_volume(self):
        with patch.object(fb, "urllib_get_text", return_value=self._payload(3)):
            rows = fb.sina_us_kline("AAPL", 10)

        assert [r["date"] for r in rows] == sorted(r["date"] for r in rows)
        assert rows[0]["volume"] == 100

    def test_raises_on_malformed_payload(self):
        with patch.object(fb, "urllib_get_text", return_value="not jsonp"):
            with pytest.raises(ValueError):
                fb.sina_us_kline("AAPL", 5)


class TestRepairUsVolume:
    """volume=0 会让 anomaly 产出伪 high 信号，修复只填 0 不覆盖真实值。"""

    def _history(self, tmp_path, rows):
        p = tmp_path / "us_quote_history.json"
        p.write_text(json.dumps({"AAPL": rows}), encoding="utf-8")
        return p

    def test_fills_only_zero_volume(self, tmp_path):
        path = self._history(
            tmp_path,
            [
                {"date": "2026-07-30", "close": 333.43, "volume": 0},
                {"date": "2026-07-31", "close": 308.91, "volume": 999},
            ],
        )
        kline = [
            {"date": "2026-07-30", "volume": 74817792},
            {"date": "2026-07-31", "volume": 132489119},
        ]
        with patch.object(fb, "sina_us_kline", return_value=kline):
            fixed, warnings = fb.repair_us_volume(path, ["AAPL"])

        data = json.loads(path.read_text(encoding="utf-8"))
        assert fixed == 1
        assert warnings == []
        assert data["AAPL"][0]["volume"] == 74817792
        assert data["AAPL"][1]["volume"] == 999  # 非零值不被覆盖

    def test_skips_when_no_zero_rows(self, tmp_path):
        path = self._history(tmp_path, [{"date": "2026-07-30", "volume": 5}])
        with patch.object(fb, "sina_us_kline", side_effect=AssertionError("不该调用")):
            fixed, warnings = fb.repair_us_volume(path, ["AAPL"])

        assert fixed == 0
        assert warnings == []

    def test_reports_failed_symbols(self, tmp_path):
        path = self._history(tmp_path, [{"date": "2026-07-30", "volume": 0}])
        with patch.object(fb, "sina_us_kline", side_effect=RuntimeError("boom")):
            fixed, warnings = fb.repair_us_volume(path, ["AAPL"])

        assert fixed == 0
        assert "AAPL" in warnings[0]

    def test_missing_file_is_noop(self, tmp_path):
        assert fb.repair_us_volume(tmp_path / "nope.json", ["AAPL"]) == (0, [])


class TestDetectStale:
    """同花顺北向曾连续三日返回同一数值，静默使用会把陈旧数据当当日证据。"""

    def test_flags_after_max_repeat(self, tmp_path):
        path = tmp_path / "state.json"
        value = [-9.28, None]

        stale, state = fb.detect_stale(path, "northbound", value, max_repeat=2)
        assert stale is False
        path.write_text(json.dumps(state), encoding="utf-8")

        stale, state = fb.detect_stale(path, "northbound", value, max_repeat=2)
        assert stale is False  # 第 2 次: repeat=1
        path.write_text(json.dumps(state), encoding="utf-8")

        stale, _ = fb.detect_stale(path, "northbound", value, max_repeat=2)
        assert stale is True  # 第 3 次: repeat=2 → 判陈旧

    def test_value_change_resets_counter(self, tmp_path):
        path = tmp_path / "state.json"
        for _ in range(3):
            _, state = fb.detect_stale(path, "nb", [-9.28], max_repeat=2)
            path.write_text(json.dumps(state), encoding="utf-8")

        stale, state = fb.detect_stale(path, "nb", [-15.0], max_repeat=2)
        assert stale is False
        assert state["nb"]["repeat"] == 0

    def test_corrupt_state_file_does_not_raise(self, tmp_path):
        path = tmp_path / "state.json"
        path.write_text("{not json", encoding="utf-8")

        stale, state = fb.detect_stale(path, "nb", [1.0])
        assert stale is False
        assert "nb" in state


class TestHkexNorthbound:
    """HKEX 只有成交额没有净买入 —— 解析必须如实反映这一点。"""

    PAYLOAD = """tabData = [
      {"market": "SSE Northbound", "date": "2026-07-31", "content": [
        {"table": {"classname": "tradingTable",
                   "tr": [{"td": [["159,927.12"]]}, {"td": [["7,647,887"]]}]}},
        {"table": {"classname": "top10Table",
                   "tr": [{"td": [["1", "603986", "兆易創新　　", "3,882,909,312"]]}]}}
      ]},
      {"market": "SSE Southbound", "date": "2026-07-31", "content": [
        {"table": {"classname": "tradingTable", "tr": [{"td": [["86,457.67"]]}]}}
      ]}
    ];"""

    def test_parses_turnover_and_top10(self):
        with patch.object(fb, "urllib_get_text", return_value=self.PAYLOAD):
            out = fb.hkex_northbound(date(2026, 7, 31))

        # 百万港元 -> 亿
        assert out["sse_turnover_yi"] == pytest.approx(1599.27, abs=0.01)
        assert len(out["top10"]) == 1
        assert out["top10"][0]["code"] == "603986"
        assert out["top10"][0]["name"] == "兆易創新"  # 全角空格已剥离
        assert out["top10"][0]["turnover_yi"] == pytest.approx(38.83, abs=0.01)

    def test_ignores_southbound(self):
        with patch.object(fb, "urllib_get_text", return_value=self.PAYLOAD):
            out = fb.hkex_northbound(date(2026, 7, 31))

        assert all(t["market"] == "SSE" for t in out["top10"])
        assert out["szse_turnover_yi"] is None

    def test_raises_on_malformed_payload(self):
        with patch.object(fb, "urllib_get_text", return_value="tabData = ;"):
            with pytest.raises(ValueError):
                fb.hkex_northbound(date(2026, 7, 31))


class TestNasdaqEarnings:
    def test_maps_session_labels(self):
        payload = {
            "data": {
                "rows": [
                    {"symbol": "COP", "name": "C", "time": "time-pre-market", "epsForecast": "$2.96"},
                    {"symbol": "MP", "name": "M", "time": "time-after-hours", "epsForecast": "($0.02)"},
                    {"symbol": "TM", "name": "T", "time": "time-not-supplied", "epsForecast": None},
                ]
            }
        }
        with patch.object(fb, "urllib_get_json", return_value=payload):
            rows = fb.nasdaq_earnings(date(2026, 8, 6))

        assert [r["when"] for r in rows] == ["盘前", "盘后", ""]
        assert rows[2]["eps_forecast"] == ""

    def test_empty_response_is_empty_list(self):
        with patch.object(fb, "urllib_get_json", return_value={}):
            assert fb.nasdaq_earnings(date(2026, 8, 6)) == []
