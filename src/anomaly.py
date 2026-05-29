"""异动检测模块。"""

from datetime import date

from src.models import (
    Anomaly,
    AnomalyType,
    DailyQuote,
    Severity,
    StockConfig,
    ThresholdConfig,
)


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

    return [
        Anomaly(
            symbol=symbol,
            name=name,
            anomaly_type=anomaly_type,
            severity=severity,
            details={
                "change_pct": round(change, 2),
                "close": today.close,
                "volume": today.volume,
            },
            detected_date=today.date,
        )
    ]


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
