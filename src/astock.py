"""A股增强数据模块。

集成 a-stock-data 工具包 (https://github.com/simonlin1212/a-stock-data) 的两项能力:
- 基本面: 腾讯财经 API (PE/PB/市值/换手率/量比/涨跌停, HTTP GBK, 不封IP, 无需key)
- 价格兜底: mootdx 通达信 TCP 日K线 (AKShare 限流失败时使用)

用法:
    basics = fetch_a_share_basics(["600519", "000001"])
    quotes = fetch_a_share_kline_mootdx("600519", bars=40)
"""

import logging
from datetime import date

import httpx
import pandas as pd

from src.models import AShareBasicInfo, DailyQuote

logger = logging.getLogger(__name__)

# 腾讯财经实时行情 (GBK, ~分隔字段)
TENCENT_QUOTE_URL = "https://qt.gtimg.cn/q="
TENCENT_REQUEST_TIMEOUT = 10.0
TENCENT_USER_AGENT = "Mozilla/5.0"

# 腾讯字段索引 (实测校准, 见 a-stock-data SKILL)；注意 43=振幅 不是PB, PB在46
TENCENT_MIN_FIELDS = 53


def _a_share_prefix(code: str) -> str:
    """6位A股代码 -> 腾讯/通达信市场前缀。"""
    if code.startswith(("6", "9")):
        return "sh"
    elif code.startswith("8"):
        return "bj"
    return "sz"


def fetch_a_share_basics(symbols: list[str]) -> dict[str, AShareBasicInfo]:
    """批量获取A股基本面快照 (腾讯财经)。

    Args:
        symbols: A股6位代码列表

    Returns:
        {symbol: AShareBasicInfo}，获取失败的代码不在结果中
    """
    if not symbols:
        return {}

    prefixed = [f"{_a_share_prefix(c)}{c}" for c in symbols]
    url = TENCENT_QUOTE_URL + ",".join(prefixed)

    try:
        resp = httpx.get(
            url,
            headers={"User-Agent": TENCENT_USER_AGENT},
            timeout=TENCENT_REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        # 腾讯返回 GBK 编码
        text = resp.content.decode("gbk", errors="ignore")
    except Exception as e:
        logger.warning(f"腾讯财经基本面请求失败: {e}")
        return {}

    result: dict[str, AShareBasicInfo] = {}
    for line in text.strip().split(";"):
        info = _parse_tencent_line(line)
        if info:
            result[info.symbol] = info
    return result


def _parse_tencent_line(line: str) -> AShareBasicInfo | None:
    """解析单行腾讯行情 (v_sh600519="1~贵州茅台~...")。"""
    line = line.strip()
    if "=" not in line or '"' not in line:
        return None

    try:
        key = line.split("=")[0].split("_")[-1]  # 如 sh600519
        vals = line.split('"')[1].split("~")
    except (IndexError, ValueError):
        return None

    if len(vals) < TENCENT_MIN_FIELDS:
        return None

    code = key[2:] if len(key) > 2 else key

    def f(idx: int) -> float:
        try:
            return float(vals[idx]) if vals[idx] else 0.0
        except (ValueError, IndexError):
            return 0.0

    return AShareBasicInfo(
        symbol=code,
        name=vals[1],
        price=f(3),
        change_pct=f(32),
        pe_ttm=f(39),
        pe_static=f(52),
        pb=f(46),
        mcap_yi=f(44),
        float_mcap_yi=f(45),
        turnover_pct=f(38),
        vol_ratio=f(49),
        limit_up=f(47),
        limit_down=f(48),
    )


# ── mootdx 价格兜底 ───────────────────────────────────────────
def fetch_a_share_kline_mootdx(symbol: str, bars: int = 40) -> list[DailyQuote]:
    """通过 mootdx (通达信TCP) 获取A股日线，作为 AKShare 的兜底。

    Args:
        symbol: A股6位代码
        bars: 获取K线根数

    Returns:
        DailyQuote列表 (按日期升序)，失败返回空列表
    """
    try:
        from mootdx.quotes import Quotes
    except ImportError:
        logger.error("mootdx 未安装，无法兜底A股行情。安装: pip install mootdx")
        return []

    try:
        client = Quotes.factory(market="std")
        df = client.bars(symbol=symbol, category=4, offset=bars)
    except Exception as e:
        logger.warning(f"mootdx 获取 {symbol} 行情失败: {e}")
        return []

    if df is None or df.empty:
        logger.warning(f"mootdx 返回空数据: {symbol}")
        return []

    return _normalize_mootdx_df(df, symbol)


def _normalize_mootdx_df(df: pd.DataFrame, symbol: str) -> list[DailyQuote]:
    """将 mootdx 日K线 DataFrame 转换为 DailyQuote 列表。

    mootdx bars 返回列: open, high, low, close, vol, amount, 日期在 datetime 列或索引。
    涨跌幅按前一交易日收盘价计算 (mootdx 不直接提供)。
    """
    rows = []
    for idx, row in df.iterrows():
        try:
            quote_date = _mootdx_row_date(row, idx)
            if quote_date is None:
                continue
            rows.append({
                "date": quote_date,
                "open": float(row.get("open", 0)),
                "close": float(row.get("close", 0)),
                "high": float(row.get("high", 0)),
                "low": float(row.get("low", 0)),
                "volume": int(float(row.get("vol", 0))),
                "turnover": float(row.get("amount", 0)),
            })
        except (ValueError, TypeError, KeyError) as e:
            logger.warning(f"解析 mootdx 行失败 {symbol}: {e}")

    rows.sort(key=lambda r: r["date"])

    quotes = []
    prev_close: float | None = None
    for r in rows:
        if r["close"] == 0:
            continue
        if prev_close and prev_close > 0:
            change_pct = (r["close"] - prev_close) / prev_close * 100
        else:
            change_pct = 0.0
        quotes.append(DailyQuote(
            symbol=symbol,
            date=r["date"],
            open=r["open"],
            close=r["close"],
            high=r["high"],
            low=r["low"],
            volume=r["volume"],
            turnover=r["turnover"],
            change_pct=round(change_pct, 4),
        ))
        prev_close = r["close"]

    return quotes


def _mootdx_row_date(row: pd.Series, idx) -> date | None:
    """从 mootdx 行中提取日期 (优先 datetime 列，回退索引)。"""
    raw = row.get("datetime") if "datetime" in row else idx
    if raw is None:
        return None
    try:
        return pd.Timestamp(raw).date()
    except (ValueError, TypeError):
        return None
