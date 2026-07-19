"""Tests for predictive (Wyckoff) anomaly detectors and z-score severity."""

from datetime import date

from src.anomaly import (
    _change_zscore,
    _detect_distribution_warning,
    _detect_momentum_exhaustion,
    _detect_price_change,
    _detect_volume_dryup,
    aggregate_sector_signals,
    detect_lone_strength,
)
from src.models import (
    AnomalyType,
    DailyQuote,
    Severity,
    StockConfig,
    ThresholdConfig,
)

BASE = date(2026, 6, 1)


def q(i: int, close: float, volume: int, change_pct: float) -> DailyQuote:
    return DailyQuote(
        symbol="TEST",
        date=date.fromordinal(BASE.toordinal() + i),
        open=close,
        close=close,
        high=close * 1.02,
        low=close * 0.98,
        volume=volume,
        turnover=close * volume,
        change_pct=change_pct,
    )


def flat_history(n: int, close=10.0, volume=1_000_000) -> list[DailyQuote]:
    return [q(i, close, volume, 0.1) for i in range(n)]


class TestDistributionWarning:
    def _runup_history(self) -> list[DailyQuote]:
        """30天从10拉到15(涨幅50%), 高位。"""
        quotes = []
        for i in range(30):
            close = 10.0 + i * 5.0 / 29
            quotes.append(q(i, close, 1_000_000, 1.5))
        return quotes

    def test_high_volume_stall_triggers(self):
        quotes = self._runup_history()
        # 高位滞涨 + 2.5倍放量
        quotes.append(q(30, 15.02, 2_500_000, 0.1))
        result = _detect_distribution_warning("TEST", "测试", quotes)
        assert len(result) == 1
        assert result[0].anomaly_type == AnomalyType.DISTRIBUTION_WARNING
        assert result[0].severity == Severity.HIGH
        assert result[0].details["signal"] == "高位放量滞涨"

    def test_utad_shrinking_volume_new_high(self):
        quotes = self._runup_history()
        # 缩量创新高
        quotes.append(q(30, 15.20, 500_000, 1.2))
        result = _detect_distribution_warning("TEST", "测试", quotes)
        assert len(result) == 1
        assert "UTAD" in result[0].details["signal"]

    def test_no_trigger_without_runup(self):
        quotes = flat_history(30)
        quotes.append(q(30, 10.05, 2_500_000, 0.1))  # 放量滞涨但没经历拉升
        assert _detect_distribution_warning("TEST", "测试", quotes) == []

    def test_no_trigger_when_below_high(self):
        quotes = self._runup_history()
        quotes.append(q(30, 13.0, 2_500_000, 0.1))  # 已距高点>5%
        assert _detect_distribution_warning("TEST", "测试", quotes) == []


class TestVolumeDryup:
    def test_dryup_after_deep_drawdown(self):
        quotes = [q(i, 20.0, 1_000_000, 0.0) for i in range(25)]
        # 回撤到15 (-25%), 最近2天量枯竭
        quotes.append(q(25, 15.0, 400_000, -1.0))
        quotes.append(q(26, 15.0, 300_000, 0.0))
        result = _detect_volume_dryup("TEST", "测试", quotes)
        assert len(result) == 1
        assert result[0].anomaly_type == AnomalyType.VOLUME_DRYUP

    def test_extreme_dryup_is_high(self):
        quotes = [q(i, 20.0, 1_000_000, 0.0) for i in range(25)]
        quotes.append(q(25, 15.0, 300_000, -1.0))
        quotes.append(q(26, 15.0, 200_000, 0.0))
        result = _detect_volume_dryup("TEST", "测试", quotes)
        assert result[0].severity == Severity.HIGH

    def test_no_trigger_without_drawdown(self):
        quotes = flat_history(25)
        quotes.append(q(25, 10.0, 300_000, 0.0))
        quotes.append(q(26, 10.0, 300_000, 0.0))
        assert _detect_volume_dryup("TEST", "测试", quotes) == []


