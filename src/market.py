"""市场体温计: 指数 + 宽度 + 两融 + regime 状态机。

所有网络获取均容错降级——任何一路数据失败不影响 pipeline，
regime 用可用数据尽力判断。
"""

import json
import logging
import statistics
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import quote as urlquote

import httpx

from src.models import (
    DailyQuote,
    IndexQuote,
    MarginSnapshot,
    MarketBreadth,
    MarketThermometer,
    StockConfig,
)

logger = logging.getLogger(__name__)

TENCENT_INDEX_URL = "https://qt.gtimg.cn/q="
TENCENT_TIMEOUT = 10.0

# 腾讯简版行情 (s_ 前缀): 1~名称~代码~现价~涨跌~涨跌幅~...
CN_INDEXES = [
    ("s_sh000001", "上证指数"),
    ("s_sz399001", "深证成指"),
    ("s_sz399006", "创业板指"),
    ("s_sh000688", "科创50"),
]

YAHOO_CHART_URL = "https://query2.finance.yahoo.com/v8/finance/chart/"
YAHOO_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
YAHOO_TIMEOUT = 15.0

GLOBAL_INDEXES = [
    ("^IXIC", "纳斯达克"),
    ("^SOX", "费城半导体"),
    ("^HSI", "恒生指数"),
]

# 成长指数（regime判断的主线代理）
GROWTH_INDEX_NAMES = {"创业板指", "科创50"}

# 防御板块（watchlist sector 字段），其余视为进攻板块
DEFENSIVE_SECTORS = {
    "银行/券商/金融信息",
    "金融",
    "电力/通信/交通/装备",
    "消费/食品饮料/医药/农业",
}

REGIME_HISTORY_FILE = "market_regime_history.json"
REGIME_HISTORY_DAYS = 5

# regime 分档阈值
KILL_GROWTH_DROP = -4.0        # 成长指数单日跌幅 → 杀跌
KILL_MAIN_DROP = -2.5          # 上证跌幅 → 杀跌
KILL_LIMIT_DOWN = 80           # 跌停家数 → 杀跌
KILL_DOWN_RATIO = 0.85         # 下跌占比(配合上证-1.5) → 杀跌
WEAK_GROWTH_DROP = -1.5        # 成长指数走弱线（换挡/熄火判断入口）
SHIFT_SECTOR_GAIN = 1.5        # 有进攻板块中位涨幅 ≥ 此值 → 换挡
ATTACK_GROWTH_GAIN = 1.5       # 成长指数涨幅 → 进攻
ATTACK_DOWN_RATIO = 0.45       # 下跌占比上限 → 进攻


def fetch_cn_indexes() -> list[IndexQuote]:
    """腾讯简版行情获取A股指数（不封IP）。"""
    codes = ",".join(c for c, _ in CN_INDEXES)
    try:
        resp = httpx.get(
            TENCENT_INDEX_URL + codes,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=TENCENT_TIMEOUT,
        )
        resp.raise_for_status()
        text = resp.content.decode("gbk", errors="replace")
    except Exception as e:
        logger.warning(f"CN index fetch failed: {e}")
        return []

    results = []
    for line in text.strip().split(";"):
        line = line.strip()
        if "=" not in line:
            continue
        parsed = parse_tencent_index_line(line)
        if parsed:
            results.append(parsed)
    return results


def parse_tencent_index_line(line: str) -> IndexQuote | None:
    """解析腾讯 s_ 简版行情行: v_s_sh000001="1~上证指数~000001~3764.15~-118.61~-3.05~..."。"""
    try:
        var_name, payload = line.split("=", 1)
        symbol = var_name.strip().replace("v_s_", "").replace("v_", "")
        fields = payload.strip().strip('";').split("~")
        if len(fields) < 6:
            return None
        return IndexQuote(
            symbol=symbol,
            name=fields[1],
            price=float(fields[3]),
            change_pct=float(fields[5]),
        )
    except (ValueError, IndexError):
        return None


