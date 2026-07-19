"""未来风险日历: 美股财报(Finnhub) + A股解禁 + 新股申购(抽水)。

事前而非事后——NFLX 财报暴雷、解禁抛压、IPO抽水都应提前出现在报告里。
所有获取独立容错, 失败降级为警告。
"""

import logging
import os
from datetime import date, timedelta

import httpx

from src.models import CalendarEvent, StockConfig

logger = logging.getLogger(__name__)

FINNHUB_EARNINGS_URL = "https://finnhub.io/api/v1/calendar/earnings"
FINNHUB_TIMEOUT = 15.0
EARNINGS_DAYS_AHEAD = 10
RESTRICTED_DAYS_AHEAD = 14
IPO_DAYS_AHEAD = 7


def fetch_event_calendar(
    watchlist: list[StockConfig],
) -> tuple[list[CalendarEvent], list[str]]:
    """汇总未来风险事件, 返回 (事件列表, 数据警告列表)。"""
    events: list[CalendarEvent] = []
    warnings: list[str] = []

    us_symbols = {s.symbol: s.name for s in watchlist if s.market == "美股"}
    a_symbols = {s.symbol: s.name for s in watchlist if s.market == "A股"}

    earnings, w = _fetch_us_earnings(us_symbols)
    events.extend(earnings)
    warnings.extend(w)

    restricted, w = _fetch_a_share_restricted(a_symbols)
    events.extend(restricted)
    warnings.extend(w)

    ipos, w = _fetch_upcoming_ipos()
    events.extend(ipos)
    warnings.extend(w)

    events.sort(key=lambda e: e.event_date)
    return events, warnings


def _fetch_us_earnings(
    us_symbols: dict[str, str],
) -> tuple[list[CalendarEvent], list[str]]:
    """Finnhub 财报日历, 过滤 watchlist 美股。"""
    if not us_symbols:
        return [], []
    api_key = os.environ.get("FINNHUB_API_KEY", "")
    if not api_key:
        return [], ["财报日历: 缺少 FINNHUB_API_KEY, 跳过"]

    today = date.today()
    try:
        resp = httpx.get(
            FINNHUB_EARNINGS_URL,
            params={
                "from": today.isoformat(),
                "to": (today + timedelta(days=EARNINGS_DAYS_AHEAD)).isoformat(),
                "token": api_key,
            },
            timeout=FINNHUB_TIMEOUT,
        )
        resp.raise_for_status()
        items = resp.json().get("earningsCalendar", [])
    except Exception as e:
        logger.warning(f"Earnings calendar fetch failed: {e}")
        return [], [f"财报日历获取失败: {str(e)[:120]}"]

    events = []
    for item in items:
        symbol = item.get("symbol", "")
        if symbol not in us_symbols:
            continue
        hour = item.get("hour", "")
        hour_text = {"bmo": "盘前", "amc": "盘后", "dmh": "盘中"}.get(hour, hour)
        eps = item.get("epsEstimate")
        detail = f"财报({hour_text})" if hour_text else "财报"
        if eps is not None:
            detail += f", EPS预期 {eps}"
        events.append(
            CalendarEvent(
                event_date=item.get("date", ""),
                category="财报",
                symbol=symbol,
                name=us_symbols[symbol],
                detail=detail,
            )
        )
    return events, []


def _fetch_a_share_restricted(
    a_symbols: dict[str, str],
) -> tuple[list[CalendarEvent], list[str]]:
    """A股解禁日历(东财批量), 过滤 watchlist。"""
    if not a_symbols:
        return [], []
    try:
        import akshare as ak

        today = date.today()
        df = ak.stock_restricted_release_detail_em(
            start_date=today.strftime("%Y%m%d"),
            end_date=(today + timedelta(days=RESTRICTED_DAYS_AHEAD)).strftime("%Y%m%d"),
        )
    except Exception as e:
        logger.warning(f"Restricted release fetch failed: {e}")
        return [], [f"解禁日历获取失败: {str(e)[:120]}"]

    events = []
    try:
        for _, row in df.iterrows():
            code = str(row.get("股票代码", "")).zfill(6)
            if code not in a_symbols:
                continue
            release_date = str(row.get("解禁时间", ""))[:10]
            ratio = row.get("占流通市值比例", "")
            detail = "限售解禁"
            if ratio not in ("", None):
                try:
                    detail += f", 占流通市值 {float(ratio):.1f}%"
                except (TypeError, ValueError):
                    pass
            events.append(
                CalendarEvent(
                    event_date=release_date,
                    category="解禁",
                    symbol=code,
                    name=a_symbols[code],
                    detail=detail,
                )
            )
    except Exception as e:
        logger.warning(f"Restricted release parse failed: {e}")
        return [], [f"解禁日历解析失败: {str(e)[:120]}"]
    return events, []


def _fetch_upcoming_ipos() -> tuple[list[CalendarEvent], list[str]]:
    """未来新股申购（IPO抽水信号, 市场级非个股）。"""
    try:
        import akshare as ak

        df = ak.stock_xgsglb_em(symbol="全部股票")
    except Exception as e:
        logger.warning(f"IPO calendar fetch failed: {e}")
        return [], [f"新股日历获取失败: {str(e)[:120]}"]

    today = date.today()
    horizon = today + timedelta(days=IPO_DAYS_AHEAD)
    events = []
    try:
        for _, row in df.iterrows():
            raw = row.get("申购日期")
            if raw in ("", None):
                continue
            try:
                d = date.fromisoformat(str(raw)[:10])
            except ValueError:
                continue
            if not (today <= d <= horizon):
                continue
            amount = row.get("募集资金", "")
            detail = "新股申购"
            if amount not in ("", None):
                try:
                    detail += f", 拟募 {float(amount):.1f}亿"
                except (TypeError, ValueError):
                    pass
            events.append(
                CalendarEvent(
                    event_date=d.isoformat(),
                    category="新股",
                    symbol=str(row.get("股票代码", "")),
                    name=str(row.get("股票简称", "")),
                    detail=detail,
                )
            )
    except Exception as e:
        logger.warning(f"IPO calendar parse failed: {e}")
        return [], [f"新股日历解析失败: {str(e)[:120]}"]
    return events, []
