"""美股/港股增强数据模块。

集成 global-stock-data 工具包 (https://github.com/simonlin1212/global-stock-data) 的两项能力:
- 历史K线: Yahoo Finance chart v8 (零crumb), 美股+港股完整 OHLCV 含真实成交量
- 基本面: Yahoo quoteSummary (PE/PB/PEG/市值/ROE/利润率/目标价/评级, 自动 cookie+crumb)

用法:
    quotes = fetch_kline_yahoo("AAPL", market="美股", range_="3mo")
    basics = fetch_global_basics([("AAPL", "苹果", "美股"), ("00700", "腾讯", "港股")])
"""

import logging
from datetime import date, datetime

import httpx

from src.config import MARKET_HK
from src.models import DailyQuote, GlobalStockBasicInfo

logger = logging.getLogger(__name__)

YAHOO_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
YAHOO_CHART_URL = "https://query2.finance.yahoo.com/v8/finance/chart/"
YAHOO_QUOTESUMMARY_URL = "https://query2.finance.yahoo.com/v10/finance/quoteSummary/"
YAHOO_TIMEOUT = 15.0

# 带 crumb 的 Yahoo 会话 (模块级缓存)
_yahoo_client: httpx.Client | None = None
_yahoo_crumb: str = ""


def to_yahoo_symbol(symbol: str, market: str) -> str:
    """股票代码 -> Yahoo Finance 格式。

    美股: 直接 ticker (点号转横线, 如 BRK.B -> BRK-B)
    港股: 4位数字 + .HK (如 00700 -> 0700.HK)
    """
    if market == MARKET_HK:
        digits = symbol.lstrip("0") or "0"
        return f"{digits.zfill(4)}.HK"
    return symbol.upper().replace(".", "-")


