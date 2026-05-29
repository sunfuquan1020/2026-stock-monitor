"""多市场实时行情获取脚本。

支持市场：
- A股：新浪财经实时API（免费）
- 美股：Finnhub（需FINNHUB_API_KEY，免费额度60次/分钟）/ Stooq（免费备选）
- 港股：EOD Historical Data（需EOD_API_KEY）

用法：
    python -m src.realtime                    # 从config.yaml读取
    python -m src.realtime --watchlist watchlist.md  # 从watchlist.md读取
"""

import argparse
import csv
import io
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime

import httpx


@dataclass(frozen=True)
class StockInfo:
    symbol: str
    name: str
    sector: str
    market: str       # A股 / 美股 / 港股
    cap_level: str    # 大盘 / 中盘 / 小盘


@dataclass(frozen=True)
class RealtimeQuote:
    symbol: str
    name: str
    market: str
    cap_level: str
    price: float
    change_pct: float
    is_anomaly: bool
    anomaly_reason: str


# ── 市值级别异动阈值 ──────────────────────────────────────────
ANOMALY_THRESHOLDS = {
    "大盘": 3.0,   # ±3%
    "中盘": 5.0,   # ±5%
    "小盘": 7.0,   # ±7%
}


# ── 解析 watchlist.md ─────────────────────────────────────────
def parse_watchlist_md(path: str) -> list[StockInfo]:
    """从 watchlist.md 表格中提取股票信息。"""
    stocks: list[StockInfo] = []
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    # 匹配 markdown 表格行: | 序号 | 代码 | 名称 | 行业 | 市值级别 | ...
    # 序号可以是数字或"-"（原有持仓）
    # 代码可以是 6位数字(A股) 或 字母+点号(美股，如 BRK.B)
    pattern = re.compile(
        r"\|\s*[\d-]+\s*\|\s*([A-Z0-9.]+)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(大盘|中盘|小盘)\s*\|"
    )
    for m in pattern.finditer(content):
        symbol, name, sector, cap = m.groups()
        market = _detect_market(symbol)
        stocks.append(StockInfo(
            symbol=symbol.strip(),
            name=name.strip(),
            sector=sector.strip(),
            market=market,
            cap_level=cap.strip(),
        ))
    return stocks


def _detect_market(symbol: str) -> str:
    """根据代码格式判断市场。"""
    if re.match(r"^[036]\d{5}$", symbol):
        return "A股"
    elif re.match(r"^\d{4,5}$", symbol):
        return "港股"
    elif re.match(r"^[A-Z]{1,5}(\.[A-Z])?$", symbol):
        return "美股"
    return "未知"


# ── A股：新浪财经实时API ──────────────────────────────────────
def fetch_a_stock_realtime(stocks: list[StockInfo]) -> list[RealtimeQuote]:
    """通过新浪财经API获取A股实时行情。"""
    if not stocks:
        return []

    codes = []
    for s in stocks:
        prefix = "sh" if s.symbol.startswith(("6", "9")) else "sz"
        codes.append(f"{prefix}{s.symbol}")

    url = f"https://hq.sinajs.cn/list={','.join(codes)}"
    headers = {"Referer": "https://finance.sina.com.cn"}

    try:
        resp = httpx.get(url, headers=headers, timeout=10.0)
        resp.raise_for_status()
        resp.encoding = "gbk"
    except Exception as e:
        print(f"[ERROR] 新浪API请求失败: {e}", file=sys.stderr)
        return []

    quotes = []
    lines = resp.text.strip().split("\n")
    stock_map = {s.symbol: s for s in stocks}

    for line in lines:
        m = re.match(r'var hq_str_(\w+)="(.+)"', line)
        if not m:
            continue
        full_code, data = m.groups()
        symbol = full_code[2:]  # 去掉 sh/sz 前缀
        if symbol not in stock_map:
            continue

        fields = data.split(",")
        if len(fields) < 32:
            continue

        try:
            name = fields[0]
            open_price = float(fields[1])
            prev_close = float(fields[2])
            price = float(fields[3])
            high = float(fields[4])
            low = float(fields[5])

            if prev_close == 0 or price == 0:
                continue

            change_pct = (price - prev_close) / prev_close * 100
            info = stock_map[symbol]
            threshold = ANOMALY_THRESHOLDS.get(info.cap_level, 5.0)
            is_anomaly = abs(change_pct) >= threshold

            quotes.append(RealtimeQuote(
                symbol=symbol,
                name=name,
                market="A股",
                cap_level=info.cap_level,
                price=round(price, 2),
                change_pct=round(change_pct, 2),
                is_anomaly=is_anomaly,
                anomaly_reason=f"涨跌幅{change_pct:+.2f}%超过{info.cap_level}阈值±{threshold}%"
                if is_anomaly else "",
            ))
        except (ValueError, IndexError):
            continue

    return quotes