class TestMomentumExhaustion:
    def test_up_with_shrinking_volume_triggers(self):
        thresholds = ThresholdConfig()
        quotes = flat_history(10)
        quotes.append(q(10, 10.5, 3_000_000, 2.0))
        quotes.append(q(11, 11.0, 2_000_000, 2.0))
        quotes.append(q(12, 11.5, 1_000_000, 2.0))
        result = _detect_momentum_exhaustion("TEST", "测试", quotes, thresholds)
        assert len(result) == 1
        assert result[0].anomaly_type == AnomalyType.MOMENTUM_EXHAUSTION

    def test_no_trigger_when_volume_grows(self):
        thresholds = ThresholdConfig()
        quotes = flat_history(10)
        quotes.append(q(10, 10.5, 1_000_000, 2.0))
        quotes.append(q(11, 11.0, 2_000_000, 2.0))
        quotes.append(q(12, 11.5, 3_000_000, 2.0))
        assert _detect_momentum_exhaustion("TEST", "测试", quotes, thresholds) == []


class TestLoneStrength:
    def _sector_quotes(self):
        watchlist = [
            StockConfig(f"60000{i}", f"股{i}", "半导体", market="A股") for i in range(5)
        ]
        quotes = {}
        for i in range(4):
            quotes[f"60000{i}"] = [q(0, 10.0, 100, -4.0)]  # 板块普跌
        quotes["600004"] = [q(0, 10.0, 100, 11.0)]  # 独苗
        return quotes, watchlist

    def test_lone_strength_flagged(self):
        quotes, watchlist = self._sector_quotes()
        result = detect_lone_strength(quotes, watchlist)
        assert len(result) == 1
        assert result[0].symbol == "600004"
        assert result[0].anomaly_type == AnomalyType.LONE_STRENGTH
        assert result[0].severity == Severity.HIGH

    def test_no_flag_in_small_sector(self):
        watchlist = [
            StockConfig("600000", "A", "小板块", market="A股"),
            StockConfig("600001", "B", "小板块", market="A股"),
        ]
        quotes = {
            "600000": [q(0, 10.0, 100, -4.0)],
            "600001": [q(0, 10.0, 100, 11.0)],
        }
        assert detect_lone_strength(quotes, watchlist) == []


class TestZScoreSeverity:
    def test_low_vol_stock_upgraded_to_high(self):
        """茅台式低波动股 -5% 应升级为 high (z-score)。"""
        thresholds = ThresholdConfig()
        quotes = [q(i, 100.0, 1_000_000, 0.3 if i % 2 else -0.3) for i in range(20)]
        quotes.append(q(20, 95.0, 1_000_000, -5.0))
        result = _detect_price_change("TEST", "测试", quotes, thresholds)
        assert len(result) == 1
        assert result[0].severity == Severity.HIGH
        assert abs(result[0].details["z_score"]) >= 3.0

    def test_high_vol_stock_not_downgraded(self):
        """高波动股 -7.5% 保持 high (只升不降)。"""
        thresholds = ThresholdConfig()
        quotes = [q(i, 100.0, 1_000_000, 4.0 if i % 2 else -4.0) for i in range(20)]
        quotes.append(q(20, 92.5, 1_000_000, -7.5))
        result = _detect_price_change("TEST", "测试", quotes, thresholds)
        assert result[0].severity == Severity.HIGH

    def test_zscore_none_with_short_history(self):
        assert _change_zscore([q(0, 10, 100, 1.0)] * 3) is None


class TestSectorSignals:
    def test_aggregation_and_sorting(self):
        watchlist = [
            StockConfig("600000", "A", "半导体", market="A股"),
            StockConfig("600001", "B", "半导体", market="A股"),
            StockConfig("600002", "C", "消费", market="A股"),
            StockConfig("600003", "D", "消费", market="A股"),
        ]
        quotes = {
            "600000": [q(0, 10.0, 100, -8.0)],
            "600001": [q(0, 10.0, 100, -6.0)],
            "600002": [q(0, 10.0, 100, 0.5)],
            "600003": [q(0, 10.0, 100, 1.5)],
        }
        signals = aggregate_sector_signals(quotes, watchlist, [])
        assert signals[0].sector == "半导体"  # |中位| 最大排最前
        assert signals[0].median_change_pct == -7.0
        assert signals[0].down_count == 2
        assert signals[1].sector == "消费"
