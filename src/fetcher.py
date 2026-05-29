"""多市场数据获取模块。

数据源:
- A股: AKShare (主要)
- 美股: Finnhub (主要，需FINNHUB_API_KEY) + Stooq (备用)
- 期货: TqSdk (需天勤账号)
- 本地历史: output/us_quote_history.json 累积每日数据用于异动检测

用法:
    quotes = fetch_daily_quotes(symbols, days=30, market_map={"AAPL": "美股", "000001": "A股"})
"""

import csv
import io
import json
import logging
import os
import time
from datetime import date, timedelta
from pathlib import Path

import akshare as ak
import httpx
import pandas as pd

from src.config import MARKET_A_SHARE, MARKET_FUTURES, MARKET_US, detect_market
from src.models import DailyQuote

logger = logging.getLogger(__name__)

# Finnhub API (美股主要数据源)
FINNHUB_QUOTE_URL = "https://finnhub.io/api/v1/quote"
FINNHUB_REQUEST_TIMEOUT = 10.0

# Stooq实时行情端点 (美股备用，无需API Key)
STOOQ_LATEST_URL = "https://stooq.com/q/l/"
STOOQ_REQUEST_TIMEOUT = 15.0

# 请求间隔 (秒)，避免被数据源限流
REQUEST_DELAY = 1.0

# 本地美股历史数据文件
DEFAULT_HISTORY_DIR = "output"
HISTORY_FILENAME = "us_quote_history.json"

# 期货历史数据文件
FUTURES_HISTORY_FILENAME = "futures_quote_history.json"


def fetch_daily_quotes(
    symbols: list[str],
    days: int = 30,
    market_map: dict[str, str] | None = None,
    output_dir: str = DEFAULT_HISTORY_DIR,
    futures_config: dict | None = None,
) -> dict[str, list[DailyQuote]]:
    """获取多市场历史日线数据。

    Args:
        symbols: 股票代码列表
        days: 获取天数
        market_map: 股票代码到市场的映射，未提供则自动检测
        output_dir: 输出目录（用于本地历史文件）
        futures_config: 期货配置 (tqsdk用户名/密码)

    Returns:
        字典，key为股票代码，value为DailyQuote列表（按日期升序）
    """
    end_date = date.today()
    start_date = end_date - timedelta(days=days + 10)

    if market_map is None:
        market_map = {s: detect_market(s) for s in symbols}

    # 按市场分组
    a_share_symbols = []
    us_symbols = []
    futures_symbols = []
    for symbol in symbols:
        market = market_map.get(symbol, detect_market(symbol))
        if market == MARKET_A_SHARE:
            a_share_symbols.append(symbol)
        elif market == MARKET_FUTURES:
            futures_symbols.append(symbol)
        else:
            us_symbols.append(symbol)

    result = {}

    # 加载本地历史数据
    us_history_path = str(Path(output_dir) / HISTORY_FILENAME)
    us_history = _load_local_history(us_history_path)

    futures_history_path = str(Path(output_dir) / FUTURES_HISTORY_FILENAME)
    futures_history = _load_local_history(futures_history_path)

    # 获取A股数据 (AKShare)
    if a_share_symbols:
        a_share_data = _fetch_a_share_batch(a_share_symbols, start_date, end_date)
        result.update(a_share_data)

    # 获取美股数据 (Finnhub主 + Stooq备)
    for i, symbol in enumerate(us_symbols):
        if i > 0:
            time.sleep(REQUEST_DELAY)

        try:
            quotes = _fetch_us_symbol(symbol, start_date, end_date, us_history)
            if quotes:
                result[symbol] = quotes
            else:
                logger.warning(f"No data returned for {symbol} (美股)")
        except Exception as e:
            logger.error(f"Failed to fetch data for {symbol} (美股): {e}")

    # 保存更新后的美股历史
    _save_local_history(us_history_path, us_history)

    # 获取期货数据 (TqSdk)
    if futures_symbols:
        futures_data = _fetch_futures_batch(
            futures_symbols, start_date, end_date,
            futures_history, futures_config,
        )
        result.update(futures_data)
        _save_local_history(futures_history_path, futures_history)

    return result


# ── A股: AKShare ──────────────────────────────────────────────
AKSHARE_MAX_RETRIES = 3
AKSHARE_RETRY_DELAY = 5.0


