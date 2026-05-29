"""Immutable data models for the stock monitoring system."""

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Any


class AnomalyType(str, Enum):
    PRICE_SURGE = "price_surge"
    PRICE_DROP = "price_drop"
    VOLUME_SPIKE = "volume_spike"
    CONSECUTIVE_MOVE = "consecutive_move"


class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class HypothesisStatus(str, Enum):
    ACTIVE = "active"
    CONFIRMED = "confirmed"
    INVALIDATED = "invalidated"
    NEEDS_REVIEW = "needs_review"


@dataclass(frozen=True)
class StockConfig:
    symbol: str
    name: str
    sector: str
    market: str = "A股"


@dataclass(frozen=True)
class ThresholdConfig:
    price_change_pct: float = 5.0
    volume_spike_ratio: float = 2.5
    consecutive_days: int = 3
    consecutive_change_pct: float = 3.0
    lookback_days: int = 30


@dataclass(frozen=True)
class DailyQuote:
    symbol: str
    date: date
    open: float
    close: float
    high: float
    low: float
    volume: int
    turnover: float
    change_pct: float


@dataclass(frozen=True)
class Anomaly:
    symbol: str
    name: str
    anomaly_type: AnomalyType
    severity: Severity
    details: dict[str, Any] = field(default_factory=dict)
    detected_date: date = field(default_factory=date.today)


@dataclass(frozen=True)
class NewsItem:
    title: str
    source: str
    url: str
    publish_time: str
    content_snippet: str


@dataclass(frozen=True)
class AnalysisResult:
    symbol: str
    anomaly: Anomaly | None
    news_items: tuple[NewsItem, ...]
    claude_analysis: str
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass(frozen=True)
class Hypothesis:
    id: str
    text: str
    related_symbols: tuple[str, ...]
    status: HypothesisStatus = HypothesisStatus.ACTIVE


@dataclass(frozen=True)
class HypothesisUpdate:
    hypothesis_id: str
    hypothesis_text: str
    new_evidence: str
    claude_assessment: str
    suggested_status: HypothesisStatus
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass(frozen=True)
class ReportData:
    date: date
    anomalies: tuple[Anomaly, ...]
    analyses: tuple[AnalysisResult, ...]
    hypothesis_updates: tuple[HypothesisUpdate, ...]
    market_summary: str
