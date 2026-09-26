"""备用数据源 —— 主源失败时的独立降级通道。

设计原则: 每类数据的备胎必须**换域名或换传输层**, 与主源不共用风控面。
东财系接口(datacenter/push2/push2ex)共用一套风控, 单纯重试无意义。

端点来源: github.com/simonlin1212/{a-stock-data,global-stock-data} 的
「备用源速查」章节, 全部于 2026-08-03 在本机代理环境下实测通过。

| 数据 | 主源 | 本模块备胎 | 实测 |
|------|------|-----------|------|
| 解禁/新股/龙虎榜 | akshare(requests) | 东财 datacenter + urllib 传输 | 3/3 |
| 个股资金流 | 东财 push2(httpx) | 新浪日级四档净额 | 8/8 |
| 龙虎榜 | 东财 datacenter | 深交所+上交所官方 | 10行/444行 |
| 美股日K(含成交量) | Yahoo chart | 新浪美股 US_MinKService | 10001根 |
| 美股财报日历 | Finnhub(需key) | Nasdaq(零鉴权) | 574家/日 |
| 北向活跃股 | — | HKEX 官方日统计 | 通 |
"""

import json
import logging
import re
from datetime import date, datetime

from src.http_urllib import (
    urllib_get_json,
    urllib_get_jsonp,
    urllib_get_text,
)

logger = logging.getLogger(__name__)

EM_DATACENTER_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
EM_HEADERS = {"Referer": "https://data.eastmoney.com/"}


# ═══════════════════════════════════════════════════════════
# 东财 datacenter (换 urllib 传输层, 不换域名 —— 见 http_urllib 模块说明)
# ═══════════════════════════════════════════════════════════


def em_datacenter(
    report_name: str,
    columns: str = "ALL",
    filter_: str = "",
    sort_columns: str = "",
    sort_types: str = "",
    page_size: int = 500,
) -> list[dict]:
    """东财数据中心统一查询, 走 urllib 传输。

    Raises:
        Exception: 网络/解析失败, 由调用方降级
    """
    params = {
        "reportName": report_name,
        "columns": columns,
        "pageSize": str(page_size),
        "pageNumber": "1",
        "source": "WEB",
        "client": "WEB",
    }
    if filter_:
        params["filter"] = filter_
    if sort_columns:
        params["sortColumns"] = sort_columns
        params["sortTypes"] = sort_types or "1"
    payload = urllib_get_json(EM_DATACENTER_URL, params, EM_HEADERS)
    if not isinstance(payload, dict):
        raise ValueError("东财 datacenter 返回非对象")
    return (payload.get("result") or {}).get("data") or []


def restricted_release(start: date, end: date) -> list[dict]:
    """限售解禁日历 (东财 RPT_LIFT_STAGE, urllib 传输)。

    Returns:
        [{code, name, free_date(ISO), lift_market_cap_yi, free_ratio_pct}]
        LIFT_MARKET_CAP 单位是万元, 转亿元需 /1e4。
    """
    rows = em_datacenter(
        "RPT_LIFT_STAGE",
        columns="SECURITY_CODE,SECURITY_NAME_ABBR,FREE_DATE,LIFT_MARKET_CAP,FREE_RATIO",
        filter_=f"(FREE_DATE>='{start.isoformat()}')(FREE_DATE<='{end.isoformat()}')",
        sort_columns="FREE_DATE",
        sort_types="1",
    )
    out = []
    for r in rows:
        cap = r.get("LIFT_MARKET_CAP")
        ratio = r.get("FREE_RATIO")
        out.append(
            {
                "code": str(r.get("SECURITY_CODE", "")).zfill(6),
                "name": str(r.get("SECURITY_NAME_ABBR", "")),
                "free_date": str(r.get("FREE_DATE", ""))[:10],
                # 东财该字段单位为万元
                "lift_market_cap_yi": round(float(cap) / 1e4, 2) if cap else None,
                # FREE_RATIO 是小数占比(0.4395), 转百分比
                "free_ratio_pct": round(float(ratio) * 100, 2) if ratio else None,
            }
        )
    return out


