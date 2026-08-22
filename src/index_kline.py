"""指数量价结构: 成交量 + 均线位置 + 小盘相对强度。

数据源为 mootdx 通达信 TCP —— 不走代理、不封IP, 在东财 push2his 不可用时仍然稳定
(2026-08-12 实测: 东财 20:50 后全灭而 mootdx 全部成功)。

价格与涨跌幅仍由 market.fetch_cn_indexes() 的腾讯简版行情提供; 本模块只补充
腾讯简版行情拿不到的东西——**成交量、均线位置、相对强度**。两者按指数名称合并。

任何一路失败只降级不中断: 拿不到K线就只剩点位表, 不影响 pipeline。
"""

import logging

from src.models import IndexTape, StyleStrength

logger = logging.getLogger(__name__)

# 与 market.CN_INDEXES 对应的通达信指数代码 (mootdx 自动识别沪深市场)
INDEX_KLINE_CODES = [
    ("000001", "上证指数"),
    ("399001", "深证成指"),
    ("399006", "创业板指"),
    ("000688", "科创50"),
    ("000300", "沪深300"),
    ("000905", "中证500"),
    ("000852", "中证1000"),
]

# 相对强度分子/分母: 小盘 / 大盘
STYLE_SMALL_CODE = "000852"
STYLE_BIG_CODE = "000300"

KLINE_FREQUENCY_DAILY = 9  # mootdx frequency 枚举: 9 = 日线
KLINE_OFFSET = 70          # 取70根足够算 MA60 + 60日高

MA_SHORT = 20
MA_LONG = 60
VOL_WINDOW = 20
VOL_TREND_WINDOW = 5

# 量能定性阈值 (5日均量 / 20日均量)
VOL_TREND_EXPAND = 1.15
VOL_TREND_SHRINK = 0.85

# 单日量比阈值。指数是成分股加权, 波动天然小于个股 —— 沿用个股的 1.3/0.7
# 会让指数几乎永远落在中间带(2026-08-12 实测六个上涨指数量比全在 0.73~0.91),
# 提示语因此失去信息量。这里按指数口径收窄。
VOL_RATIO_EXPAND = 1.15
VOL_RATIO_SHRINK = 0.85

# 「涨而不放量」提示的触发线
NO_VOLUME_RALLY_GAIN = 0.5


def _fetch_bars(client, code: str):
    """取单个指数日K, 失败返回 None。"""
    try:
        df = client.index(symbol=code, frequency=KLINE_FREQUENCY_DAILY, offset=KLINE_OFFSET)
    except Exception as e:
        logger.warning(f"Index kline {code} fetch failed: {e}")
        return None
    if df is None or len(df) < MA_LONG:
        logger.warning(f"Index kline {code} too short: {0 if df is None else len(df)} bars")
        return None
    return df.reset_index(drop=True)


def _classify_vol_trend(ratio_5d_vs_20d: float) -> str:
    if ratio_5d_vs_20d >= VOL_TREND_EXPAND:
        return "放量"
    if ratio_5d_vs_20d <= VOL_TREND_SHRINK:
        return "缩量"
    return "平量"


def build_index_tape(df, symbol: str, name: str) -> IndexTape | None:
    """从日K算出量价结构。df 需含 close/vol 两列且长度 >= MA_LONG。"""
    try:
        close = df["close"]
        vol = df["vol"]
        last_close = float(close.iloc[-1])

        vol_ma20 = float(vol.tail(VOL_WINDOW).mean())
        vol_ma5 = float(vol.tail(VOL_TREND_WINDOW).mean())
        if vol_ma20 <= 0:
            return None

        ma_short = float(close.tail(MA_SHORT).mean())
        ma_long = float(close.tail(MA_LONG).mean())
        high_60d = float(close.tail(MA_LONG).max())

        return IndexTape(
            symbol=symbol,
            name=name,
            vol_ratio_20d=round(float(vol.iloc[-1]) / vol_ma20, 2),
            vol_trend=_classify_vol_trend(vol_ma5 / vol_ma20),
            vs_ma20_pct=round((last_close / ma_short - 1) * 100, 2),
            vs_ma60_pct=round((last_close / ma_long - 1) * 100, 2),
            dist_60d_high_pct=round((last_close / high_60d - 1) * 100, 2),
        )
    except (KeyError, IndexError, ValueError, ZeroDivisionError) as e:
        logger.warning(f"Index tape {symbol} compute failed: {e}")
        return None


