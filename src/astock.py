"""A股增强数据模块。

集成 a-stock-data 工具包 (https://github.com/simonlin1212/a-stock-data) 的两项能力:
- 基本面: 腾讯财经 API (PE/PB/市值/换手率/量比/涨跌停, HTTP GBK, 不封IP, 无需key)
- 价格兜底: 腾讯财经前复权 K 线；mootdx 通达信 TCP 日K线保留为末级兜底

用法:
    basics = fetch_a_share_basics(["600519", "000001"])
    quotes = fetch_a_share_kline_mootdx("600519", bars=40)
"""

import json
import logging
import math
from datetime import date
from pathlib import Path

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
TENCENT_KLINE_HOSTS = (
    "https://web.ifzq.gtimg.cn",
    "https://proxy.finance.qq.com/ifzqgtimg",
    "https://ifzq.gtimg.cn",
)


def fetch_a_share_kline_tencent(symbol: str, bars: int = 40) -> list[DailyQuote]:
    """腾讯前复权日线兜底；量从「手」转为股，成交额缺失保持 0。

    北交所历史仅返回一根，不能冒充完整 K 线。单次最多 640 根。
    """
    if _a_share_prefix(symbol) == "bj":
        logger.warning("腾讯历史 K 线不支持北交所: %s", symbol)
        return []
    if not symbol.isdigit() or len(symbol) != 6:
        logger.warning("腾讯历史 K 线代码无效: %s", symbol)
        return []
    code = _a_share_prefix(symbol) + symbol
    count = min(max(bars, 1), 640)
    errors = []
    for host in TENCENT_KLINE_HOSTS:
        try:
            response = httpx.get(
                host + "/appstock/app/fqkline/get",
                params={"param": f"{code},day,,,{count},qfq"},
                headers={"Referer": "https://gu.qq.com/", "User-Agent": TENCENT_USER_AGENT},
                timeout=15.0,
            )
            response.raise_for_status()
            payload = response.json()
            data = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(data, dict):
                raise ValueError("data 不是对象")
            node = data.get(code, {})
            if not isinstance(node, dict):
                raise ValueError("data node 不是对象")
            if "qfqday" in node:
                rows = node["qfqday"]
                if not rows and node.get("day"):
                    raise ValueError("qfqday 为空但原始日线非空，拒绝混用复权口径")
            else:
                rows = node.get("day")  # 从未除权的标的只返回 day
            if not isinstance(rows, list) or not rows:
                raise ValueError("未返回日线列表")
            parsed = []
            seen = set()
            for row in rows:
                quote_date = date.fromisoformat(str(row[0])[:10])
                if quote_date in seen:
                    raise ValueError(f"重复日期 {quote_date}")
                seen.add(quote_date)
                open_, close, high, low = map(float, (row[1], row[2], row[3], row[4]))
                volume = int(float(row[5]) * 100)
                if (not all(math.isfinite(price) and price > 0 for price in (open_, close, high, low))
                        or volume < 0):
                    raise ValueError(f"非正价格或负成交量 {quote_date}")
                parsed.append((quote_date, open_, close, high, low, volume))
            parsed.sort()
            quotes = []
            previous = None
            for quote_date, open_, close, high, low, volume in parsed:
                change_pct = (close / previous - 1) * 100 if previous else 0.0
                quotes.append(DailyQuote(
                    symbol=symbol, date=quote_date, open=open_, close=close,
                    high=high, low=low, volume=volume, turnover=0.0,
                    change_pct=round(change_pct, 4),
                ))
                previous = close
            logger.info("A 股 %s 日线降级为腾讯前复权: %s (%s 根)", symbol, host, len(quotes))
            return quotes
        except (httpx.HTTPError, ValueError, TypeError, KeyError, IndexError) as exc:
            errors.append(f"{host}: {exc}")
    logger.warning("腾讯历史 K 线获取失败 %s: %s", symbol, "; ".join(errors))
    return []


