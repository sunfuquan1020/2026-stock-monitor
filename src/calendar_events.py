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
    """财报日历: Nasdaq 官方(零鉴权, 主) -> Finnhub(需 key, 备)。"""
    if not us_symbols:
        return [], []

    events, err = _fetch_us_earnings_nasdaq(us_symbols)
    if err is None:
        return events, []

    logger.warning(f"Nasdaq 财报日历失败, 降级 Finnhub: {err}")
    events, w = _fetch_us_earnings_finnhub(us_symbols)
    if w:
        w = [f"财报日历: Nasdaq 失败({err[:60]}); " + w[0]]
    else:
        w = ["财报日历: Nasdaq 失败, 已降级 Finnhub"]
    return events, w


def _fetch_us_earnings_nasdaq(
    us_symbols: dict[str, str],
) -> tuple[list[CalendarEvent], str | None]:
    """Nasdaq 财报日历 —— 零鉴权, 逐日查询未来 EARNINGS_DAYS_AHEAD 天。

    Returns:
        (events, None) 成功 / ([], 错误摘要) 失败
    """
    from src.fallback_sources import nasdaq_earnings

    today = date.today()
    events: list[CalendarEvent] = []
    ok_days = 0
    last_err = ""
    for offset in range(EARNINGS_DAYS_AHEAD + 1):
        day = today + timedelta(days=offset)
        if day.weekday() >= 5:  # 周末无财报
            continue
        try:
            rows = nasdaq_earnings(day)
            ok_days += 1
        except Exception as e:
            last_err = str(e)[:120]
            continue
        for r in rows:
            symbol = r["symbol"]
            if symbol not in us_symbols:
                continue
            detail = f"财报({r['when']})" if r["when"] else "财报"
            if r["eps_forecast"]:
                detail += f", EPS预期 {r['eps_forecast']}"
            events.append(
                CalendarEvent(
                    event_date=day.isoformat(),
                    category="财报",
                    symbol=symbol,
                    name=us_symbols[symbol],
                    detail=detail,
                )
            )
    if ok_days == 0:
        return [], last_err or "Nasdaq 全部交易日查询失败"
    return events, None


def _fetch_us_earnings_finnhub(
    us_symbols: dict[str, str],
) -> tuple[list[CalendarEvent], list[str]]:
    """Finnhub 财报日历（备用源, 需 FINNHUB_API_KEY）。"""
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
    """A股解禁日历, 过滤 watchlist。

    源顺序: 东财 datacenter(urllib 传输, 主) -> akshare(requests) -> 新浪逐标的。
    urllib 通道在本地代理下实测 3/3, 而 akshare 的 requests 通道 100% 失败,
    故把 urllib 提到主位。见 src/http_urllib 模块说明。
    """
    if not a_symbols:
        return [], []
    from src.net_retry import retry_call

    today = date.today()
    horizon = today + timedelta(days=RESTRICTED_DAYS_AHEAD)

    # 主源: 东财 datacenter, urllib 传输
    try:
        from src.fallback_sources import restricted_release

        rows = retry_call(
            lambda: restricted_release(today, horizon),
            attempts=4,
            base_delay=2.0,
            label="解禁日历(东财/urllib)",
        )
        events = []
        for r in rows:
            if r["code"] not in a_symbols:
                continue
            detail = "限售解禁"
            if r["lift_market_cap_yi"] is not None:
                detail += f", 解禁市值 {r['lift_market_cap_yi']:.1f}亿"
            if r["free_ratio_pct"] is not None:
                detail += f", 占流通 {r['free_ratio_pct']:.1f}%"
            events.append(
                CalendarEvent(
                    event_date=r["free_date"],
                    category="解禁",
                    symbol=r["code"],
                    name=a_symbols[r["code"]],
                    detail=detail,
                )
            )
        return events, []
    except Exception as e:
        logger.warning(f"解禁(东财/urllib)失败, 降级 akshare: {str(e)[:120]}")

    try:
        import akshare as ak

        df = retry_call(
            lambda: ak.stock_restricted_release_detail_em(
                start_date=today.strftime("%Y%m%d"),
                end_date=horizon.strftime("%Y%m%d"),
            ),
            attempts=2,
            label="解禁日历(东财/akshare)",
        )
    except Exception as e:
        logger.warning(f"Restricted release fetch failed (东财, 重试后): {e}")
        # 东财兜底: 新浪按标的查询解禁队列 (非东财)
        return _fetch_a_share_restricted_sina(a_symbols)

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


