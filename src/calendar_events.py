"""未来风险日历: 财报/业绩披露 + 分红送转除权除息 + A股解禁 + 新股申购(抽水)。

事前而非事后——NFLX 财报暴雷、解禁抛压、IPO抽水、除权缺口都应提前出现在报告里。
- A股: 定期报告预约披露日 + 分红送转除权除息日 (东财 datacenter)
- 美股: 财报 Nasdaq(主)/Finnhub(备); 港股财报与美港股除息日 Yahoo calendarEvents
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
REPORT_DAYS_AHEAD = 30          # A股定期报告预约日: 提前一个月可见, 给足预警时间
DIVIDEND_DAYS_AHEAD = 14        # 除权除息日
GLOBAL_EVENTS_DAYS_AHEAD = 30   # Yahoo 港股财报/美港股除息
REPORT_PERIOD_LOOKAHEAD_DAYS = 31  # 报告期结束前约一个月, 交易所开始公布预约日
# 各报告期(月,日) → 法定披露截止(跨年, 月, 日)
_REPORT_DEADLINES = {(3, 31): (0, 4, 30), (6, 30): (0, 8, 31), (9, 30): (0, 10, 31), (12, 31): (1, 4, 30)}


def fetch_event_calendar(
    watchlist: list[StockConfig],
) -> tuple[list[CalendarEvent], list[str]]:
    """汇总未来风险事件, 返回 (事件列表, 数据警告列表)。"""
    events: list[CalendarEvent] = []
    warnings: list[str] = []

    us_symbols = {s.symbol: s.name for s in watchlist if s.market == "美股"}
    a_symbols = {s.symbol: s.name for s in watchlist if s.market == "A股"}
    global_symbols = {
        s.symbol: (s.name, s.market) for s in watchlist if s.market in ("美股", "港股")
    }

    earnings, w = _fetch_us_earnings(us_symbols)
    events.extend(earnings)
    warnings.extend(w)

    covered = {e.symbol for e in earnings if e.category == "财报"}
    global_events, w = _fetch_global_corporate_events(global_symbols, covered)
    events.extend(global_events)
    warnings.extend(w)

    report_dates, w = _fetch_a_share_report_dates(a_symbols)
    events.extend(report_dates)
    warnings.extend(w)

    dividends, w = _fetch_a_share_dividends(a_symbols)
    events.extend(dividends)
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


# ═══════════════════════════════════════════════════════════
# 重大公司事件: 业绩披露 / 分红送转 (A股东财) + 财报 / 除息 (美港股 Yahoo)
# ═══════════════════════════════════════════════════════════


def pending_report_periods(today: date) -> list[date]:
    """当前可能有预约披露日的报告期: 截止日未过, 且报告期末距今不超过约一个月。"""
    periods = []
    for year in (today.year - 1, today.year):
        for (month, day), (dy, dm, dd) in _REPORT_DEADLINES.items():
            period = date(year, month, day)
            deadline = date(year + dy, dm, dd)
            if deadline >= today and period <= today + timedelta(days=REPORT_PERIOD_LOOKAHEAD_DAYS):
                periods.append(period)
    return sorted(periods)


def _in_window(raw: str, today: date, days: int) -> bool:
    try:
        d = date.fromisoformat(raw[:10])
    except (TypeError, ValueError):
        return False
    return today <= d <= today + timedelta(days=days)


def _fetch_a_share_report_dates(
    a_symbols: dict[str, str], today: date | None = None
) -> tuple[list[CalendarEvent], list[str]]:
    """A股定期报告(季报/中报/年报)预约披露日, 东财 RPT_PUBLIC_BS_APPOIN。"""
    if not a_symbols:
        return [], []
    from src import fallback_sources as fb

    today = today or date.today()
    events: list[CalendarEvent] = []
    for period in pending_report_periods(today):
        try:
            rows = fb.report_appointments(list(a_symbols), period)
        except Exception as e:
            logger.warning(f"业绩披露日历失败 {period}: {e}")
            return events, [f"业绩披露日历获取失败({period}): {str(e)[:100]}"]
        for r in rows:
            if r["code"] not in a_symbols or not _in_window(r["appoint_date"], today, REPORT_DAYS_AHEAD):
                continue
            detail = f"{r['report_name']} 预约披露".strip()
            if r["change_count"]:
                detail += f"(已改期{r['change_count']}次)"
            events.append(
                CalendarEvent(
                    event_date=r["appoint_date"],
                    category="业绩披露",
                    symbol=r["code"],
                    name=a_symbols[r["code"]],
                    detail=detail,
                )
            )
    return events, []


def _fetch_a_share_dividends(
    a_symbols: dict[str, str], today: date | None = None
) -> tuple[list[CalendarEvent], list[str]]:
    """A股分红送转除权除息日, 东财 RPT_SHAREBONUS_DET。"""
    if not a_symbols:
        return [], []
    from src import fallback_sources as fb

    today = today or date.today()
    try:
        rows = fb.dividend_plans(list(a_symbols), today)
    except Exception as e:
        logger.warning(f"分红送转日历失败: {e}")
        return [], [f"分红送转日历获取失败: {str(e)[:100]}"]
    events = []
    for r in rows:
        if r["code"] not in a_symbols or not _in_window(r["ex_date"], today, DIVIDEND_DAYS_AHEAD):
            continue
        detail = r["plan"] or "分红送转"
        if r["record_date"]:
            detail += f", 股权登记日 {r['record_date']}"
        events.append(
            CalendarEvent(
                event_date=r["ex_date"],
                category="除权除息",
                symbol=r["code"],
                name=a_symbols[r["code"]],
                detail=detail,
            )
        )
    return events, []


def _yahoo_calendar(yahoo_symbol: str, market: str) -> dict | None:
    from src.global_stock import fetch_calendar_events

    return fetch_calendar_events(yahoo_symbol)


def _fetch_global_corporate_events(
    symbols: dict[str, tuple[str, str]],
    us_earnings_covered: set[str],
    today: date | None = None,
) -> tuple[list[CalendarEvent], list[str]]:
    """美股/港股财报日与除息日 (Yahoo calendarEvents)。

    美股财报已由 Nasdaq/Finnhub 覆盖的标的不重复添加; Yahoo 标注预估的日期在详情里注明。
    """
    if not symbols:
        return [], []
    from src.global_stock import to_yahoo_symbol

    today = today or date.today()
    events: list[CalendarEvent] = []
    failed: list[str] = []
    for symbol, (name, market) in symbols.items():
        info = _yahoo_calendar(to_yahoo_symbol(symbol, market), market)
        if info is None:
            failed.append(symbol)
            continue
        earnings = info.get("earnings_date")
        if (
            earnings
            and symbol not in us_earnings_covered
            and _in_window(earnings, today, GLOBAL_EVENTS_DAYS_AHEAD)
        ):
            detail = "财报(日期预估,公司未确认)" if info.get("earnings_is_estimate") else "财报"
            events.append(CalendarEvent(earnings, "财报", symbol, name, detail))
        ex_div = info.get("ex_dividend_date")
        if ex_div and _in_window(ex_div, today, GLOBAL_EVENTS_DAYS_AHEAD):
            events.append(CalendarEvent(ex_div, "除权除息", symbol, name, "除息日"))
    warnings = []
    if failed:
        warnings.append(f"美港股事件日历(Yahoo) {len(failed)} 只取数失败: {', '.join(failed[:8])}")
    return events, warnings
