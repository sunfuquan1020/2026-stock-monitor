"""异动检测模块。

滞后信号: price_surge/drop, volume_spike, consecutive_move
预测信号(威科夫): distribution_warning(派发), volume_dryup(地量),
lone_strength(独苗背离), momentum_exhaustion(量能衰竭)
"""

import statistics
from datetime import date

from src.models import (
    Anomaly,
    AnomalyType,
    DailyQuote,
    SectorSignal,
    Severity,
    StockConfig,
    ThresholdConfig,
)

# --- 预测性检测器阈值 ---
DIST_NEAR_HIGH_PCT = 0.95        # 收盘价 ≥ 60日高点的95% 视为高位
DIST_MIN_RUNUP = 0.15            # 60日低点到高点涨幅 ≥15% 才算经历过拉升
DIST_STALL_CHANGE = 1.5          # 滞涨: 当日涨跌幅绝对值 < 此值
DIST_STALL_VOL_RATIO = 1.8       # 滞涨需放量 ≥ 1.8倍20日均量
DIST_UTAD_VOL_RATIO = 0.7        # 缩量创新高: 量 ≤ 0.7倍均量
DRYUP_DRAWDOWN = 0.15            # 距60日高点回撤 ≥15% 后才谈地量
DRYUP_VOL_RATIO = 0.5            # 地量: 连续2日量 < 0.5倍20日均量
DRYUP_VOL_RATIO_HIGH = 0.35      # 极端地量 → high
LONE_SECTOR_DROP = -2.0          # 板块中位跌幅 ≤ 此值
LONE_STOCK_GAIN = 3.0            # 单票逆势涨幅 ≥ 此值 → 独苗
LONE_MIN_PEERS = 4               # 板块至少4只才有统计意义
EXHAUST_MIN_DAYS = 3             # 连涨天数
ZSCORE_HIGH = 3.0                # 涨跌幅 z-score ≥3 → severity 升级为 high
ZSCORE_MIN_HISTORY = 10          # 计算z-score所需最少历史天数


def detect_anomalies(
    quotes: dict[str, list[DailyQuote]],
    watchlist: list[StockConfig],
    thresholds: ThresholdConfig,
) -> list[Anomaly]:
    """检测所有股票的异动。

    Args:
        quotes: 股票代码到DailyQuote列表的映射
        watchlist: 自选股配置
        thresholds: 检测阈值

    Returns:
        检测到的异动列表
    """
    name_map = {s.symbol: s.name for s in watchlist}
    anomalies = []

    for symbol, daily_quotes in quotes.items():
        name = name_map.get(symbol, symbol)
        anomalies.extend(_detect_price_change(symbol, name, daily_quotes, thresholds))
        anomalies.extend(_detect_volume_spike(symbol, name, daily_quotes, thresholds))
        anomalies.extend(_detect_consecutive_move(symbol, name, daily_quotes, thresholds))
        anomalies.extend(_detect_distribution_warning(symbol, name, daily_quotes))
        anomalies.extend(_detect_volume_dryup(symbol, name, daily_quotes))
        anomalies.extend(_detect_momentum_exhaustion(symbol, name, daily_quotes, thresholds))

    # 需要板块上下文的检测器
    anomalies.extend(detect_lone_strength(quotes, watchlist))

    return anomalies


def _detect_price_change(
    symbol: str,
    name: str,
    quotes: list[DailyQuote],
    thresholds: ThresholdConfig,
) -> list[Anomaly]:
    """检测价格异动（单日涨跌幅超阈值）。"""
    if len(quotes) < 2:
        return []

    today = quotes[-1]
    change = today.change_pct

    if abs(change) < thresholds.price_change_pct:
        return []

    if change > 0:
        anomaly_type = AnomalyType.PRICE_SURGE
    else:
        anomaly_type = AnomalyType.PRICE_DROP

    severity = _price_change_severity(abs(change))
    details = {
        "change_pct": round(change, 2),
        "close": today.close,
        "volume": today.volume,
    }

    # 波动率归一化: 低波动股(如茅台)的-5%远比高波动股(半导体)的-5%异常
    z = _change_zscore(quotes)
    if z is not None:
        details["z_score"] = z
        if abs(z) >= ZSCORE_HIGH:
            severity = Severity.HIGH  # 只升级不降级

    return [
        Anomaly(
            symbol=symbol,
            name=name,
            anomaly_type=anomaly_type,
            severity=severity,
            details=details,
            detected_date=today.date,
        )
    ]