def _fetch_a_share_restricted_sina(
    a_symbols: dict[str, str],
) -> tuple[list[CalendarEvent], list[str]]:
    """解禁东财失败兜底: 新浪按标的查询解禁队列 (非东财, 每标的一次)。

    新浪接口按 symbol 查询, 只遍历 watchlist A股, 过滤未来 RESTRICTED_DAYS_AHEAD 内。
    """
    import time

    from src.net_retry import retry_call

    try:
        import akshare as ak
    except Exception as e:
        return [], [f"解禁日历获取失败(东财+新浪均失败): {str(e)[:120]}"]

    today = date.today()
    horizon = today + timedelta(days=RESTRICTED_DAYS_AHEAD)
    events: list[CalendarEvent] = []
    for code, name in a_symbols.items():
        try:
            df = retry_call(
                lambda c=code: ak.stock_restricted_release_queue_sina(symbol=c),
                attempts=2,
                label=f"解禁(新浪 {code})",
            )
        except Exception:
            continue
        if df is None or df.empty:
            continue
        for _, row in df.iterrows():
            raw = str(row.get("解禁日期", ""))[:10]
            try:
                d = date.fromisoformat(raw)
            except ValueError:
                continue
            if not (today <= d <= horizon):
                continue
            mcap = row.get("解禁股流通市值", "")
            detail = "限售解禁"
            if mcap not in ("", None):
                try:
                    detail += f", 解禁市值 {float(mcap):.1f}亿"
                except (TypeError, ValueError):
                    pass
            events.append(
                CalendarEvent(
                    event_date=raw,
                    category="解禁",
                    symbol=code,
                    name=name,
                    detail=detail,
                )
            )
        time.sleep(0.2)  # 新浪限速
    warn = ["解禁: 东财失败, 已降级新浪源(逐标的查询)"]
    return events, warn


def _fetch_upcoming_ipos() -> tuple[list[CalendarEvent], list[str]]:
    """未来新股申购（IPO抽水信号, 市场级非个股）。

    源顺序: 东财 datacenter(urllib, 主) -> akshare(requests, 备)。
    """
    from src.net_retry import retry_call

    today = date.today()
    horizon = today + timedelta(days=IPO_DAYS_AHEAD)

    try:
        from src.fallback_sources import ipo_calendar

        rows = retry_call(
            lambda: ipo_calendar(),
            attempts=4,
            base_delay=2.0,
            label="新股日历(东财/urllib)",
        )
        events = []
        for r in rows:
            try:
                d = date.fromisoformat(r["apply_date"])
            except ValueError:
                continue
            if not (today <= d <= horizon):
                continue
            detail = "新股申购"
            if r.get("issue_price"):
                detail += f", 发行价 {r['issue_price']}"
            if r.get("after_issue_pe"):
                detail += f", 发行后PE {r['after_issue_pe']}"
            events.append(
                CalendarEvent(
                    event_date=d.isoformat(),
                    category="新股",
                    symbol=r["code"],
                    name=r["name"],
                    detail=detail,
                )
            )
        return events, []
    except Exception as e:
        logger.warning(f"新股(东财/urllib)失败, 降级 akshare: {str(e)[:120]}")

    try:
        import akshare as ak

        df = retry_call(
            lambda: ak.stock_xgsglb_em(symbol="全部股票"),
            attempts=2,
            label="新股日历(东财/akshare)",
        )
    except Exception as e:
        logger.warning(f"IPO calendar fetch failed (重试后): {e}")
        return [], [f"新股日历获取失败(重试后): {str(e)[:120]}"]

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