def _a_share_prefix(code: str) -> str:
    """6位A股代码 -> 腾讯/通达信市场前缀。"""
    if code.startswith(("4", "8", "92")):
        return "bj"
    if code.startswith(("6", "9")):
        return "sh"
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
        logger.warning(f"腾讯财经基本面请求失败: {str(e)[:120]}")
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
        logger.warning(f"mootdx 获取 {symbol} 行情失败: {str(e)[:120]}")
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


# ═══════════════════════════════════════════════════════════
# 资金面: 主力资金流 + 龙虎榜 (东财批量接口, 每日各一次调用)
# ═══════════════════════════════════════════════════════════


# 东财 push2 主力资金流批量接口 (按 secids 精确取 watchlist, 小响应)
PUSH2_ULIST_URL = "https://push2.eastmoney.com/api/qt/ulist.np/get"
PUSH2_FUND_FLOW_UT = "b2884a393a59ad64002292a3e90d46a5"
# 本地代理对大响应易截断, secids 分小批(每批响应保持极小最稳)
FUND_FLOW_BATCH = 15


def _a_share_secid(code: str) -> str:
    """6位A股代码 -> 东财 secid (1.=沪, 0.=深/北)。"""
    return f"1.{code}" if code.startswith(("6", "9")) else f"0.{code}"


def fetch_fund_flow_rank(symbols: list[str], names: dict[str, str] | None = None):
    """watchlist A股当日主力资金流 (东财 push2 ulist.np, 按 secids 精确批量)。

    改用 secids 精确批量而非全市场排行: 响应仅 watchlist 大小, 小载荷,
    在本地代理环境下比全市场 6000 行请求稳定得多 (大响应易被代理截断)。
    经 httpx(honor proxy) 直连 push2, 绕开 akshare/requests 对 push2 的 TLS 失败。

    Returns:
        (list[FundFlowInfo] 按主力净流入排序, warnings)
    """
    from src.models import FundFlowInfo
    from src.net_retry import retry_call

    if not symbols:
        return [], []

    codes = [str(s).zfill(6) for s in symbols]
    flows: list[FundFlowInfo] = []
    failed_batches = 0
    n_batches = 0
    for i in range(0, len(codes), FUND_FLOW_BATCH):
        batch = codes[i : i + FUND_FLOW_BATCH]
        n_batches += 1
        params = {
            "fltt": "2",
            "invt": "2",
            "ut": PUSH2_FUND_FLOW_UT,
            "secids": ",".join(_a_share_secid(c) for c in batch),
            "fields": "f12,f14,f62,f184",  # 代码/名称/主力净额/主力净占比
        }
        try:
            resp = retry_call(
                lambda p=params: httpx.get(
                    PUSH2_ULIST_URL,
                    params=p,
                    headers={"User-Agent": TENCENT_USER_AGENT},
                    timeout=TENCENT_REQUEST_TIMEOUT,
                    trust_env=True,  # push2 直连被 TLS 阻断, 必须走本地代理
                ),
                attempts=2,
                label=f"主力资金流(push2 批{n_batches})",
            )
            resp.raise_for_status()
            diff = (resp.json().get("data") or {}).get("diff") or []
        except Exception as e:
            logger.warning(f"Fund flow batch {n_batches} failed: {e}")
            failed_batches += 1
            continue

        rows = diff.values() if isinstance(diff, dict) else diff
        for row in rows:
            net = row.get("f62")
            pct = row.get("f184")
            try:
                net_yi = float(net) / 1e8
                pct_f = float(pct)
            except (TypeError, ValueError):
                continue  # 停牌/无数据返回 "-"
            flows.append(
                FundFlowInfo(
                    symbol=str(row.get("f12", "")).zfill(6),
                    name=str(row.get("f14", "")),
                    main_net_inflow_yi=round(net_yi, 2),
                    main_net_pct=round(pct_f, 2),
                )
            )

    # push2 全灭时降级新浪日级源 (逐标的, 非东财域名)
    if not flows and failed_batches == n_batches:
        logger.warning("push2 主力资金流全部失败, 降级新浪日级源")
        return _fetch_fund_flow_sina(codes, names or {})

    flows.sort(key=lambda f: f.main_net_inflow_yi, reverse=True)
    warnings = []
    if failed_batches:
        warnings.append(
            f"主力资金流: {failed_batches}/{n_batches} 批次失败(代理不稳), 数据不完整"
        )
    return flows, warnings


