"""指数量价结构与小盘相对强度。"""

import pandas as pd
import pytest

from src.index_kline import (
    build_index_tape,
    build_style_strength,
    describe_volume_price,
)
from src.models import IndexTape


def make_bars(closes: list[float], vols: list[float]) -> pd.DataFrame:
    assert len(closes) == len(vols)
    return pd.DataFrame({"close": closes, "vol": vols})


def flat_bars(n: int = 70, close: float = 100.0, vol: float = 1000.0) -> pd.DataFrame:
    return make_bars([close] * n, [vol] * n)


class TestBuildIndexTape:
    def test_computes_vol_ratio_against_20d_average(self):
        # Arrange: 前69根量1000, 最后一根2000 → 20日均量约1050
        vols = [1000.0] * 69 + [2000.0]
        df = make_bars([100.0] * 70, vols)

        # Act
        tape = build_index_tape(df, "000852", "中证1000")

        # Assert
        expected = 2000.0 / (sum(vols[-20:]) / 20)
        assert tape.vol_ratio_20d == pytest.approx(expected, abs=0.01)

    def test_price_above_ma20_below_ma60_is_rebound_position(self):
        # Arrange: 高位120 → 杀到100 → 反弹到110
        # = MA20上方 / MA60下方 / 距60日高为负, 即 Markdown 后的反弹位置
        df = make_bars([120.0] * 50 + [100.0] * 19 + [110.0], [1000.0] * 70)

        # Act
        tape = build_index_tape(df, "000852", "中证1000")

        # Assert
        assert tape.vs_ma20_pct > 0
        assert tape.vs_ma60_pct < 0
        assert tape.dist_60d_high_pct < 0

    def test_shrinking_volume_is_labelled_缩量(self):
        # Arrange: 近5日量能只有此前的一半
        df = make_bars([100.0] * 70, [1000.0] * 65 + [300.0] * 5)

        # Act
        tape = build_index_tape(df, "000001", "上证指数")

        # Assert
        assert tape.vol_trend == "缩量"

    def test_expanding_volume_is_labelled_放量(self):
        df = make_bars([100.0] * 70, [1000.0] * 65 + [3000.0] * 5)
        assert build_index_tape(df, "000001", "上证指数").vol_trend == "放量"

    def test_steady_volume_is_labelled_平量(self):
        assert build_index_tape(flat_bars(), "000001", "上证指数").vol_trend == "平量"

    def test_returns_none_when_volume_is_all_zero(self):
        # 除零保护: 美股 volume=0 曾连续三期制造伪信号, 指数侧不重蹈覆辙
        df = make_bars([100.0] * 70, [0.0] * 70)
        assert build_index_tape(df, "000001", "上证指数") is None

    def test_returns_none_on_missing_column(self):
        df = pd.DataFrame({"close": [100.0] * 70})
        assert build_index_tape(df, "000001", "上证指数") is None


class TestBuildStyleStrength:
    def test_ratio_rises_when_small_cap_outperforms(self):
        # Arrange: 小盘从100涨到120, 大盘持平
        small = make_bars([100.0] * 60 + [120.0] * 11, [1.0] * 71)
        big = make_bars([100.0] * 71, [1.0] * 71)

        # Act
        style = build_style_strength(small, big)

        # Assert
        assert style.ratio == pytest.approx(1.2, abs=0.001)
        assert style.chg_5d_pct == pytest.approx(0.0, abs=0.01)
        assert style.chg_60d_pct > 0

    def test_short_and_long_horizons_can_disagree(self):
        # 60日前小盘更强, 近5日修复 —— 2026-08-12 的真实形态
        closes = [130.0] * 11 + [100.0] * 55 + [104.0] * 5
        small = make_bars(closes, [1.0] * 71)
        big = make_bars([100.0] * 71, [1.0] * 71)

        style = build_style_strength(small, big)

        assert style.chg_5d_pct > 0
        assert style.chg_60d_pct < 0

    def test_returns_none_when_history_too_short(self):
        short = make_bars([100.0] * 10, [1.0] * 10)
        assert build_style_strength(short, short) is None


class TestDescribeVolumePrice:
    @staticmethod
    def tape(name: str, vol_ratio: float) -> IndexTape:
        return IndexTape(
            symbol="x", name=name, vol_ratio_20d=vol_ratio, vol_trend="缩量",
            vs_ma20_pct=0.0, vs_ma60_pct=0.0, dist_60d_high_pct=0.0,
        )

    def test_flags_no_volume_rally_when_all_rising_indexes_are_dry(self):
        tapes = [self.tape("上证指数", 0.6), self.tape("中证1000", 0.5)]
        note = describe_volume_price(tapes, {"上证指数": 1.0, "中证1000": 1.5})
        assert "无量反弹" in note

    def test_reports_expansion_when_an_index_rises_on_volume(self):
        tapes = [self.tape("上证指数", 0.6), self.tape("中证1000", 1.5)]
        note = describe_volume_price(tapes, {"上证指数": 1.0, "中证1000": 1.5})
        assert "放量上涨" in note and "中证1000" in note

    def test_middle_band_still_warns_that_volume_is_unconfirmed(self):
        # 2026-08-12 真实形态: 上涨指数量比全在 0.73~0.91, 既非放量也未到缩量线。
        # 旧口径(1.3/0.7)会输出无信息量的「0/N 缩量」, 这里必须给出量比区间。
        tapes = [self.tape("中证1000", 0.91), self.tape("沪深300", 0.73)]
        note = describe_volume_price(tapes, {"中证1000": 1.24, "沪深300": 0.58})
        assert "无一放量" in note
        assert "0.73" in note and "0.91" in note

    def test_returns_empty_when_nothing_is_rising(self):
        tapes = [self.tape("上证指数", 0.6)]
        assert describe_volume_price(tapes, {"上证指数": -1.0}) == ""

    def test_missing_tape_does_not_raise(self):
        assert describe_volume_price([], {"上证指数": 2.0}) == ""