def _change_zscore(quotes: list[DailyQuote]) -> float | None:
    """当日涨跌幅相对自身历史波动的z-score（历史不足返回None）。"""
    history = [q.change_pct for q in quotes[:-1]][-20:]
    if len(history) < ZSCORE_MIN_HISTORY:
        return None
    std = statistics.pstdev(history)
    if std < 0.1:  # 波动过小视为无效
        return None
    return round(quotes[-1].change_pct / std, 1)


def _detect_volume_spike(
    symbol: str,
    name: str,
    quotes: list[DailyQuote],
    thresholds: ThresholdConfig,
) -> list[Anomaly]:
    """检测放量异动（成交量超过N倍均量）。"""
    if len(quotes) < 21:  # 需要至少20天历史数据
        return []

    today = quotes[-1]
    historical = quotes[-21:-1]  # 最近20天（不含今天）
    avg_volume = sum(q.volume for q in historical) / len(historical)

    if avg_volume == 0:
        return []

    ratio = today.volume / avg_volume

    if ratio < thresholds.volume_spike_ratio:
        return []

    severity = _volume_spike_severity(ratio)

    return [
        Anomaly(
            symbol=symbol,
            name=name,
            anomaly_type=AnomalyType.VOLUME_SPIKE,
            severity=severity,
            details={
                "volume_ratio": round(ratio, 2),
                "today_volume": today.volume,
                "avg_volume_20d": int(avg_volume),
                "price_change_pct": today.change_pct,
            },
            detected_date=today.date,
        )
    ]


def _detect_consecutive_move(
    symbol: str,
    name: str,
    quotes: list[DailyQuote],
    thresholds: ThresholdConfig,
) -> list[Anomaly]:
    """检测连续走势（连续N天同方向变化）。"""
    if len(quotes) < thresholds.consecutive_days + 1:
        return []

    # 从最新一天开始，向前数连续同方向的天数
    count = 0
    cumulative_change = 0.0
    direction = 0  # 1=涨，-1=跌

    for i in range(len(quotes) - 1, 0, -1):
        change = quotes[i].change_pct
        if change == 0:
            break

        current_dir = 1 if change > 0 else -1

        if count == 0:
            direction = current_dir
            count = 1
            cumulative_change = change
        elif current_dir == direction:
            count += 1
            cumulative_change += change
        else:
            break

    if count < thresholds.consecutive_days:
        return []

    if abs(cumulative_change) < thresholds.consecutive_change_pct:
        return []

    if direction > 0:
        anomaly_type = AnomalyType.CONSECUTIVE_MOVE
        direction_text = "连续上涨"
    else:
        anomaly_type = AnomalyType.CONSECUTIVE_MOVE
        direction_text = "连续下跌"

    severity = _consecutive_move_severity(count)

    return [
        Anomaly(
            symbol=symbol,
            name=name,
            anomaly_type=anomaly_type,
            severity=severity,
            details={
                "consecutive_days": count,
                "cumulative_change_pct": round(cumulative_change, 2),
                "direction": direction_text,
            },
            detected_date=quotes[-1].date,
        )
    ]


def _price_change_severity(abs_change: float) -> Severity:
    """根据涨跌幅确定严重程度。"""
    if abs_change >= 7.0:
        return Severity.HIGH
    elif abs_change >= 5.0:
        return Severity.MEDIUM
    return Severity.LOW


def _volume_spike_severity(ratio: float) -> Severity:
    """根据放量倍数确定严重程度。"""
    if ratio >= 4.0:
        return Severity.HIGH
    elif ratio >= 2.5:
        return Severity.MEDIUM
    return Severity.LOW


def _consecutive_move_severity(days: int) -> Severity:
    """根据连续天数确定严重程度。"""
    if days >= 5:
        return Severity.HIGH
    elif days >= 3:
        return Severity.MEDIUM
    return Severity.LOW