def _fetch_a_share_batch(
    symbols: list[str], start_date: date, end_date: date
) -> dict[str, list[DailyQuote]]:
    """逐个获取A股历史数据，使用AKShare stock_zh_a_hist。"""
    result = {}

    for i, symbol in enumerate(symbols):
        if i > 0:
            time.sleep(REQUEST_DELAY)

        try:
            quotes = _fetch_a_share_symbol(symbol, start_date, end_date)
            if quotes:
                result[symbol] = quotes
        except Exception as e:
            logger.error(f"Failed to fetch A-share data for {symbol}: {e}")

    return result


def _fetch_a_share_symbol(
    symbol: str, start_date: date, end_date: date
) -> list[DailyQuote]:
    """通过AKShare获取单只A股历史数据，带重试逻辑。"""
    for attempt in range(AKSHARE_MAX_RETRIES):
        try:
            df = ak.stock_zh_a_hist(
                symbol=symbol,
                period="daily",
                start_date=start_date.strftime("%Y%m%d"),
                end_date=end_date.strftime("%Y%m%d"),
                adjust="qfq",
            )

            if df is None or df.empty:
                logger.warning(f"Empty data for A-share {symbol}")
                return []

            return _normalize_akshare_df(df, symbol)

        except Exception as e:
            error_msg = str(e).lower()
            if "rate" in error_msg or "limit" in error_msg or "frequency" in error_msg:
                if attempt < AKSHARE_MAX_RETRIES - 1:
                    wait_time = AKSHARE_RETRY_DELAY * (attempt + 1)
                    logger.warning(
                        f"AKShare rate limited for {symbol}, "
                        f"retrying in {wait_time}s (attempt {attempt + 1}/{AKSHARE_MAX_RETRIES})"
                    )
                    time.sleep(wait_time)
                else:
                    logger.error(f"AKShare rate limited for {symbol}, max retries exceeded")
            else:
                logger.error(f"Failed to fetch A-share {symbol}: {e}")
                break

    return []


def _normalize_akshare_df(df: pd.DataFrame, symbol: str) -> list[DailyQuote]:
    """将AKShare DataFrame转换为DailyQuote列表。

    AKShare返回中文列名: 日期, 开盘, 收盘, 最高, 最低, 成交量, 成交额, 涨跌幅
    """
    quotes = []
    for _, row in df.iterrows():
        try:
            quote_date = _parse_akshare_date(str(row["日期"]))
            open_price = float(row["开盘"])
            close_price = float(row["收盘"])
            high_price = float(row["最高"])
            low_price = float(row["最低"])
            volume_val = int(row["成交量"])
            turnover_val = float(row["成交额"])
            change_pct = float(row["涨跌幅"])

            quotes.append(DailyQuote(
                symbol=symbol,
                date=quote_date,
                open=open_price,
                close=close_price,
                high=high_price,
                low=low_price,
                volume=volume_val,
                turnover=turnover_val,
                change_pct=round(change_pct, 4),
            ))
        except (ValueError, TypeError, KeyError) as e:
            logger.warning(f"Failed to parse AKShare row for {symbol}: {e}")

    return sorted(quotes, key=lambda q: q.date)


def _parse_akshare_date(date_str: str) -> date:
    """解析AKShare日期格式 (YYYY-MM-DD)。"""
    try:
        return date.fromisoformat(date_str[:10])
    except ValueError:
        return date.today()


# ── 美股: Finnhub (主) + Stooq (备) + 本地历史 ────────────────
def _fetch_us_symbol(
    symbol: str,
    start_date: date,
    end_date: date,
    history: dict[str, list[dict]],
) -> list[DailyQuote]:
    """获取美股数据。Finnhub为主，Stooq为备，合并本地历史。"""
    # Step 1: 获取今日行情 (Finnhub优先)
    today_quote = _fetch_us_finnhub(symbol)
    if not today_quote:
        logger.info(f"Finnhub failed for {symbol}, trying Stooq...")
        today_quote = _fetch_us_stooq_latest(symbol)

    if today_quote:
        _update_history(history, symbol, today_quote[0])

    # Step 2: 从本地历史获取足够数据
    historical = _get_history_quotes(symbol, start_date, end_date, history)

    if historical:
        return historical

    # Step 3: 如果没有历史数据，至少返回今日行情
    if today_quote:
        return today_quote

    logger.warning(f"All US data sources failed for {symbol}")
    return []


