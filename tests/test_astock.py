"""Tests for A股增强数据模块 (腾讯基本面 + mootdx 兜底)。"""

from datetime import date

import pandas as pd
import pytest

from src.astock import (
    _a_share_prefix,
    _normalize_mootdx_df,
    _parse_tencent_line,
    fetch_a_share_basics,
)


# 真实腾讯返回格式样本 (贵州茅台)，字段以 ~ 分隔；尾部补足到 >=53 字段
def make_tencent_line(code: str = "600519", name: str = "贵州茅台") -> str:
    fields = ["1", name, code]
    # 索引 3 起，填充占位，再覆盖关键索引
    fields += ["0"] * 60
    fields[3] = "1688.00"   # price
    fields[4] = "1670.00"   # last_close
    fields[5] = "1675.00"   # open
    fields[32] = "1.08"     # change_pct
    fields[33] = "1695.00"  # high
    fields[34] = "1672.00"  # low
    fields[38] = "0.35"     # turnover_pct
    fields[39] = "22.5"     # pe_ttm
    fields[43] = "1.38"     # 振幅 (非PB)
    fields[44] = "2120.0"   # mcap_yi
    fields[45] = "2120.0"   # float_mcap_yi
    fields[46] = "8.9"      # pb
    fields[47] = "1837.00"  # limit_up
    fields[48] = "1503.00"  # limit_down
    fields[49] = "0.92"     # vol_ratio
    fields[52] = "23.1"     # pe_static
    payload = "~".join(fields)
    return f'v_sh{code}="{payload}"'


class TestAShachePrefix:
    def test_shanghai(self):
        assert _a_share_prefix("600519") == "sh"
        assert _a_share_prefix("688017") == "sh"

    def test_shenzhen(self):
        assert _a_share_prefix("000001") == "sz"
        assert _a_share_prefix("300750") == "sz"

    def test_beijing(self):
        assert _a_share_prefix("832000") == "bj"


class TestParseTencentLine:
    def test_parses_key_fields(self):
        info = _parse_tencent_line(make_tencent_line("600519", "贵州茅台"))
        assert info is not None
        assert info.symbol == "600519"
        assert info.name == "贵州茅台"
        assert info.price == pytest.approx(1688.00)
        assert info.change_pct == pytest.approx(1.08)
        assert info.pe_ttm == pytest.approx(22.5)
        assert info.pe_static == pytest.approx(23.1)

    def test_pb_is_index_46_not_43(self):
        # 踩坑校验: 43 是振幅(1.38)，PB 在 46(8.9)
        info = _parse_tencent_line(make_tencent_line())
        assert info.pb == pytest.approx(8.9)

    def test_market_cap_and_turnover(self):
        info = _parse_tencent_line(make_tencent_line())
        assert info.mcap_yi == pytest.approx(2120.0)
        assert info.turnover_pct == pytest.approx(0.35)
        assert info.vol_ratio == pytest.approx(0.92)
        assert info.limit_up == pytest.approx(1837.00)
        assert info.limit_down == pytest.approx(1503.00)

    def test_empty_field_defaults_to_zero(self):
        line = make_tencent_line()
        line = line.replace("~22.5~", "~~")  # 清空 pe_ttm
        info = _parse_tencent_line(line)
        assert info.pe_ttm == 0.0

    def test_invalid_line_returns_none(self):
        assert _parse_tencent_line("") is None
        assert _parse_tencent_line("garbage no equals") is None
        assert _parse_tencent_line('v_sh600519="1~太短"') is None


def make_mootdx_df(rows: int = 3) -> pd.DataFrame:
    base = pd.Timestamp("2026-06-15")
    data = []
    for i in range(rows):
        data.append({
            "datetime": base + pd.Timedelta(days=i),
            "open": 100.0 + i,
            "high": 105.0 + i,
            "low": 99.0 + i,
            "close": 102.0 + i,
            "vol": 10000 + i * 100,
            "amount": 1e6 + i * 1e4,
        })
    return pd.DataFrame(data)


class TestNormalizeMootdxDf:
    def test_normalizes_rows(self):
        quotes = _normalize_mootdx_df(make_mootdx_df(3), "600519")
        assert len(quotes) == 3
        assert quotes[0].symbol == "600519"
        assert quotes[0].date == date(2026, 6, 15)
        assert quotes[0].close == pytest.approx(102.0)

    def test_sorted_by_date(self):
        df = make_mootdx_df(4).iloc[::-1].reset_index(drop=True)
        quotes = _normalize_mootdx_df(df, "600519")
        assert [q.date for q in quotes] == sorted(q.date for q in quotes)

    def test_change_pct_day_over_day(self):
        quotes = _normalize_mootdx_df(make_mootdx_df(2), "600519")
        # 第一天无前收 -> 0；第二天 (103-102)/102*100
        assert quotes[0].change_pct == pytest.approx(0.0)
        assert quotes[1].change_pct == pytest.approx((103.0 - 102.0) / 102.0 * 100, abs=1e-3)

    def test_skips_zero_close(self):
        df = make_mootdx_df(2)
        df.loc[0, "close"] = 0
        quotes = _normalize_mootdx_df(df, "600519")
        assert len(quotes) == 1

    def test_empty_df(self):
        assert _normalize_mootdx_df(pd.DataFrame(), "600519") == []