def ipo_calendar() -> list[dict]:
    """新股申购日历 (东财 RPTA_APP_IPOAPPLY, urllib 传输)。

    Returns:
        [{code, name, apply_date(ISO), issue_price, predict_pe}]
    """
    rows = em_datacenter(
        "RPTA_APP_IPOAPPLY",
        columns=(
            "SECURITY_CODE,SECURITY_NAME,APPLY_DATE,ISSUE_PRICE,"
            "AFTER_ISSUE_PE,ONLINE_ISSUE_NUM"
        ),
        sort_columns="APPLY_DATE",
        sort_types="-1",
        page_size=100,
    )
    out = []
    for r in rows:
        raw = str(r.get("APPLY_DATE") or "")[:10]
        if not raw:
            continue
        out.append(
            {
                "code": str(r.get("SECURITY_CODE", "")),
                "name": str(r.get("SECURITY_NAME", "")),
                "apply_date": raw,
                "issue_price": r.get("ISSUE_PRICE"),
                "after_issue_pe": r.get("AFTER_ISSUE_PE"),
            }
        )
    return out


def _em_code_in(codes: list[str]) -> str:
    """东财 filter 的多代码条件: (SECURITY_CODE in ("600519","000001"))。"""
    quoted = ",".join(f'"{c}"' for c in codes)
    return f"(SECURITY_CODE in ({quoted}))"


def report_appointments(codes: list[str], period: date) -> list[dict]:
    """定期报告预约披露日 (东财 RPT_PUBLIC_BS_APPOIN, urllib 传输)。

    APPOINT_PUBLISH_DATE 是当前有效的预约日(改期后会更新); 已披露(IS_PUBLISH=1)的跳过。

    Returns:
        [{code, name, report_name, appoint_date(ISO), change_count}]
    """
    if not codes:
        return []
    rows = em_datacenter(
        "RPT_PUBLIC_BS_APPOIN",
        filter_=f"(REPORT_DATE='{period.isoformat()}')" + _em_code_in(codes),
        sort_columns="APPOINT_PUBLISH_DATE",
        sort_types="1",
    )
    out = []
    for r in rows:
        if str(r.get("IS_PUBLISH") or "0") == "1":
            continue
        appoint = str(r.get("APPOINT_PUBLISH_DATE") or "")[:10]
        if not appoint:
            continue
        changes = sum(
            1 for k in ("FIRST_CHANGE_DATE", "SECOND_CHANGE_DATE", "THIRD_CHANGE_DATE") if r.get(k)
        )
        out.append(
            {
                "code": str(r.get("SECURITY_CODE", "")).zfill(6),
                "name": str(r.get("SECURITY_NAME_ABBR", "")),
                "report_name": str(r.get("REPORT_TYPE_NAME", "")),
                "appoint_date": appoint,
                "change_count": changes,
            }
        )
    return out


def dividend_plans(codes: list[str], start: date) -> list[dict]:
    """分红送转实施计划 (东财 RPT_SHAREBONUS_DET, urllib 传输), 只取除权除息日 >= start。

    Returns:
        [{code, name, ex_date(ISO), record_date(ISO), plan, progress}]
    """
    if not codes:
        return []
    rows = em_datacenter(
        "RPT_SHAREBONUS_DET",
        filter_=f"(EX_DIVIDEND_DATE>='{start.isoformat()}')" + _em_code_in(codes),
        sort_columns="EX_DIVIDEND_DATE",
        sort_types="1",
    )
    out = []
    for r in rows:
        ex_date = str(r.get("EX_DIVIDEND_DATE") or "")[:10]
        if not ex_date:
            continue
        out.append(
            {
                "code": str(r.get("SECURITY_CODE", "")).zfill(6),
                "name": str(r.get("SECURITY_NAME_ABBR", "")),
                "ex_date": ex_date,
                "record_date": str(r.get("EQUITY_RECORD_DATE") or "")[:10],
                "plan": str(r.get("IMPL_PLAN_PROFILE") or ""),
                "progress": str(r.get("ASSIGN_PROGRESS") or ""),
            }
        )
    return out


def lhb_detail(start: date, end: date) -> list[dict]:
    """龙虎榜明细 (东财 RPT_DAILYBILLBOARD_DETAILSNEW, urllib 传输)。

    Returns:
        [{code, name, trade_date, reason, net_buy_yi}]
    """
    rows = em_datacenter(
        "RPT_DAILYBILLBOARD_DETAILSNEW",
        columns=(
            "SECURITY_CODE,SECURITY_NAME_ABBR,TRADE_DATE,"
            "EXPLANATION,BILLBOARD_NET_AMT"
        ),
        filter_=f"(TRADE_DATE<='{end.isoformat()}')(TRADE_DATE>='{start.isoformat()}')",
        sort_columns="TRADE_DATE",
        sort_types="-1",
    )
    out = []
    for r in rows:
        net = r.get("BILLBOARD_NET_AMT")
        out.append(
            {
                "code": str(r.get("SECURITY_CODE", "")).zfill(6),
                "name": str(r.get("SECURITY_NAME_ABBR", "")),
                "trade_date": str(r.get("TRADE_DATE", ""))[:10],
                "reason": str(r.get("EXPLANATION") or ""),
                "net_buy_yi": round(float(net) / 1e8, 2) if net else 0.0,
            }
        )
    return out