def _fetch_fund_flow_sina(codes: list[str], names: dict[str, str]):
    """主力资金流兜底: 新浪日级四档净额 (非东财域名, 逐标的查询)。

    push2.eastmoney.com 在本地代理下实测连发 0/8 成功, 换传输层也救不回来,
    只能换源。新浪按 symbol 查询, 74 只约 30s。

    ⚠️ 口径: 新浪 netamount 按大单方向聚合, 与东财主力净额不等值; 且无净占比。
    """
    import time

    from src.models import FundFlowInfo
    from src.fallback_sources import sina_fund_flow

    flows: list[FundFlowInfo] = []
    failed = 0
    for code in codes:
        try:
            rows = sina_fund_flow(code, days=1)
        except Exception:
            failed += 1
            continue
        if not rows:
            failed += 1
            continue
        flows.append(
            FundFlowInfo(
                symbol=code,
                name=names.get(code, code),  # 新浪该接口不返回名称
                main_net_inflow_yi=rows[0]["net_amount_yi"],
                main_net_pct=None,
                source="新浪",
            )
        )
        time.sleep(0.25)  # 新浪限速

    flows.sort(key=lambda f: f.main_net_inflow_yi, reverse=True)
    if not flows:
        return [], [f"主力资金流获取失败(东财 push2 + 新浪均失败, {failed} 只)"]
    warnings = ["主力资金流: 东财 push2 全灭, 已降级新浪日级源(口径不同, 无净占比)"]
    if failed:
        warnings.append(f"主力资金流: 新浪源 {failed}/{len(codes)} 只无数据")
    return flows, warnings


def fetch_lhb_hits(symbols: list[str], days_back: int = 3):
    """近N日龙虎榜(全市场一次调用), 过滤 watchlist 命中。

    游资票一眼定性: 上榜 + 涨幅大 = 游资一波流概率高。

    源顺序: 东财 datacenter(urllib, 主, 唯一带净买额) -> 沪深交易所官方
    -> akshare(requests) -> 新浪。官方源与新浪源均无净买额字段。

    Returns:
        (list[LhbEntry], warnings)
    """
    from datetime import timedelta

    from src.models import LhbEntry
    from src.net_retry import retry_call

    wanted = set(symbols)
    end = date.today()
    start = end - timedelta(days=days_back)

    # 主源: 东财 datacenter, urllib 传输 —— 唯一提供净买额的源
    try:
        from src.fallback_sources import lhb_detail

        rows = retry_call(
            lambda: lhb_detail(start, end),
            attempts=4,
            base_delay=2.0,
            label="龙虎榜(东财/urllib)",
        )
        entries = [
            LhbEntry(
                symbol=r["code"],
                name=r["name"],
                trade_date=r["trade_date"],
                reason=r["reason"],
                net_buy_yi=r["net_buy_yi"],
            )
            for r in rows
            if r["code"] in wanted
        ]
        return entries, []
    except Exception as e:
        logger.warning(f"龙虎榜(东财/urllib)失败, 降级交易所官方: {str(e)[:120]}")

    # 备胎1: 沪深交易所官方(独立域名, 东财整体被封时仍可用)
    try:
        from src.fallback_sources import lhb_exchange_official

        hits: list[LhbEntry] = []
        seen: set[tuple[str, str]] = set()
        probe = end
        for _ in range(days_back * 2 + 1):
            if probe < start:
                break
            for r in lhb_exchange_official(probe, wanted):
                key = (r["code"], r["trade_date"])
                if key in seen:
                    continue
                seen.add(key)
                hits.append(
                    LhbEntry(
                        symbol=r["code"],
                        name=r["name"],
                        trade_date=r["trade_date"],
                        reason=r["reason"],
                        net_buy_yi=0.0,
                    )
                )
            probe -= timedelta(days=1)
        if hits:
            return hits, ["龙虎榜: 东财失败, 已降级交易所官方源(无净买额字段)"]
        logger.warning("交易所官方龙虎榜无命中, 继续降级 akshare")
    except Exception as e:
        logger.warning(f"交易所官方龙虎榜失败: {str(e)[:120]}")

    try:
        import akshare as ak

        df = retry_call(
            lambda: ak.stock_lhb_detail_em(
                start_date=start.strftime("%Y%m%d"), end_date=end.strftime("%Y%m%d")
            ),
            attempts=2,
            label="龙虎榜(东财/akshare)",
        )
    except Exception as e:
        logger.warning(f"LHB fetch failed (东财, 重试后): {e}")
        # 东财兜底: 新浪龙虎榜每日详情 (非东财, 单日一次调用)
        return _fetch_lhb_hits_sina(wanted, days_back)

    entries = []
    try:
        for _, row in df.iterrows():
            code = str(row.get("代码", "")).zfill(6)
            if code not in wanted:
                continue
            net = row.get("龙虎榜净买额")
            try:
                net_yi = float(net) / 1e8
            except (TypeError, ValueError):
                net_yi = 0.0
            entries.append(
                LhbEntry(
                    symbol=code,
                    name=str(row.get("名称", "")),
                    trade_date=str(row.get("上榜日", ""))[:10],
                    reason=str(row.get("上榜原因", "")),
                    net_buy_yi=round(net_yi, 2),
                )
            )
    except Exception as e:
        logger.warning(f"LHB parse failed: {e}")
        return [], [f"龙虎榜解析失败: {str(e)[:120]}"]
    return entries, []


