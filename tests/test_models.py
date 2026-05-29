"""Tests for data models."""

from datetime import date, datetime

from src.models import (
    Anomaly,
    AnomalyType,
    DailyQuote,
    Hypothesis,
    HypothesisStatus,
    HypothesisUpdate,
    NewsItem,
    ReportData,
    Severity,
    StockConfig,
    ThresholdConfig,
)


class TestStockConfig:
    def test_create_stock_config(self):
        stock = StockConfig(symbol="000001", name="平安银行", sector="金融")
        assert stock.symbol == "000001"
        assert stock.name == "平安银行"
        assert stock.sector == "金融"

    def test_immutable(self):
        stock = StockConfig(symbol="000001", name="平安银行", sector="金融")
        try:
            stock.symbol = "600519"
            assert False, "Should have raised FrozenInstanceError"
        except AttributeError:
            pass


class TestThresholdConfig:
    def test_defaults(self):
        t = ThresholdConfig()
        assert t.price_change_pct == 5.0
        assert t.volume_spike_ratio == 2.5
        assert t.consecutive_days == 3

    def test_custom_values(self):
        t = ThresholdConfig(price_change_pct=3.0, volume_spike_ratio=4.0)
        assert t.price_change_pct == 3.0
        assert t.volume_spike_ratio == 4.0


class TestDailyQuote:
    def test_create_quote(self):
        q = DailyQuote(
            symbol="000001",
            date=date(2026, 5, 20),
            open=10.0,
            close=10.5,
            high=10.8,
            low=9.9,
            volume=1000000,
            turnover=10500000.0,
            change_pct=5.0,
        )
        assert q.symbol == "000001"
        assert q.change_pct == 5.0


class TestAnomaly:
    def test_create_anomaly(self):
        a = Anomaly(
            symbol="000001",
            name="平安银行",
            anomaly_type=AnomalyType.PRICE_SURGE,
            severity=Severity.HIGH,
            details={"change_pct": 7.5},
        )
        assert a.anomaly_type == AnomalyType.PRICE_SURGE
        assert a.severity == Severity.HIGH


class TestHypothesis:
    def test_create_hypothesis(self):
        h = Hypothesis(
            id="h1",
            text="银行板块估值修复",
            related_symbols=("000001", "601398"),
        )
        assert h.status == HypothesisStatus.ACTIVE

    def test_status_transition(self):
        from dataclasses import replace

        h = Hypothesis(
            id="h1",
            text="test",
            related_symbols=("000001",),
        )
        h2 = replace(h, status=HypothesisStatus.NEEDS_REVIEW)
        assert h.status == HypothesisStatus.ACTIVE
        assert h2.status == HypothesisStatus.NEEDS_REVIEW


class TestReportData:
    def test_create_report_data(self):
        r = ReportData(
            date=date(2026, 5, 20),
            anomalies=(),
            analyses=(),
            hypothesis_updates=(),
            market_summary="今日市场平稳",
        )
        assert r.date == date(2026, 5, 20)