def fetch_global_indexes() -> list[IndexQuote]:
    """Yahoo chart 获取隔夜美股指数 + 恒指（最近两日收盘算涨跌幅）。"""
    results = []
    for symbol, name in GLOBAL_INDEXES:
        try:
            url = f"{YAHOO_CHART_URL}{urlquote(symbol)}?interval=1d&range=5d"
            resp = httpx.get(url, headers={"User-Agent": YAHOO_UA}, timeout=YAHOO_TIMEOUT)
            resp.raise_for_status()
            result = resp.json()["chart"]["result"][0]
            closes = [
                c for c in result["indicators"]["quote"][0]["close"] if c is not None
            ]
            if len(closes) < 2:
                continue
            change = (closes[-1] / closes[-2] - 1) * 100
            results.append(
                IndexQuote(symbol=symbol, name=name, price=round(closes[-1], 2), change_pct=round(change, 2))
            )
        except Exception as e:
            logger.warning(f"Global index {symbol} fetch failed: {e}")
    return results


def fetch_breadth() -> MarketBreadth | None:
    """乐咕乐股全市场涨跌/涨跌停家数（AKShare单次调用）。"""
    try:
        import akshare as ak

        df = ak.stock_market_activity_legu()
        kv = dict(zip(df["item"], df["value"]))
        return MarketBreadth(
            up_count=int(float(kv.get("上涨", 0))),
            down_count=int(float(kv.get("下跌", 0))),
            flat_count=int(float(kv.get("平盘", 0))),
            limit_up=int(float(kv.get("涨停", 0))),
            limit_down=int(float(kv.get("跌停", 0))),
        )
    except Exception as e:
        logger.warning(f"Market breadth fetch failed: {e}")
        return None


def fetch_margin() -> MarginSnapshot | None:
    """沪市两融余额及日变化（亿元，T+1披露）。"""
    try:
        import akshare as ak

        start = (date.today() - timedelta(days=10)).strftime("%Y%m%d")
        df = ak.stock_margin_sse(start_date=start, end_date=date.today().strftime("%Y%m%d"))
        if df is None or len(df) < 2:
            return None
        df = df.sort_values("信用交易日期")
        latest, prev = df.iloc[-1], df.iloc[-2]
        bal = float(latest["融资融券余额"]) / 1e8
        prev_bal = float(prev["融资融券余额"]) / 1e8
        return MarginSnapshot(
            trade_date=str(latest["信用交易日期"]),
            balance_yi=round(bal, 1),
            change_yi=round(bal - prev_bal, 1),
        )
    except Exception as e:
        logger.warning(f"Margin fetch failed: {e}")
        return None


def compute_sector_stats(
    quotes: dict[str, list[DailyQuote]],
    watchlist: list[StockConfig],
) -> dict[str, float]:
    """watchlist 各板块当日中位涨跌幅（仅A股+港股参与regime判断）。"""
    sector_changes: dict[str, list[float]] = {}
    market_map = {s.symbol: s.market for s in watchlist}
    sector_map = {s.symbol: s.sector for s in watchlist}

    for symbol, dq in quotes.items():
        if not dq or market_map.get(symbol) == "美股":
            continue
        sector = sector_map.get(symbol, "")
        if not sector:
            continue
        sector_changes.setdefault(sector, []).append(dq[-1].change_pct)

    return {
        sector: round(statistics.median(changes), 2)
        for sector, changes in sector_changes.items()
        if len(changes) >= 2
    }