def _fetch_lhb_hits_sina(wanted: set[str], days_back: int):
    """龙虎榜东财失败兜底: 新浪每日详情(非东财)。

    新浪按交易日查询, 无净买额字段(东财专有), 仅提供上榜原因+成交额。
    从今日往前逐个自然日尝试, 命中 watchlist 即收集, 覆盖 days_back 个交易日。
    """
    from datetime import timedelta

    from src.models import LhbEntry
    from src.net_retry import retry_call

    try:
        import akshare as ak
    except Exception as e:
        return [], [f"龙虎榜获取失败(东财+新浪均失败): {str(e)[:120]}"]

    entries: list = []
    seen: set[tuple[str, str]] = set()
    trading_days_found = 0
    probe = date.today()
    # 最多回溯 days_back*2 个自然日, 覆盖周末/节假日
    for _ in range(days_back * 2 + 1):
        if trading_days_found >= days_back:
            break
        day_str = probe.strftime("%Y%m%d")
        probe -= timedelta(days=1)
        try:
            df = retry_call(
                lambda d=day_str: ak.stock_lhb_detail_daily_sina(date=d),
                attempts=2,
                label=f"龙虎榜(新浪 {day_str})",
            )
        except Exception:
            continue
        if df is None or df.empty:
            continue
        trading_days_found += 1
        for _, row in df.iterrows():
            code = str(row.get("股票代码", "")).zfill(6)
            if code not in wanted:
                continue
            key = (code, day_str)
            if key in seen:
                continue
            seen.add(key)
            trade_date = f"{day_str[:4]}-{day_str[4:6]}-{day_str[6:]}"
            entries.append(
                LhbEntry(
                    symbol=code,
                    name=str(row.get("股票名称", "")),
                    trade_date=trade_date,
                    reason=str(row.get("指标", "")),
                    net_buy_yi=0.0,  # 新浪无净买额字段
                )
            )
    warn = ["龙虎榜: 东财失败, 已降级新浪源(无净买额字段)"]
    return entries, warn


# ═══════════════════════════════════════════════════════════
# 北向资金: 同花顺 hexin hsgtApi (非东财, 市场级资金面)
# ═══════════════════════════════════════════════════════════

HEXIN_HSGT_URL = "https://data.hexin.cn/market/hsgtApi/method/dayChart/"
HEXIN_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "Chrome/117.0.0.0 Safari/537.36"
    ),
    "Host": "data.hexin.cn",
    "Referer": "https://data.hexin.cn/",
}
# 沪股通/深股通单日净买入合理区间(亿), 用于剔除上游返回的坏数据
NORTHBOUND_SANE_ABS_YI = 300.0