# ── 美股：Finnhub（主）/ Stooq（备） ──────────────────────────
def fetch_us_stock_realtime(stocks: list[StockInfo]) -> list[RealtimeQuote]:
    """获取美股实时行情。优先Finnhub，无API Key时回退Stooq。"""
    if not stocks:
        return []

    api_key = os.environ.get("FINNHUB_API_KEY", "")
    if api_key:
        return _fetch_us_finnhub(stocks, api_key)
    else:
        print("[INFO] 未设置FINNHUB_API_KEY，使用Stooq获取美股数据", file=sys.stderr)
        return _fetch_us_stooq(stocks)


def _fetch_us_finnhub(stocks: list[StockInfo], api_key: str) -> list[RealtimeQuote]:
    """通过Finnhub API获取美股实时行情。"""
    quotes = []
    for s in stocks:
        url = "https://finnhub.io/api/v1/quote"
        params = {"symbol": s.symbol, "token": api_key}
        try:
            resp = httpx.get(url, params=params, timeout=10.0)
            resp.raise_for_status()
            data = resp.json()

            price = float(data.get("c", 0))        # 当前价
            prev_close = float(data.get("pc", 0))   # 前收盘价
            if prev_close == 0 or price == 0:
                continue

            change_pct = (price - prev_close) / prev_close * 100
            threshold = ANOMALY_THRESHOLDS.get(s.cap_level, 5.0)
            is_anomaly = abs(change_pct) >= threshold

            quotes.append(RealtimeQuote(
                symbol=s.symbol,
                name=s.name,
                market="美股",
                cap_level=s.cap_level,
                price=round(price, 2),
                change_pct=round(change_pct, 2),
                is_anomaly=is_anomaly,
                anomaly_reason=f"涨跌幅{change_pct:+.2f}%超过{s.cap_level}阈值±{threshold}%"
                if is_anomaly else "",
            ))
        except Exception as e:
            print(f"[WARN] Finnhub获取{s.symbol}失败: {e}", file=sys.stderr)

    return quotes


def _fetch_us_stooq(stocks: list[StockInfo]) -> list[RealtimeQuote]:
    """通过Stooq获取美股行情（备选方案）。"""
    quotes = []
    for s in stocks:
        # Stooq用短横线代替点号，如 BRK.B → brk-b
        stooq_symbol = s.symbol.lower().replace(".", "-")
        url = f"https://stooq.com/q/l/?s={stooq_symbol}&f=sd2t2ohlcv&h&e=csv"
        try:
            resp = httpx.get(url, timeout=10.0, follow_redirects=True)
            resp.raise_for_status()
            reader = csv.DictReader(io.StringIO(resp.text))
            row = next(reader, None)
            if not row:
                continue

            price = float(row.get("Close", 0))
            open_price = float(row.get("Open", 0))
            if open_price == 0:
                continue

            change_pct = (price - open_price) / open_price * 100
            threshold = ANOMALY_THRESHOLDS.get(s.cap_level, 5.0)
            is_anomaly = abs(change_pct) >= threshold

            quotes.append(RealtimeQuote(
                symbol=s.symbol,
                name=s.name,
                market="美股",
                cap_level=s.cap_level,
                price=round(price, 2),
                change_pct=round(change_pct, 2),
                is_anomaly=is_anomaly,
                anomaly_reason=f"涨跌幅{change_pct:+.2f}%超过{s.cap_level}阈值±{threshold}%"
                if is_anomaly else "",
            ))
        except Exception as e:
            print(f"[WARN] Stooq获取{s.symbol}失败: {e}", file=sys.stderr)

    return quotes


# ── 港股：EOD Historical Data ─────────────────────────────────
def fetch_hk_stock_realtime(stocks: list[StockInfo]) -> list[RealtimeQuote]:
    """通过EOD API获取港股实时行情。"""
    api_key = os.environ.get("EOD_API_KEY", "")
    if not api_key:
        print("[WARN] 未设置EOD_API_KEY，跳过港股", file=sys.stderr)
        return []

    if not stocks:
        return []

    quotes = []
    for s in stocks:
        url = f"https://eodhistoricaldata.com/api/real-time/{s.symbol}.HK"
        params = {"api_token": api_key, "fmt": "json"}
        try:
            resp = httpx.get(url, params=params, timeout=10.0)
            resp.raise_for_status()
            data = resp.json()

            price = float(data.get("close", 0))
            prev_close = float(data.get("prevClose", 0))
            if prev_close == 0 or price == 0:
                continue

            change_pct = (price - prev_close) / prev_close * 100
            threshold = ANOMALY_THRESHOLDS.get(s.cap_level, 5.0)
            is_anomaly = abs(change_pct) >= threshold

            quotes.append(RealtimeQuote(
                symbol=s.symbol,
                name=s.name,
                market="港股",
                cap_level=s.cap_level,
                price=round(price, 2),
                change_pct=round(change_pct, 2),
                is_anomaly=is_anomaly,
                anomaly_reason=f"涨跌幅{change_pct:+.2f}%超过{s.cap_level}阈值±{threshold}%"
                if is_anomaly else "",
            ))
        except Exception as e:
            print(f"[WARN] EOD获取{s.symbol}失败: {e}", file=sys.stderr)

    return quotes