def fetch_kline_yahoo(
    symbol: str, market: str, range_: str = "3mo"
) -> list[DailyQuote]:
    """通过 Yahoo chart v8 获取日K线 (美股+港股, 含真实成交量)。

    Args:
        symbol: 原始股票代码
        market: 市场 (美股/港股)
        range_: 时间范围 (1mo/3mo/6mo/1y/...)

    Returns:
        DailyQuote列表 (按日期升序)，涨跌幅按前收盘价计算
    """
    yahoo_symbol = to_yahoo_symbol(symbol, market)
    try:
        resp = httpx.get(
            YAHOO_CHART_URL + yahoo_symbol,
            params={"interval": "1d", "range": range_},
            headers={"User-Agent": YAHOO_UA},
            timeout=YAHOO_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning(f"Yahoo K线获取失败 {symbol} ({yahoo_symbol}): {e}")
        return []

    return _parse_yahoo_chart(data, symbol)


def _parse_yahoo_chart(data: dict, symbol: str) -> list[DailyQuote]:
    """解析 Yahoo chart 响应为 DailyQuote 列表。"""
    try:
        result = data["chart"]["result"][0]
        timestamps = result.get("timestamp") or []
        quote = result["indicators"]["quote"][0]
    except (KeyError, IndexError, TypeError):
        return []

    opens = quote.get("open") or []
    highs = quote.get("high") or []
    lows = quote.get("low") or []
    closes = quote.get("close") or []
    volumes = quote.get("volume") or []

    rows = []
    for i, ts in enumerate(timestamps):
        close = _at(closes, i)
        if close is None or close == 0:
            continue
        rows.append({
            "date": datetime.fromtimestamp(ts).date(),
            "open": _at(opens, i) or 0.0,
            "high": _at(highs, i) or 0.0,
            "low": _at(lows, i) or 0.0,
            "close": close,
            "volume": int(_at(volumes, i) or 0),
        })

    rows.sort(key=lambda r: r["date"])

    quotes = []
    prev_close: float | None = None
    for r in rows:
        if prev_close and prev_close > 0:
            change_pct = (r["close"] - prev_close) / prev_close * 100
        else:
            change_pct = 0.0
        quotes.append(DailyQuote(
            symbol=symbol,
            date=r["date"],
            open=round(r["open"], 4),
            close=round(r["close"], 4),
            high=round(r["high"], 4),
            low=round(r["low"], 4),
            volume=r["volume"],
            turnover=0.0,
            change_pct=round(change_pct, 4),
        ))
        prev_close = r["close"]

    return quotes


def _at(arr: list, i: int):
    """安全取数组元素 (越界/None 返回 None)。"""
    if i < len(arr):
        return arr[i]
    return None


# ── Yahoo 基本面 (quoteSummary, 需 cookie + crumb) ──────────────
def _get_yahoo_crumb_client() -> tuple[httpx.Client, str] | tuple[None, str]:
    """获取带 cookie + crumb 的 Yahoo 会话 (模块级缓存)。"""
    global _yahoo_client, _yahoo_crumb
    if _yahoo_client is not None and _yahoo_crumb:
        return _yahoo_client, _yahoo_crumb

    try:
        client = httpx.Client(
            headers={"User-Agent": YAHOO_UA},
            timeout=YAHOO_TIMEOUT,
            follow_redirects=True,
        )
        # Step 1: 取 cookie (该端点可能返回404，但会种下 cookie)
        try:
            client.get("https://fc.yahoo.com")
        except Exception:
            pass
        # Step 2: 取 crumb
        resp = client.get("https://query2.finance.yahoo.com/v1/test/getcrumb")
        resp.raise_for_status()
        crumb = resp.text.strip()
        if not crumb:
            client.close()
            return None, ""
        _yahoo_client, _yahoo_crumb = client, crumb
        return client, crumb
    except Exception as e:
        logger.warning(f"Yahoo crumb 获取失败: {e}")
        return None, ""


def fetch_global_basics(
    items: list[tuple[str, str, str]],
) -> dict[str, GlobalStockBasicInfo]:
    """批量获取美股/港股基本面 (Yahoo quoteSummary)。

    Args:
        items: [(symbol, name, market), ...]

    Returns:
        {symbol: GlobalStockBasicInfo}，失败的代码不在结果中
    """
    if not items:
        return {}

    client, crumb = _get_yahoo_crumb_client()
    if client is None:
        logger.warning("Yahoo 基本面不可用 (crumb 获取失败)")
        return {}

    result: dict[str, GlobalStockBasicInfo] = {}
    for symbol, name, market in items:
        info = _fetch_one_basic(client, crumb, symbol, name, market)
        if info:
            result[symbol] = info
    return result


_BASIC_MODULES = ["financialData", "defaultKeyStatistics", "summaryDetail"]


def _fetch_one_basic(
    client: httpx.Client, crumb: str, symbol: str, name: str, market: str
) -> GlobalStockBasicInfo | None:
    """获取单只美股/港股基本面。"""
    yahoo_symbol = to_yahoo_symbol(symbol, market)
    try:
        resp = client.get(
            YAHOO_QUOTESUMMARY_URL + yahoo_symbol,
            params={"modules": ",".join(_BASIC_MODULES), "crumb": crumb},
        )
        resp.raise_for_status()
        results = resp.json().get("quoteSummary", {}).get("result") or [{}]
        data = results[0] if results else {}
    except Exception as e:
        logger.warning(f"Yahoo 基本面获取失败 {symbol} ({yahoo_symbol}): {e}")
        return None

    fd = data.get("financialData", {})
    ks = data.get("defaultKeyStatistics", {})
    sd = data.get("summaryDetail", {})

    def v(d: dict, key: str) -> float:
        raw = d.get(key, {})
        if isinstance(raw, dict):
            raw = raw.get("raw")
        try:
            return float(raw) if raw is not None else 0.0
        except (ValueError, TypeError):
            return 0.0

    price = v(fd, "currentPrice")
    if price == 0:
        return None

    return GlobalStockBasicInfo(
        symbol=symbol,
        name=name,
        market=market,
        price=price,
        pe_ttm=v(sd, "trailingPE"),
        forward_pe=v(ks, "forwardPE"),
        pb=v(ks, "priceToBook"),
        peg=v(ks, "pegRatio"),
        market_cap=v(sd, "marketCap"),
        roe=v(fd, "returnOnEquity"),
        profit_margin=v(ks, "profitMargins"),
        target_mean=v(fd, "targetMeanPrice"),
        recommendation=str(fd.get("recommendationKey", "") or ""),
    )