def fetch_northbound_flow():
    """北向资金当日净流入 (同花顺 hexin, 非东财)。

    东财北向字段 2024-08 起断供, 用同花顺 dayChart 作替代。返回沪股通/深股通
    当日累计净买入(亿)。上游 sgt 有时数组损坏(长度/量级异常), 逐路校验,
    异常字段置 None 并降级为警告, 不污染报告。

    Returns:
        (NorthboundFlow | None, warnings)
    """
    from src.models import NorthboundFlow
    from src.net_retry import retry_call

    try:
        resp = retry_call(
            lambda: httpx.get(
                HEXIN_HSGT_URL, headers=HEXIN_HEADERS, timeout=TENCENT_REQUEST_TIMEOUT
            ),
            attempts=2,
            label="北向资金(同花顺)",
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning(f"Northbound fetch failed: {e}")
        return None, [f"北向资金获取失败: {str(e)[:120]}"]

    times = data.get("time") or []
    hgt = data.get("hgt") or []
    sgt = data.get("sgt") or []

    def _last_sane(series, ref_len: int) -> float | None:
        """取序列最后一个有效值; 长度与时间轴不匹配或量级越界视为坏数据。"""
        if not series or abs(len(series) - ref_len) > 2:
            return None
        for v in reversed(series):
            if v is None:
                continue
            try:
                fv = float(v)
            except (TypeError, ValueError):
                continue
            if abs(fv) > NORTHBOUND_SANE_ABS_YI:
                return None
            return round(fv, 2)
        return None

    ref_len = len(times)
    hgt_val = _last_sane(hgt, ref_len)
    sgt_val = _last_sane(sgt, ref_len)
    as_of = str(times[-1]) if times else ""

    warnings: list[str] = []
    if hgt_val is None and sgt_val is None:
        return None, ["北向资金: 同花顺上游数据异常(沪深股通均无效)"]
    if hgt_val is None:
        warnings.append("北向资金: 沪股通数据异常已忽略")
    if sgt_val is None:
        warnings.append("北向资金: 深股通数据异常已忽略")

    # 陈旧检测: hexin 曾连续三次运行返回同一数值(-9.28亿), 上游未刷新
    warnings.extend(_check_northbound_stale(hgt_val, sgt_val))

    return NorthboundFlow(hgt_net_yi=hgt_val, sgt_net_yi=sgt_val, as_of=as_of), warnings


NORTHBOUND_STATE_PATH = Path("output/source_state.json")


def _check_northbound_stale(hgt: float | None, sgt: float | None) -> list[str]:
    """北向数值连续多次未变化 = 上游缓存/断更, 标记为不可信。

    2026-08-01~08-03 实测: hexin 连续三次返回完全相同的 -9.28亿(实为 07-31 值),
    静默使用会把陈旧数据当成当日资金面证据。
    """
    from src.fallback_sources import detect_stale

    try:
        stale, state = detect_stale(
            NORTHBOUND_STATE_PATH, "northbound", [hgt, sgt], max_repeat=2
        )
        NORTHBOUND_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        NORTHBOUND_STATE_PATH.write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as e:
        logger.warning(f"北向陈旧检测失败(不影响主流程): {str(e)[:80]}")
        return []
    if stale:
        return [
            "北向资金: 同花顺连续多次返回相同数值, 疑似上游未刷新, **本日数值不可信**"
        ]
    return []


def fetch_northbound_top10(trade_date: date | None = None):
    """北向十大活跃股 (HKEX 官方) —— 「北向在买什么」的独立证据。

    ⚠️ HKEX 北向只披露成交额, 不含买卖拆分, 因此**没有净买入**,
    不能替代同花顺的净流入数字, 只作补充。

    Returns:
        (dict | None, warnings)
    """
    from datetime import timedelta

    from src.fallback_sources import hkex_northbound

    probe = trade_date or date.today()
    # HKEX 当日文件可能尚未发布, 往前找最近有数据的交易日
    for _ in range(5):
        try:
            return hkex_northbound(probe), []
        except Exception:
            probe -= timedelta(days=1)
    return None, ["北向活跃股(HKEX): 近5日均无数据"]