def build_style_strength(small_df, big_df) -> StyleStrength | None:
    """小盘相对强度 = 中证1000 / 沪深300 的比值及其变化。

    比值上行 = 小盘跑赢大盘。按 60/20/5 日三个尺度给, 因为短期修复与
    中期风格反转经常方向相反(2026-08-12: 5日+3.29% 而 60日 -8.47%)。
    """
    try:
        s_close = small_df["close"].reset_index(drop=True)
        b_close = big_df["close"].reset_index(drop=True)
        n = min(len(s_close), len(b_close))
        if n < MA_LONG + 1:
            return None
        s_close, b_close = s_close.tail(n), b_close.tail(n)
        ratio = (s_close.values / b_close.values)

        def change(days: int) -> float:
            # float() 收口 numpy 标量, 避免 np.float64 泄漏进 dataclass 影响序列化
            return round(float(ratio[-1] / ratio[-1 - days] - 1) * 100, 2)

        return StyleStrength(
            ratio=round(float(ratio[-1]), 4),
            chg_5d_pct=change(5),
            chg_20d_pct=change(20),
            chg_60d_pct=change(MA_LONG),
        )
    except (KeyError, IndexError, ValueError, ZeroDivisionError) as e:
        logger.warning(f"Style strength compute failed: {e}")
        return None


def describe_volume_price(
    tapes: list[IndexTape], index_changes: dict[str, float]
) -> str:
    """一句话量价提示。

    只描述现象、不改 regime —— regime 状态机保持不变以维持跨日可比性。
    """
    rallying = [
        t for t in tapes
        if index_changes.get(t.name, 0.0) >= NO_VOLUME_RALLY_GAIN
    ]
    if not rallying:
        return ""

    wet = [t for t in rallying if t.vol_ratio_20d >= VOL_RATIO_EXPAND]
    if wet:
        return "放量上涨: " + ", ".join(
            f"{t.name}(量比{t.vol_ratio_20d:.2f})" for t in wet
        )

    lo = min(t.vol_ratio_20d for t in rallying)
    hi = max(t.vol_ratio_20d for t in rallying)
    span = f"量比{lo:.2f}~{hi:.2f}"
    if hi < VOL_RATIO_SHRINK:
        return f"⚠️ {len(rallying)} 个上涨指数全部缩量({span}) — 无量反弹, 不给多头方向"
    return f"⚠️ {len(rallying)} 个上涨指数无一放量({span}) — 涨势缺量能确认"


def fetch_index_tapes() -> tuple[list[IndexTape], StyleStrength | None]:
    """取全部指数量价结构 + 小盘相对强度。整体失败返回空。"""
    try:
        from mootdx.quotes import Quotes

        client = Quotes.factory(market="std")
    except Exception as e:
        logger.warning(f"mootdx client init failed: {e}")
        return [], None

    tapes: list[IndexTape] = []
    frames: dict[str, object] = {}
    for code, name in INDEX_KLINE_CODES:
        df = _fetch_bars(client, code)
        if df is None:
            continue
        frames[code] = df
        tape = build_index_tape(df, code, name)
        if tape:
            tapes.append(tape)

    style = None
    if STYLE_SMALL_CODE in frames and STYLE_BIG_CODE in frames:
        style = build_style_strength(frames[STYLE_SMALL_CODE], frames[STYLE_BIG_CODE])

    return tapes, style