# ═══════════════════════════════════════════════════════════
# 新浪 —— 个股资金流 (替代 push2, push2 实测 urllib 也救不回来)
# ═══════════════════════════════════════════════════════════

SINA_MONEYFLOW_URL = (
    "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
    "MoneyFlow.ssl_qsfx_zjlrqs"
)
SINA_HEADERS = {"Referer": "https://finance.sina.com.cn/"}


def _sina_prefix(code: str) -> str:
    """6位A股代码 -> 新浪前缀码。4/8/92 开头是北交所。"""
    if code.startswith(("4", "8", "92")):
        return f"bj{code}"
    return f"sh{code}" if code.startswith(("6", "9")) else f"sz{code}"


def sina_fund_flow(code: str, days: int = 2) -> list[dict]:
    """个股日级主力资金流 (新浪, 非东财)。

    ⚠️ 口径提示: 新浪 netamount 与东财主力净额口径不同(新浪按大单方向聚合),
    数量级可比但不等值; 跨源对比时不要混用。

    Returns:
        [{date, close, net_amount_yi, turnover}], 按日期倒序(最新在前)
    """
    params = {
        "page": "1",
        "num": str(days),
        "sort": "opendate",
        "asc": "0",
        "daima": _sina_prefix(str(code).zfill(6)),
    }
    arr = urllib_get_jsonp(SINA_MONEYFLOW_URL, params, SINA_HEADERS, bracket="[")
    out = []
    for x in arr:
        try:
            net_yi = float(x.get("netamount", 0)) / 1e8
        except (TypeError, ValueError):
            continue
        out.append(
            {
                "date": str(x.get("opendate", ""))[:10],
                "close": x.get("trade"),
                "net_amount_yi": round(net_yi, 2),
                "turnover": x.get("turnover"),
            }
        )
    return out


# ═══════════════════════════════════════════════════════════
# 交易所官方 —— 龙虎榜 (独立域名, 东财整体被封时仍可用)
# ═══════════════════════════════════════════════════════════

SZSE_LHB_URL = "https://www.szse.cn/api/report/ShowReport/data"
SSE_LHB_URL = "https://query.sse.com.cn/infodisplay/showTradePublicFile.do"


def szse_lhb_official(trade_date: date) -> list[dict]:
    """深市龙虎榜 (深交所官方, 结构化)。

    Returns:
        [{code, name, reason, amount}]
    """
    params = {
        "SHOWTYPE": "JSON",
        "CATALOGID": "1842_xxpl",
        "TABKEY": "tab1",
        "txtStart": trade_date.isoformat(),
        "txtEnd": trade_date.isoformat(),
        "random": "0.9",
    }
    payload = urllib_get_json(
        SZSE_LHB_URL,
        params,
        {"Referer": "https://www.szse.cn/disclosure/supervision/dealinfo/index.html"},
        lax_ssl=True,
    )
    if not isinstance(payload, list) or not payload:
        return []
    out = []
    for row in payload[0].get("data", []) or []:
        code = re.sub(r"<[^>]+>", "", str(row.get("zqdm", ""))).strip()
        out.append(
            {
                "code": code.zfill(6),
                "name": re.sub(r"<[^>]+>", "", str(row.get("zqjc", ""))).strip(),
                "reason": str(row.get("plyy", "")).strip(),
                "amount": row.get("cjje"),
            }
        )
    return out


def sse_lhb_official(trade_date: date) -> str:
    """沪市龙虎榜全文 (上交所官方, 含营业部席位)。

    上交所只提供纯文本公告, 不做结构化解析 —— 返回全文供关键词命中。
    """
    params = {
        "jsonCallBack": "cb",
        "isPagination": "false",
        "dateTx": trade_date.isoformat(),
    }
    payload = urllib_get_jsonp(
        SSE_LHB_URL,
        params,
        {"Referer": "https://www.sse.com.cn/disclosure/diclosure/public/"},
        bracket="{",
    )
    contents = payload.get("fileContents") or [] if isinstance(payload, dict) else []
    return "\n".join(str(x) for x in contents)