class TestFetchABasics:
    def test_empty_symbols(self):
        assert fetch_a_share_basics([]) == {}

    def test_parses_batch_response(self, monkeypatch):
        body = (
            make_tencent_line("600519", "贵州茅台") + ";"
            + make_tencent_line("000001", "平安银行") + ";"
        )

        class FakeResp:
            content = body.encode("gbk")

            def raise_for_status(self):
                pass

        monkeypatch.setattr("src.astock.httpx.get", lambda *a, **k: FakeResp())
        result = fetch_a_share_basics(["600519", "000001"])
        assert set(result.keys()) == {"600519", "000001"}
        assert result["000001"].name == "平安银行"

    def test_request_failure_returns_empty(self, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("network down")

        monkeypatch.setattr("src.astock.httpx.get", boom)
        assert fetch_a_share_basics(["600519"]) == {}


# ── 主力资金流: 东财失败时的新浪兜底 ─────────────────────────
class TestFundFlowSinaFallback:
    """东财 push2 限流时, 资金流应回退到新浪逐只查询。"""

    SAMPLE = [{
        "opendate": "2026-07-20",
        "netamount": "3764778706.78",
        "ratioamount": "0.273017",
    }]

    def _stub_client(self, monkeypatch, payload_for):
        """用假的 httpx.Client 替换真实网络调用。"""
        import src.astock as astock

        class FakeResp:
            def __init__(self, data):
                self._data = data

            def raise_for_status(self):
                if self._data is None:
                    raise RuntimeError("boom")

            def json(self):
                return self._data

        class FakeClient:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def get(self, url, params=None):
                return FakeResp(payload_for(params["daima"]))

        monkeypatch.setattr(astock.httpx, "Client", lambda **kw: FakeClient())

    def test_maps_sina_fields_to_fund_flow(self, monkeypatch):
        from src.astock import _fetch_fund_flow_sina

        self._stub_client(monkeypatch, lambda d: self.SAMPLE)
        flows, warnings = _fetch_fund_flow_sina(["600519"], {"600519": "贵州茅台"})

        assert warnings == []
        assert len(flows) == 1
        assert flows[0].symbol == "600519"
        assert flows[0].name == "贵州茅台"
        assert flows[0].main_net_inflow_yi == 37.65  # 元 -> 亿
        assert flows[0].main_net_pct == 27.3         # 小数 -> 百分比

    def test_sorted_by_net_inflow_desc(self, monkeypatch):
        from src.astock import _fetch_fund_flow_sina

        payloads = {
            "sh600519": [{"netamount": "1e8", "ratioamount": "0.1"}],
            "sz000001": [{"netamount": "5e8", "ratioamount": "0.2"}],
        }
        self._stub_client(monkeypatch, lambda d: payloads[d])
        flows, _ = _fetch_fund_flow_sina(["600519", "000001"])

        assert [f.symbol for f in flows] == ["000001", "600519"]

    def test_partial_failure_keeps_successful_symbols(self, monkeypatch):
        from src.astock import _fetch_fund_flow_sina

        self._stub_client(
            monkeypatch,
            lambda d: self.SAMPLE if d == "sh600519" else None,
        )
        flows, warnings = _fetch_fund_flow_sina(["600519", "000001"])

        assert warnings == []
        assert [f.symbol for f in flows] == ["600519"]

    def test_all_failed_returns_warning(self, monkeypatch):
        from src.astock import _fetch_fund_flow_sina

        self._stub_client(monkeypatch, lambda d: None)
        flows, warnings = _fetch_fund_flow_sina(["600519", "000001"])

        assert flows == []
        assert len(warnings) == 1
        assert "主力资金流获取失败" in warnings[0]

    def test_empty_symbols_no_warning(self):
        from src.astock import _fetch_fund_flow_sina

        assert _fetch_fund_flow_sina([]) == ([], [])

    def test_eastmoney_failure_triggers_sina(self, monkeypatch):
        """akshare 抛 SSLError 时应走兜底而不是直接报警告。"""
        import src.astock as astock
        from src.astock import fetch_fund_flow_rank

        called = {}

        def fake_sina(symbols, names=None):
            called["hit"] = (symbols, names)
            return ["sentinel"], []

        monkeypatch.setattr(astock, "_fetch_fund_flow_sina", fake_sina)
        import akshare as ak
        monkeypatch.setattr(
            ak, "stock_individual_fund_flow_rank",
            lambda **kw: (_ for _ in ()).throw(OSError("SSL EOF")),
        )

        flows, warnings = fetch_fund_flow_rank(["600519"], {"600519": "贵州茅台"})

        assert flows == ["sentinel"]
        assert warnings == []
        assert called["hit"] == (["600519"], {"600519": "贵州茅台"})
