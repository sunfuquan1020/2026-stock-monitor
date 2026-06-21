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
class AShareBasicInfo:
    """A股基本面快照（来自腾讯财经，估值 + 交易维度）。"""
    symbol: str
    name: str
    price: float
    change_pct: float
    pe_ttm: float          # 市盈率(TTM)
    pe_static: float       # 市盈率(静)
    pb: float              # 市净率
    mcap_yi: float         # 总市值(亿元)
    float_mcap_yi: float   # 流通市值(亿元)
    turnover_pct: float    # 换手率(%)
    vol_ratio: float       # 量比
    limit_up: float        # 涨停价
    limit_down: float      # 跌停价


@dataclass(frozen=True)
class GlobalStockBasicInfo:
    """美股/港股基本面快照 (来自 Yahoo quoteSummary)。"""
    symbol: str
    name: str
    market: str
    price: float
    pe_ttm: float          # 市盈率(TTM)
    forward_pe: float      # 前瞻市盈率
    pb: float              # 市净率
    peg: float             # PEG
    market_cap: float      # 总市值(原始, 本币)
    roe: float             # 净资产收益率(小数, 0.15=15%)
    profit_margin: float   # 净利率(小数)
    target_mean: float     # 分析师平均目标价
    recommendation: str    # 评级 (buy/hold/sell)


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
    a_share_basics: tuple[AShareBasicInfo, ...] = ()
    global_basics: tuple[GlobalStockBasicInfo, ...] = ()
