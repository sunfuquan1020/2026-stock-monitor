"""Tests for anomaly detection."""

from datetime import date

from src.anomaly import (
    detect_anomalies,
    _detect_price_change,
    _detect_volume_spike,
    _detect_consecutive_move,
    _price_change_severity,
    _volume_spike_severity,
    _consecutive_move_severity,
)
from src.models import (
    AnomalyType,
    DailyQuote,
    Severity,
    StockConfig,
    ThresholdConfig,
)


def make_quote(
    symbol="000001",
    d=date(2026, 5, 20),
    open_=10.0,
    close=10.0,
    high=10.5,
    low=9.5,
    volume=1000000,
    turnover=10000000.0,
    change_pct=0.0,
) -> DailyQuote:
    return DailyQuote(
        symbol=symbol,
        date=d,
        open=open_,
        close=close,
        high=high,
        low=low,
        volume=volume,
        turnover=turnover,
        change_pct=change_pct,
    )


def make_quotes(n: int, base_volume: int = 1000000) -> list[DailyQuote]:
    """生成n天的正常行情数据。"""
    quotes = []
    base = date(2026, 5, 1)
    for i in range(n):
        d = date.fromordinal(base.toordinal() - (n - i))
        quotes.append(
            make_quote(
                d=d,
                close=10.0 + i * 0.01,
                volume=base_volume,
                change_pct=0.1,
            )
        )
    return quotes


DEFAULT_THRESHOLDS = ThresholdConfig()
WATCHLIST = [StockConfig(symbol="000001", name="平安银行", sector="金融")]


class TestPriceChange:
    def test_no_anomaly_within_threshold(self):
        quotes = [make_quote(), make_quote(change_pct=3.0)]
        result = _detect_price_change("000001", "平安银行", quotes, DEFAULT_THRESHOLDS)
        assert result == []

    def test_detects_surge(self):
        quotes = [make_quote(), make_quote(change_pct=6.0)]
        result = _detect_price_change("000001", "平安银行", quotes, DEFAULT_THRESHOLDS)
        assert len(result) == 1
        assert result[0].anomaly_type == AnomalyType.PRICE_SURGE
        assert result[0].severity == Severity.MEDIUM

    def test_detects_drop(self):
        quotes = [make_quote(), make_quote(change_pct=-8.0)]
        result = _detect_price_change("000001", "平安银行", quotes, DEFAULT_THRESHOLDS)
        assert len(result) == 1
        assert result[0].anomaly_type == AnomalyType.PRICE_DROP
        assert result[0].severity == Severity.HIGH

    def test_insufficient_data(self):
        quotes = [make_quote()]
        result = _detect_price_change("000001", "平安银行", quotes, DEFAULT_THRESHOLDS)
        assert result == []


class TestVolumeSpike:
    def test_no_anomaly_within_threshold(self):
        quotes = make_quotes(21)
        result = _detect_volume_spike("000001", "平安银行", quotes, DEFAULT_THRESHOLDS)
        assert result == []

    def test_detects_volume_spike(self):
        quotes = make_quotes(21, base_volume=1000000)
        # 最后一天放量
        quotes[-1] = make_quote(volume=3000000, change_pct=2.0)
        result = _detect_volume_spike("000001", "平安银行", quotes, DEFAULT_THRESHOLDS)
        assert len(result) == 1
        assert result[0].anomaly_type == AnomalyType.VOLUME_SPIKE
        assert result[0].details["volume_ratio"] == 3.0

    def test_insufficient_data(self):
        quotes = make_quotes(10)
        result = _detect_volume_spike("000001", "平安银行", quotes, DEFAULT_THRESHOLDS)
        assert result == []


class TestConsecutiveMove:
    def test_no_anomaly_short_streak(self):
        quotes = make_quotes(21, base_volume=1000000)
        # 前面都是微涨，最后2天连续上涨但不够3天
        for i in range(19):
            quotes[i] = make_quote(d=quotes[i].date, volume=1000000, change_pct=0.1)
        quotes[-1] = make_quote(d=quotes[-1].date, change_pct=2.0)
        quotes[-2] = make_quote(d=quotes[-2].date, change_pct=1.0)
        quotes[-3] = make_quote(d=quotes[-3].date, change_pct=-0.5)  # 打断连续
        result = _detect_consecutive_move("000001", "平安银行", quotes, DEFAULT_THRESHOLDS)
        assert result == []

    def test_detects_consecutive_rise(self):
        quotes = make_quotes(21)
        # 3天连续上涨，累计超过3%
        quotes[-1] = make_quote(change_pct=1.5)
        quotes[-2] = make_quote(change_pct=1.0)
        quotes[-3] = make_quote(change_pct=1.0)
        result = _detect_consecutive_move("000001", "平安银行", quotes, DEFAULT_THRESHOLDS)
        assert len(result) == 1
        assert result[0].anomaly_type == AnomalyType.CONSECUTIVE_MOVE
        assert result[0].details["direction"] == "连续上涨"

    def test_detects_consecutive_fall(self):
        quotes = make_quotes(21)
        # 4天连续下跌
        quotes[-1] = make_quote(change_pct=-1.0)
        quotes[-2] = make_quote(change_pct=-1.0)
        quotes[-3] = make_quote(change_pct=-0.5)
        quotes[-4] = make_quote(change_pct=-0.5)
        result = _detect_consecutive_move("000001", "平安银行", quotes, DEFAULT_THRESHOLDS)
        assert len(result) == 1
        assert result[0].details["direction"] == "连续下跌"


class TestSeverity:
    def test_price_change_severity(self):
        assert _price_change_severity(4.0) == Severity.LOW
        assert _price_change_severity(6.0) == Severity.MEDIUM
        assert _price_change_severity(8.0) == Severity.HIGH

    def test_volume_spike_severity(self):
        assert _volume_spike_severity(2.0) == Severity.LOW
        assert _volume_spike_severity(3.0) == Severity.MEDIUM
        assert _volume_spike_severity(5.0) == Severity.HIGH

    def test_consecutive_move_severity(self):
        assert _consecutive_move_severity(2) == Severity.LOW
        assert _consecutive_move_severity(4) == Severity.MEDIUM
        assert _consecutive_move_severity(6) == Severity.HIGH


class TestDetectAnomalies:
    def test_integration(self):
        # 构造一个有价格异动的数据
        quotes = make_quotes(21)
        quotes[-1] = make_quote(change_pct=7.0, volume=3000000)
        # 前20天正常
        for i in range(20):
            quotes[i] = make_quote(volume=1000000, change_pct=0.1)

        data = {"000001": quotes}
        result = detect_anomalies(data, WATCHLIST, DEFAULT_THRESHOLDS)

        # 应该检测到价格异动和放量异动
        types = {a.anomaly_type for a in result}
        assert AnomalyType.PRICE_SURGE in types
        assert AnomalyType.VOLUME_SPIKE in types

    def test_empty_data(self):
        result = detect_anomalies({}, WATCHLIST, DEFAULT_THRESHOLDS)
        assert result == []