def lhb_exchange_official(trade_date: date, wanted: set[str]) -> list[dict]:
    """龙虎榜官方备胎: 深市结构化 + 沪市全文命中。

    Returns:
        [{code, name, trade_date, reason, net_buy_yi}] — 官方源无净买额, 恒为 0.0
    """
    hits: list[dict] = []
    iso = trade_date.isoformat()

    try:
        for row in szse_lhb_official(trade_date):
            if row["code"] in wanted:
                hits.append(
                    {
                        "code": row["code"],
                        "name": row["name"],
                        "trade_date": iso,
                        "reason": row["reason"],
                        "net_buy_yi": 0.0,
                    }
                )
    except Exception as e:
        logger.warning(f"深交所龙虎榜失败: {str(e)[:100]}")

    # 沪市: 全文里搜 watchlist 的 6 位代码
    sh_wanted = {c for c in wanted if c.startswith(("6", "9"))}
    if sh_wanted:
        try:
            text = sse_lhb_official(trade_date)
            for code in sh_wanted:
                if code in text:
                    hits.append(
                        {
                            "code": code,
                            "name": "",
                            "trade_date": iso,
                            "reason": "上交所每日交易公开信息(全文命中)",
                            "net_buy_yi": 0.0,
                        }
                    )
        except Exception as e:
            logger.warning(f"上交所龙虎榜失败: {str(e)[:100]}")

    return hits


# ═══════════════════════════════════════════════════════════
# 美股 —— 新浪日K(含真实成交量) + Nasdaq 财报日历
# ═══════════════════════════════════════════════════════════

SINA_US_KLINE_URL = (
    "https://stock.finance.sina.com.cn/usstock/api/jsonp.php/var/"
    "US_MinKService.getDailyK"
)
NASDAQ_EARNINGS_URL = "https://api.nasdaq.com/api/calendar/earnings"


def sina_us_kline(ticker: str, num: int = 120) -> list[dict]:
    """美股日K (新浪, 含真实成交量) —— 修 Yahoo 回填 volume=0。

    ⚠️ 上游 num 参数实测不生效, 一律返回自 1984 年起的全量历史(约 10k 根),
    因此本函数在客户端截取最近 num 根。

    Returns:
        [{date, open, high, low, close, volume}] 按日期升序, 最多 num 根
    """
    text = urllib_get_text(
        SINA_US_KLINE_URL,
        {"symbol": ticker.upper(), "num": str(num)},
        SINA_HEADERS,
    )
    m = re.search(r"\((\[.+\])\)", text, re.S)
    if not m:
        raise ValueError(f"新浪美股K线载荷未找到: {text[:80]}")
    items = json.loads(m.group(1))
    out = []
    for x in items:
        try:
            out.append(
                {
                    "date": str(x.get("d", ""))[:10],
                    "open": float(x.get("o", 0)),
                    "high": float(x.get("h", 0)),
                    "low": float(x.get("l", 0)),
                    "close": float(x.get("c", 0)),
                    "volume": int(float(x.get("v", 0))),
                }
            )
        except (TypeError, ValueError):
            continue
    out.sort(key=lambda r: r["date"])
    return out[-num:] if num > 0 else out