# ── 主流程 ────────────────────────────────────────────────────
def fetch_all_realtime(stocks: list[StockInfo]) -> list[RealtimeQuote]:
    """按市场分组，批量获取实时行情。"""
    a_stocks = [s for s in stocks if s.market == "A股"]
    us_stocks = [s for s in stocks if s.market == "美股"]
    hk_stocks = [s for s in stocks if s.market == "港股"]

    quotes: list[RealtimeQuote] = []
    quotes.extend(fetch_a_stock_realtime(a_stocks))
    quotes.extend(fetch_us_stock_realtime(us_stocks))
    quotes.extend(fetch_hk_stock_realtime(hk_stocks))

    return sorted(quotes, key=lambda q: abs(q.change_pct), reverse=True)


def format_report(quotes: list[RealtimeQuote]) -> str:
    """格式化输出报告。"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    anomalies = [q for q in quotes if q.is_anomaly]
    normal = [q for q in quotes if not q.is_anomaly]

    lines = [
        f"# 实时行情监控 -- {now}",
        "",
        f"共监控 {len(quotes)} 只股票，{len(anomalies)} 只触发异动",
        "",
    ]

    if anomalies:
        lines.append("## 异动信号")
        lines.append("")
        lines.append("| 股票 | 代码 | 市场 | 市值 | 现价 | 涨跌幅 | 原因 |")
        lines.append("|------|------|------|------|------|--------|------|")
        for q in anomalies:
            arrow = "↑" if q.change_pct > 0 else "↓"
            lines.append(
                f"| {q.name} | {q.symbol} | {q.market} | {q.cap_level} "
                f"| {q.price} | {arrow}{q.change_pct:+.2f}% | {q.anomaly_reason} |"
            )
        lines.append("")

    # 正常股票只显示涨跌幅 Top5
    if normal:
        top_gainers = sorted(normal, key=lambda q: q.change_pct, reverse=True)[:5]
        top_losers = sorted(normal, key=lambda q: q.change_pct)[:5]

        lines.append("## 涨幅前5")
        lines.append("")
        lines.append("| 股票 | 代码 | 市值 | 现价 | 涨跌幅 |")
        lines.append("|------|------|------|------|--------|")
        for q in top_gainers:
            lines.append(
                f"| {q.name} | {q.symbol} | {q.cap_level} "
                f"| {q.price} | ↑{q.change_pct:+.2f}% |"
            )
        lines.append("")

        lines.append("## 跌幅前5")
        lines.append("")
        lines.append("| 股票 | 代码 | 市值 | 现价 | 涨跌幅 |")
        lines.append("|------|------|------|------|--------|")
        for q in top_losers:
            lines.append(
                f"| {q.name} | {q.symbol} | {q.cap_level} "
                f"| {q.price} | ↓{q.change_pct:+.2f}% |"
            )
        lines.append("")

    return "\n".join(lines)


# ── CLI ───────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="多市场实时行情监控")
    parser.add_argument(
        "--watchlist", default="watchlist.md",
        help="watchlist文件路径 (默认: watchlist.md)",
    )
    parser.add_argument(
        "--config", default="config.yaml",
        help="config文件路径，仅在watchlist.md不存在时使用",
    )
    parser.add_argument(
        "--output", default=None,
        help="输出文件路径 (默认: 输出到终端)",
    )
    args = parser.parse_args()

    if os.path.exists(args.watchlist):
        print(f"从 {args.watchlist} 读取自选股...", file=sys.stderr)
        stocks = parse_watchlist_md(args.watchlist)
    else:
        print(f"{args.watchlist} 不存在，从 {args.config} 读取...", file=sys.stderr)
        from src.config import get_watchlist, load_config
        config = load_config(args.config)
        raw = get_watchlist(config)
        stocks = [
            StockInfo(
                symbol=s.symbol, name=s.name, sector=s.sector,
                market=_detect_market(s.symbol), cap_level="中盘",
            )
            for s in raw
        ]

    print(f"共 {len(stocks)} 只股票，开始获取实时行情...", file=sys.stderr)

    quotes = fetch_all_realtime(stocks)
    report = format_report(quotes)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"报告已保存到 {args.output}", file=sys.stderr)
    else:
        print(report)


if __name__ == "__main__":
    main()