# ═══════════════════════════════════════════════════════════
# 预测性检测器（威科夫框架, 提前于价格崩溃发信号）
# ═══════════════════════════════════════════════════════════


def _avg_volume_20d(quotes: list[DailyQuote]) -> float:
    """今天以外最近20日均量; 数据不足返回0。"""
    historical = quotes[-21:-1]
    if len(historical) < 20:
        return 0.0
    return sum(q.volume for q in historical) / len(historical)


def _detect_distribution_warning(
    symbol: str, name: str, quotes: list[DailyQuote]
) -> list[Anomaly]:
    """派发预警: 经历拉升后处于高位, 出现放量滞涨 或 缩量创新高(UTAD)。

    这是威科夫框架里唯一能提前于崩溃的信号——
    "高位明显缩量滞涨 = 至少暂时性调整"。
    """
    if len(quotes) < 21:
        return []

    today = quotes[-1]
    window = quotes[-60:] if len(quotes) >= 60 else quotes
    high = max(q.close for q in window)
    low = min(q.close for q in window)

    # 必须"经历过拉升且仍在高位"
    if low <= 0 or (high / low - 1) < DIST_MIN_RUNUP:
        return []
    if today.close < high * DIST_NEAR_HIGH_PCT:
        return []

    avg_vol = _avg_volume_20d(quotes)
    if avg_vol <= 0:
        return []
    vol_ratio = today.volume / avg_vol

    is_new_high = today.close >= max(q.close for q in window[:-1])
    stall = abs(today.change_pct) < DIST_STALL_CHANGE and vol_ratio >= DIST_STALL_VOL_RATIO
    utad = is_new_high and vol_ratio <= DIST_UTAD_VOL_RATIO and today.change_pct > 0

    if not stall and not utad:
        return []

    signal = "高位放量滞涨" if stall else "缩量创新高(UTAD)"
    severity = Severity.HIGH if (utad or vol_ratio >= 2.5) else Severity.MEDIUM

    return [
        Anomaly(
            symbol=symbol,
            name=name,
            anomaly_type=AnomalyType.DISTRIBUTION_WARNING,
            severity=severity,
            details={
                "signal": signal,
                "close": today.close,
                "change_pct": round(today.change_pct, 2),
                "vol_ratio_vs_20d": round(vol_ratio, 2),
                "dist_from_60d_high_pct": round((today.close / high - 1) * 100, 1),
            },
            detected_date=today.date,
        )
    ]


def _detect_volume_dryup(
    symbol: str, name: str, quotes: list[DailyQuote]
) -> list[Anomaly]:
    """地量检测: 深度回撤后连续2日量能枯竭 → Markdown尾声候选（左侧观察信号）。"""
    if len(quotes) < 22:
        return []

    today = quotes[-1]
    window = quotes[-60:] if len(quotes) >= 60 else quotes
    high = max(q.close for q in window)
    if high <= 0 or today.close > high * (1 - DRYUP_DRAWDOWN):
        return []  # 回撤不足, 不谈地量

    avg_vol = _avg_volume_20d(quotes)
    if avg_vol <= 0:
        return []

    last2_ratios = [q.volume / avg_vol for q in quotes[-2:]]
    if not all(r < DRYUP_VOL_RATIO for r in last2_ratios):
        return []

    severity = (
        Severity.HIGH
        if all(r < DRYUP_VOL_RATIO_HIGH for r in last2_ratios)
        else Severity.MEDIUM
    )

    return [
        Anomaly(
            symbol=symbol,
            name=name,
            anomaly_type=AnomalyType.VOLUME_DRYUP,
            severity=severity,
            details={
                "vol_ratio_last2": [round(r, 2) for r in last2_ratios],
                "drawdown_from_60d_high_pct": round((today.close / high - 1) * 100, 1),
                "close": today.close,
            },
            detected_date=today.date,
        )
    ]