def _fetch_us_finnhub(symbol: str) -> list[DailyQuote]:
    """通过Finnhub获取美股最新行情（主要来源，需FINNHUB_API_KEY）。"""
    api_key = os.environ.get("FINNHUB_API_KEY", "")
    if not api_key:
        logger.warning("FINNHUB_API_KEY not set, skipping Finnhub")
        return []

    try:
        resp = httpx.get(
            FINNHUB_QUOTE_URL,
            params={"symbol": symbol, "token": api_key},
            timeout=FINNHUB_REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()

        current = float(data.get("c", 0))
        if current == 0:
            return []

        # Finnhub提供精确的涨跌幅
        change_pct = float(data.get("dp", 0))

        return [
            DailyQuote(
                symbol=symbol,
                date=date.today(),
                open=float(data.get("o", 0)),
                close=current,
                high=float(data.get("h", 0)),
                low=float(data.get("l", 0)),
                volume=0,  # Finnhub quote不提供成交量
                turnover=0.0,
                change_pct=round(change_pct, 4),
            )
        ]
    except Exception as e:
        logger.warning(f"Finnhub fetch failed for {symbol}: {e}")
        return []


def _fetch_us_stooq_latest(symbol: str) -> list[DailyQuote]:
    """通过Stooq获取美股最新行情（备用来源，无需API Key）。"""
    try:
        stooq_symbol = f"{symbol.lower().replace('.', '-')}.us"
        params = {
            "s": stooq_symbol,
            "f": "sd2t2ohlcv",
            "h": "",
            "e": "csv",
        }
        resp = httpx.get(
            STOOQ_LATEST_URL,
            params=params,
            timeout=STOOQ_REQUEST_TIMEOUT,
            follow_redirects=True,
        )
        resp.raise_for_status()

        reader = csv.DictReader(io.StringIO(resp.text))
        row = next(reader, None)
        if not row:
            return []

        close_str = row.get("Close", "N/D")
        if close_str == "N/D":
            return []

        close_price = float(close_str)
        open_price = float(row.get("Open", 0))
        high_price = float(row.get("High", 0))
        low_price = float(row.get("Low", 0))
        volume_val = int(float(row.get("Volume", 0)))
        quote_date = _parse_stooq_date(row.get("Date", ""))

        change_pct = 0.0
        if open_price > 0:
            change_pct = (close_price - open_price) / open_price * 100

        return [
            DailyQuote(
                symbol=symbol,
                date=quote_date,
                open=open_price,
                close=close_price,
                high=high_price,
                low=low_price,
                volume=volume_val,
                turnover=0.0,
                change_pct=round(change_pct, 4),
            )
        ]
    except Exception as e:
        logger.warning(f"Stooq fetch failed for {symbol}: {e}")
        return []


# ── 期货: TqSdk ───────────────────────────────────────────────
def _fetch_futures_batch(
    symbols: list[str],
    start_date: date,
    end_date: date,
    history: dict[str, list[dict]],
    futures_config: dict | None = None,
) -> dict[str, list[DailyQuote]]:
    """批量获取期货日线数据，使用TqSdk。

    Args:
        symbols: 期货合约代码列表 (如 "SHFE.cu2501")
        start_date: 开始日期
        end_date: 结束日期
        history: 本地历史数据
        futures_config: 期货配置 (tqsdk用户名/密码)

    Returns:
        字典，key为合约代码，value为DailyQuote列表
    """
    if not futures_config:
        logger.warning("No futures config provided, skipping futures data")
        return {}

    username = futures_config.get("username", "")
    password = futures_config.get("password", "")
    if not username or not password:
        logger.warning("TqSdk username/password not configured, skipping futures")
        return {}

    result = {}

    try:
        from tqsdk import TqApi, TqAuth

        api = TqApi(auth=TqAuth(username, password))

        for symbol in symbols:
            try:
                # 计算需要获取的K线根数
                days_count = (end_date - start_date).days + 10

                # 获取日K线数据 (86400秒 = 1天)
                klines = api.get_kline_serial(symbol, 86400, days_count)

                if klines is not None and not klines.empty:
                    quotes = _normalize_tqsdk_df(klines, symbol)
                    if quotes:
                        result[symbol] = quotes
                        # 更新本地历史
                        for q in quotes:
                            _update_history(history, symbol, q)
                else:
                    logger.warning(f"No futures data returned for {symbol}")

            except Exception as e:
                logger.error(f"Failed to fetch futures data for {symbol}: {e}")

        api.close()

    except ImportError:
        logger.error("tqsdk not installed. Install with: pip install tqsdk")
    except Exception as e:
        logger.error(f"TqSdk connection failed: {e}")

    return result


def _normalize_tqsdk_df(df: pd.DataFrame, symbol: str) -> list[DailyQuote]:
    """将TqSdk K线DataFrame转换为DailyQuote列表。

    TqSdk返回列: datetime, open, high, low, close, volume, open_oi, close_oi
    datetime为毫秒时间戳。
    """
    quotes = []
    for _, row in df.iterrows():
        try:
            # datetime为纳秒时间戳 (pandas Timestamp)
            ts = row.get("datetime")
            if ts is None:
                continue

            # TqSdk的datetime是纳秒级时间戳
            if isinstance(ts, (int, float)):
                quote_date = pd.Timestamp(ts, unit="ns").date()
            else:
                quote_date = pd.Timestamp(ts).date()

            open_price = float(row.get("open", 0))
            close_price = float(row.get("close", 0))
            high_price = float(row.get("high", 0))
            low_price = float(row.get("low", 0))
            volume_val = int(row.get("volume", 0))

            if close_price == 0:
                continue

            # 计算涨跌幅 (近似，基于开盘价)
            change_pct = 0.0
            if open_price > 0:
                change_pct = (close_price - open_price) / open_price * 100

            quotes.append(DailyQuote(
                symbol=symbol,
                date=quote_date,
                open=open_price,
                close=close_price,
                high=high_price,
                low=low_price,
                volume=volume_val,
                turnover=0.0,
                change_pct=round(change_pct, 4),
            ))
        except (ValueError, TypeError, KeyError) as e:
            logger.warning(f"Failed to parse TqSdk row for {symbol}: {e}")

    return sorted(quotes, key=lambda q: q.date)


# ── 本地历史数据管理 ───────────────────────────────────────────
def _load_local_history(path: str) -> dict[str, list[dict]]:
    """加载本地历史数据。"""
    history_path = Path(path)
    if not history_path.exists():
        return {}
    try:
        with open(history_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        logger.warning(f"Failed to load history from {path}: {e}")
        return {}


def _save_local_history(path: str, history: dict[str, list[dict]]) -> None:
    """保存历史数据到本地文件。"""
    history_path = Path(path)
    history_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(history_path, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
    except IOError as e:
        logger.warning(f"Failed to save history to {path}: {e}")


def _update_history(
    history: dict[str, list[dict]], symbol: str, quote: DailyQuote
) -> None:
    """将最新行情更新到历史记录中（按日期去重）。"""
    if symbol not in history:
        history[symbol] = []

    date_str = quote.date.isoformat()

    # 检查是否已有该日期的数据
    existing_dates = {entry["date"] for entry in history[symbol]}
    if date_str in existing_dates:
        return

    history[symbol].append({
        "date": date_str,
        "open": quote.open,
        "high": quote.high,
        "low": quote.low,
        "close": quote.close,
        "volume": quote.volume,
        "change_pct": quote.change_pct,
    })

    # 保留最近90天数据
    history[symbol] = sorted(history[symbol], key=lambda x: x["date"])[-90:]


def _get_history_quotes(
    symbol: str,
    start_date: date,
    end_date: date,
    history: dict[str, list[dict]],
) -> list[DailyQuote]:
    """从本地历史中提取指定日期范围的行情数据。"""
    if symbol not in history:
        return []

    quotes = []
    for entry in history[symbol]:
        try:
            entry_date = date.fromisoformat(entry["date"])
            if start_date <= entry_date <= end_date:
                quotes.append(DailyQuote(
                    symbol=symbol,
                    date=entry_date,
                    open=float(entry.get("open", 0)),
                    close=float(entry.get("close", 0)),
                    high=float(entry.get("high", 0)),
                    low=float(entry.get("low", 0)),
                    volume=int(entry.get("volume", 0)),
                    turnover=0.0,
                    change_pct=float(entry.get("change_pct", 0)),
                ))
        except (ValueError, KeyError) as e:
            logger.warning(f"Failed to parse history entry for {symbol}: {e}")

    return sorted(quotes, key=lambda q: q.date)


# ── 日期解析 ──────────────────────────────────────────────────
def _parse_stooq_date(date_str: str) -> date:
    """解析Stooq日期格式 (YYYY-MM-DD)。"""
    if not date_str or date_str == "N/D":
        return date.today()
    try:
        return date.fromisoformat(date_str)
    except ValueError:
        return date.today()