def classify_regime(
    cn_indexes: list[IndexQuote],
    breadth: MarketBreadth | None,
    sector_stats: dict[str, float],
) -> tuple[str, list[str]]:
    """市场状态机: 杀跌 < 熄火 < 震荡 < 换挡 < 进攻。

    规则源自多空逻辑框架:
    - 杀跌: 全板块无差别下跌, 资金离场蒸发
    - 熄火: 主线(成长)跌、仅防御板块有承接
    - 换挡: 主线跌但另一进攻板块放量接力
    - 进攻: 宽度健康、成长领涨
    """
    reasons: list[str] = []
    idx = {q.name: q.change_pct for q in cn_indexes}
    growth_changes = [v for k, v in idx.items() if k in GROWTH_INDEX_NAMES]
    growth = min(growth_changes) if growth_changes else None
    main = idx.get("上证指数")

    attack_sectors = {
        k: v for k, v in sector_stats.items() if k not in DEFENSIVE_SECTORS
    }
    defensive_sectors = {
        k: v for k, v in sector_stats.items() if k in DEFENSIVE_SECTORS
    }

    # --- 杀跌 ---
    if growth is not None and growth <= KILL_GROWTH_DROP:
        reasons.append(f"成长指数重挫 {growth:+.1f}%")
    if main is not None and main <= KILL_MAIN_DROP:
        reasons.append(f"上证 {main:+.1f}%")
    if breadth and breadth.limit_down >= KILL_LIMIT_DOWN:
        reasons.append(f"跌停 {breadth.limit_down} 家")
    if breadth and breadth.down_ratio >= KILL_DOWN_RATIO and main is not None and main <= -1.5:
        reasons.append(f"下跌占比 {breadth.down_ratio:.0%}")
    if reasons:
        return "杀跌", reasons

    # --- 成长走弱分支: 换挡 or 熄火 ---
    if growth is not None and growth <= WEAK_GROWTH_DROP:
        hot = {k: v for k, v in attack_sectors.items() if v >= SHIFT_SECTOR_GAIN}
        if hot:
            top = max(hot, key=lambda k: hot[k])
            return "换挡", [
                f"成长指数 {growth:+.1f}% 走弱",
                f"进攻板块接力: {top} 中位 {hot[top]:+.1f}%",
            ]
        defensive_hold = any(v >= 0 for v in defensive_sectors.values())
        reasons = [f"成长指数 {growth:+.1f}% 走弱, 无进攻板块接力"]
        if defensive_hold:
            reasons.append("仅防御板块有承接")
        return "熄火", reasons

    # --- 进攻 ---
    if growth is not None and growth >= ATTACK_GROWTH_GAIN:
        if breadth is None or breadth.down_ratio <= ATTACK_DOWN_RATIO:
            reasons = [f"成长指数 {growth:+.1f}% 领涨"]
            if breadth:
                reasons.append(f"上涨 {breadth.up_count} 家 vs 下跌 {breadth.down_count} 家")
            return "进攻", reasons

    return "震荡", ["无明确方向信号"]


def _load_regime_history(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def update_regime_history(
    output_dir: str, today: date, regime: str, reasons: list[str]
) -> list[str]:
    """持久化 regime 并返回近N日轨迹（含今日）, 格式 'MM-DD:regime'。"""
    path = Path(output_dir) / REGIME_HISTORY_FILE
    history = _load_regime_history(path)
    history[today.isoformat()] = {"regime": regime, "reasons": reasons}

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError as e:
        logger.warning(f"Regime history save failed: {e}")

    recent = sorted(history.keys())[-REGIME_HISTORY_DAYS:]
    return [f"{d[5:]}:{history[d]['regime']}" for d in recent]


def build_thermometer(
    quotes: dict[str, list[DailyQuote]],
    watchlist: list[StockConfig],
    output_dir: str,
) -> MarketThermometer:
    """组装市场体温计（每路数据独立容错）。"""
    cn_indexes = fetch_cn_indexes()
    global_indexes = fetch_global_indexes()
    breadth = fetch_breadth()
    margin = fetch_margin()

    sector_stats = compute_sector_stats(quotes, watchlist)
    regime, reasons = classify_regime(cn_indexes, breadth, sector_stats)
    history = update_regime_history(output_dir, date.today(), regime, reasons)

    return MarketThermometer(
        cn_indexes=tuple(cn_indexes),
        global_indexes=tuple(global_indexes),
        breadth=breadth,
        margin=margin,
        regime=regime,
        regime_reasons=tuple(reasons),
        regime_history=tuple(history),
    )