def _detect_momentum_exhaustion(
    symbol: str, name: str, quotes: list[DailyQuote], thresholds: ThresholdConfig
) -> list[Anomaly]:
    """量能衰竭: 连涨≥3日但成交量逐日递减（Effort递减, Result难以为继）。"""
    if len(quotes) < EXHAUST_MIN_DAYS + 1:
        return []

    recent = quotes[-EXHAUST_MIN_DAYS:]
    if not all(q.change_pct > 0 for q in recent):
        return []

    volumes = [q.volume for q in recent]
    if not all(volumes[i] > volumes[i + 1] for i in range(len(volumes) - 1)):
        return []
    if 0 in volumes:
        return []

    cumulative = sum(q.change_pct for q in recent)
    if cumulative < thresholds.consecutive_change_pct:
        return []

    return [
        Anomaly(
            symbol=symbol,
            name=name,
            anomaly_type=AnomalyType.MOMENTUM_EXHAUSTION,
            severity=Severity.MEDIUM,
            details={
                "up_days": EXHAUST_MIN_DAYS,
                "cumulative_change_pct": round(cumulative, 2),
                "volume_trend": volumes,
            },
            detected_date=quotes[-1].date,
        )
    ]


def detect_lone_strength(
    quotes: dict[str, list[DailyQuote]],
    watchlist: list[StockConfig],
) -> list[Anomaly]:
    """独苗背离: 板块中位数大跌而单票逆势大涨 → 补跌风险。

    实证: 07-14 新易盛在半导体板块普跌中逆势+11%, 07-18 跌停补跌。
    """
    sector_map = {s.symbol: s.sector for s in watchlist}
    name_map = {s.symbol: s.name for s in watchlist}

    # 板块 → [(symbol, change)]
    by_sector: dict[str, list[tuple[str, float]]] = {}
    for symbol, dq in quotes.items():
        if not dq:
            continue
        sector = sector_map.get(symbol, "")
        if sector:
            by_sector.setdefault(sector, []).append((symbol, dq[-1].change_pct))

    anomalies = []
    for sector, members in by_sector.items():
        if len(members) < LONE_MIN_PEERS:
            continue
        median = statistics.median(c for _, c in members)
        if median > LONE_SECTOR_DROP:
            continue
        for symbol, change in members:
            if change >= LONE_STOCK_GAIN:
                dq = quotes[symbol]
                anomalies.append(
                    Anomaly(
                        symbol=symbol,
                        name=name_map.get(symbol, symbol),
                        anomaly_type=AnomalyType.LONE_STRENGTH,
                        severity=Severity.HIGH,
                        details={
                            "change_pct": round(change, 2),
                            "sector": sector,
                            "sector_median_pct": round(median, 2),
                            "note": "板块普跌中逆势极强, 警惕最后补跌",
                        },
                        detected_date=dq[-1].date,
                    )
                )
    return anomalies


def aggregate_sector_signals(
    quotes: dict[str, list[DailyQuote]],
    watchlist: list[StockConfig],
    anomalies: list[Anomaly],
) -> list[SectorSignal]:
    """板块聚合信号: 识别板块效应（主线故事是板块级的, 单票视角看不见结构）。"""
    sector_map = {s.symbol: s.sector for s in watchlist}
    anomaly_counts: dict[str, int] = {}
    for a in anomalies:
        sector = sector_map.get(a.symbol, "")
        if sector:
            anomaly_counts[sector] = anomaly_counts.get(sector, 0) + 1

    by_sector: dict[str, list[float]] = {}
    for symbol, dq in quotes.items():
        if not dq:
            continue
        sector = sector_map.get(symbol, "")
        if sector:
            by_sector.setdefault(sector, []).append(dq[-1].change_pct)

    signals = []
    for sector, changes in by_sector.items():
        if len(changes) < 2:
            continue
        signals.append(
            SectorSignal(
                sector=sector,
                total=len(changes),
                up_count=sum(1 for c in changes if c > 0),
                down_count=sum(1 for c in changes if c < 0),
                median_change_pct=round(statistics.median(changes), 2),
                anomaly_count=anomaly_counts.get(sector, 0),
            )
        )
    # 按板块异动强度排序（|中位涨跌幅| 优先）
    signals.sort(key=lambda s: abs(s.median_change_pct), reverse=True)
    return signals
