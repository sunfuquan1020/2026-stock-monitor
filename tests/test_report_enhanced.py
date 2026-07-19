"""Tests for report enhancements: anomaly merging, Chinese JSON, new sections."""

from datetime import date
from pathlib import Path

from src.models import (
    Anomaly,
    AnomalyType,
    IndexQuote,
    MarketBreadth,
    MarketThermometer,
    ReportData,
    SectorSignal,
    Severity,
)
from src.report import generate_report, merge_anomaly_rows

TEMPLATE_DIR = str(Path(__file__).parent.parent / "templates")


def make_anomaly(symbol="600519", name="茅台", atype=AnomalyType.PRICE_DROP,
                 severity=Severity.MEDIUM, details=None) -> Anomaly:
    return Anomaly(
        symbol=symbol,
        name=name,
        anomaly_type=atype,
        severity=severity,
        details=details or {"change_pct": -5.0, "direction": "连续下跌"},
        detected_date=date(2026, 7, 18),
    )


class TestMergeAnomalyRows:
    def test_same_symbol_merged_with_max_severity(self):
        anomalies = (
            make_anomaly(atype=AnomalyType.PRICE_DROP, severity=Severity.MEDIUM),
            make_anomaly(atype=AnomalyType.CONSECUTIVE_MOVE, severity=Severity.HIGH),
        )
        rows = merge_anomaly_rows(anomalies)
        assert len(rows) == 1
        assert rows[0]["severity"] == "high"
        assert set(rows[0]["types"]) == {"price_drop", "consecutive_move"}
        assert "price_drop" in rows[0]["details"]

    def test_high_sorted_first(self):
        anomalies = (
            make_anomaly(symbol="A", severity=Severity.MEDIUM),
            make_anomaly(symbol="B", severity=Severity.HIGH),
        )
        rows = merge_anomaly_rows(anomalies)
        assert rows[0]["symbol"] == "B"


class TestReportRendering:
    def _data(self) -> ReportData:
        return ReportData(
            date=date(2026, 7, 18),
            anomalies=(make_anomaly(),),
            analyses=(),
            hypothesis_updates=(),
            market_summary="test",
            thermometer=MarketThermometer(
                cn_indexes=(IndexQuote("sh000001", "上证指数", 3764.15, -3.05),),
                breadth=MarketBreadth(500, 4500, 100, 5, 176),
                regime="杀跌",
                regime_reasons=("跌停 176 家",),
                regime_history=("07-17:熄火", "07-18:杀跌"),
            ),
            sector_signals=(SectorSignal("半导体", 10, 1, 9, -7.0, 8),),
            data_warnings=("美股数据疑似未更新",),
        )

    def test_renders_chinese_unescaped_in_details(self):
        content = generate_report(self._data(), TEMPLATE_DIR)
        assert "连续下跌" in content
        assert "\\u" not in content

    def test_renders_new_sections(self):
        content = generate_report(self._data(), TEMPLATE_DIR)
        assert "市场体温计" in content
        assert "Regime: 杀跌" in content
        assert "07-17:熄火 → 07-18:杀跌" in content
        assert "板块聚合信号" in content
        assert "数据质量警告" in content
        assert "跌停 176" in content

    def test_renders_without_optional_sections(self):
        data = ReportData(
            date=date(2026, 7, 18),
            anomalies=(),
            analyses=(),
            hypothesis_updates=(),
            market_summary="test",
        )
        content = generate_report(data, TEMPLATE_DIR)
        assert "市场体温计" not in content
        assert "今日无异常波动信号" in content