def repair_us_volume(history_path, symbols: list[str]) -> tuple[int, list[str]]:
    """用新浪日K补全 us_quote_history.json 里 volume=0 的行。

    Yahoo 回填长期返回 volume=0, 导致 anomaly 的 volume_dryup /
    distribution_warning 每天产出大量伪 high 信号(2026-08-01 一次就有 10 条)。
    本函数在管线层做独立修复: 只填 0 值, 不改已有非零成交量。

    Args:
        history_path: output/us_quote_history.json 的 Path
        symbols: 需修复的美股 ticker (港股新浪无此接口, 跳过)

    Returns:
        (修复行数, warnings)
    """
    import time

    if not history_path.exists():
        return 0, []
    try:
        history = json.loads(history_path.read_text(encoding="utf-8"))
    except Exception as e:
        return 0, [f"美股成交量修复: 历史文件读取失败 {str(e)[:80]}"]

    fixed = 0
    failed: list[str] = []
    for sym in symbols:
        rows = history.get(sym)
        if not rows:
            continue
        zero_dates = {r["date"] for r in rows if not r.get("volume")}
        if not zero_dates:
            continue
        try:
            # 覆盖历史里最早的缺口即可, 上限 500 根
            kline = {k["date"]: k["volume"] for k in sina_us_kline(sym, 500)}
        except Exception as e:
            failed.append(sym)
            logger.warning(f"新浪美股K线失败 {sym}: {str(e)[:80]}")
            continue
        for r in rows:
            if not r.get("volume") and kline.get(r["date"]):
                r["volume"] = kline[r["date"]]
                fixed += 1
        time.sleep(0.25)

    if fixed:
        try:
            history_path.write_text(
                json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as e:
            return 0, [f"美股成交量修复: 写回失败 {str(e)[:80]}"]

    warnings = []
    if failed:
        warnings.append(
            f"美股成交量修复: 新浪源 {len(failed)} 只失败 ({', '.join(failed[:5])})"
        )
    return fixed, warnings


def nasdaq_earnings(day: date) -> list[dict]:
    """美股财报日历 (Nasdaq 官方, 零鉴权 —— 替代需 key 的 Finnhub)。

    Returns:
        [{symbol, name, when, eps_forecast}]
        when: 盘前 / 盘后 / 盘中 / ""
    """
    payload = urllib_get_json(
        NASDAQ_EARNINGS_URL,
        {"date": day.isoformat()},
        {"Accept": "application/json"},
    )
    rows = ((payload.get("data") or {}) if isinstance(payload, dict) else {}).get(
        "rows"
    ) or []
    when_map = {
        "time-pre-market": "盘前",
        "time-after-hours": "盘后",
        "time-not-supplied": "",
    }
    out = []
    for r in rows:
        out.append(
            {
                "symbol": str(r.get("symbol", "")),
                "name": str(r.get("name", "")),
                "when": when_map.get(str(r.get("time", "")), ""),
                "eps_forecast": str(r.get("epsForecast") or "").strip(),
            }
        )
    return out


# ═══════════════════════════════════════════════════════════
# HKEX —— 北向成交额 + 十大活跃股
# ═══════════════════════════════════════════════════════════

HKEX_DAILY_URL = "https://www.hkex.com.hk/chi/csm/DailyStat/data_tab_daily_{d}c.js"


def hkex_northbound(trade_date: date) -> dict:
    """北向权威日统计 (HKEX 官方)。

    ⚠️ 重要口径: HKEX 北向只披露 **成交额(Total Turnover)**, 不含买卖拆分,
    因此**拿不到净买入**。它不能替代同花顺 hexin 的净流入数字, 只能作为
    「北向在买什么」的补充证据(十大活跃股)。

    Returns:
        {date, sse_turnover_yi, szse_turnover_yi, top10: [{market, rank, code, name, turnover_yi}]}
    """
    url = HKEX_DAILY_URL.format(d=trade_date.strftime("%Y%m%d"))
    text = urllib_get_text(url, encoding="utf-8-sig")
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end <= start:
        raise ValueError("HKEX 载荷未找到")
    blocks = json.loads(text[start : end + 1])

    def _num(s) -> float | None:
        try:
            return float(str(s).replace(",", ""))
        except (TypeError, ValueError):
            return None

    result: dict = {
        "date": trade_date.isoformat(),
        "sse_turnover_yi": None,
        "szse_turnover_yi": None,
        "top10": [],
    }
    for blk in blocks:
        market = str(blk.get("market", ""))
        if "Northbound" not in market:
            continue
        tag = "sse" if market.startswith("SSE") else "szse"
        for c in blk.get("content", []) or []:
            table = c.get("table") or {}
            rows = table.get("tr") or []
            if table.get("classname") == "tradingTable" and rows:
                # 首行是 Total Turnover, 单位百万港元 -> 亿
                v = _num(rows[0].get("td", [[None]])[0][0])
                if v is not None:
                    result[f"{tag}_turnover_yi"] = round(v / 100, 2)
            elif table.get("classname") == "top10Table":
                for r in rows:
                    td = (r.get("td") or [[]])[0]
                    if len(td) < 4:
                        continue
                    result["top10"].append(
                        {
                            "market": tag.upper(),
                            "rank": td[0],
                            "code": str(td[1]).zfill(6),
                            "name": str(td[2]).replace("　", "").strip(),
                            "turnover_yi": round((_num(td[-1]) or 0) / 1e8, 2),
                        }
                    )
    return result


# ═══════════════════════════════════════════════════════════
# 陈旧数据检测 —— 同花顺 hexin 北向连续多日返回同一数值
# ═══════════════════════════════════════════════════════════


def detect_stale(path, key: str, value, max_repeat: int = 2) -> tuple[bool, dict]:
    """检测某数值是否连续多次未变化(上游缓存/断更的典型表现)。

    Args:
        path: 状态文件 Path
        key: 指标名
        value: 本次取到的值
        max_repeat: 连续相同多少次后判为陈旧

    Returns:
        (is_stale, 新状态dict) —— 调用方负责写回
    """
    state: dict = {}
    try:
        if path.exists():
            state = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        state = {}

    entry = state.get(key) or {}
    same = entry.get("value") == value
    count = (entry.get("repeat", 0) + 1) if same else 0
    state[key] = {
        "value": value,
        "repeat": count,
        "last_seen": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    return count >= max_repeat, state
