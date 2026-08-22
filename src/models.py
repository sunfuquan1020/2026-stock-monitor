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
    # 预测性信号（威科夫框架，提前于价格崩溃）
    DISTRIBUTION_WARNING = "distribution_warning"  # 派发预警: 高位放量滞涨 / 缩量创新高(UTAD)
    VOLUME_DRYUP = "volume_dryup"                  # 地量: 深度回撤后量能枯竭, Markdown尾声候选
    LONE_STRENGTH = "lone_strength"                # 独苗背离: 板块普跌中单票逆势极强, 补跌风险
    MOMENTUM_EXHAUSTION = "momentum_exhaustion"    # 量能衰竭: 连涨但量逐日递减


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
class IndexQuote:
    """指数行情快照。"""
    symbol: str
    name: str
    price: float
    change_pct: float


@dataclass(frozen=True)
class IndexTape:
    """指数量价结构（mootdx 日K衍生，腾讯简版行情拿不到成交量）。"""
    symbol: str
    name: str
    vol_ratio_20d: float        # 当日成交量 / 20日均量
    vol_trend: str              # 5日均量 vs 20日均量: 放量/平量/缩量
    vs_ma20_pct: float          # 现价相对MA20
    vs_ma60_pct: float          # 现价相对MA60（Markdown/Markup 分界）
    dist_60d_high_pct: float


@dataclass(frozen=True)
class StyleStrength:
    """小盘相对强度: 中证1000 / 沪深300。比值上行 = 小盘跑赢。"""
    ratio: float
    chg_5d_pct: float
    chg_20d_pct: float
    chg_60d_pct: float


@dataclass(frozen=True)
class MarketBreadth:
    """A股全市场宽度（涨跌/涨跌停家数）。"""
    up_count: int
    down_count: int
    flat_count: int
    limit_up: int
    limit_down: int

    @property
    def down_ratio(self) -> float:
        total = self.up_count + self.down_count
        return self.down_count / total if total else 0.0


@dataclass(frozen=True)
class MarginSnapshot:
    """两融余额快照（沪市，亿元）。"""
    trade_date: str
    balance_yi: float
    change_yi: float


@dataclass(frozen=True)
class MarketThermometer:
    """市场体温计: 指数 + 宽度 + 两融 + regime状态机。"""
    cn_indexes: tuple[IndexQuote, ...] = ()
    global_indexes: tuple[IndexQuote, ...] = ()
    index_tapes: tuple[IndexTape, ...] = ()   # 指数量价（按 name 与 cn_indexes 对应）
    style_strength: StyleStrength | None = None
    volume_price_note: str = ""               # 量价一句话提示（不参与 regime 判定）
    breadth: MarketBreadth | None = None
    margin: MarginSnapshot | None = None
    regime: str = "未知"
    regime_reasons: tuple[str, ...] = ()
    regime_history: tuple[str, ...] = ()  # 近5日 "date:regime" 轨迹


@dataclass(frozen=True)
class SectorSignal:
    """板块聚合信号（识别板块效应 vs 单票行为）。"""
    sector: str
    total: int
    up_count: int
    down_count: int
    median_change_pct: float
    anomaly_count: int


@dataclass(frozen=True)
class CalendarEvent:
    """未来风险日历事件。"""
    event_date: str  # ISO日期
    category: str    # 财报 / 解禁 / 新股
    symbol: str
    name: str
    detail: str


@dataclass(frozen=True)
class FundFlowInfo:
    """A股主力资金流（当日）。

    source: "东财" 或 "新浪"。两者口径不同(新浪按大单方向聚合)，不可混算；
    新浪源不提供净占比，main_net_pct 为 None。
    """
    symbol: str
    name: str
    main_net_inflow_yi: float   # 主力净流入(亿)
    main_net_pct: float | None  # 主力净占比(%)，新浪源为 None
    source: str = "东财"


@dataclass(frozen=True)
class LhbEntry:
    """龙虎榜命中记录。"""
    symbol: str
    name: str
    trade_date: str
    reason: str
    net_buy_yi: float


@dataclass(frozen=True)
class NorthboundFlow:
    """北向资金当日净流入（同花顺 hexin, 非东财, 市场级）。

    东财北向字段自 2024-08 上游断供, 改用同花顺 hsgtApi 作非东财替代源。
    单位: 亿元。沪股通/深股通任一异常时对应字段为 None。
    """
    hgt_net_yi: float | None   # 沪股通当日累计净买入(亿)
    sgt_net_yi: float | None   # 深股通当日累计净买入(亿)
    as_of: str                 # 数据时点 (HH:MM)

    @property
    def total_net_yi(self) -> float | None:
        if self.hgt_net_yi is None and self.sgt_net_yi is None:
            return None
        return round((self.hgt_net_yi or 0.0) + (self.sgt_net_yi or 0.0), 2)


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
    thermometer: "MarketThermometer | None" = None
    sector_signals: tuple[SectorSignal, ...] = ()
    calendar_events: tuple[CalendarEvent, ...] = ()
    fund_flows: tuple[FundFlowInfo, ...] = ()
    lhb_entries: tuple[LhbEntry, ...] = ()
    northbound: "NorthboundFlow | None" = None
    northbound_top10: tuple[dict, ...] = ()  # HKEX 北向十大活跃股(成交额口径)
    data_warnings: tuple[str, ...] = ()
