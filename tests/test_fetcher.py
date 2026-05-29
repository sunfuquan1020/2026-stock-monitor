"""Tests for multi-market data fetching."""

import json
import os
import tempfile
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from src.config import MARKET_A_SHARE, MARKET_FUTURES, MARKET_US
from src.fetcher import (
    _get_history_quotes,
    _load_local_history,
    _normalize_akshare_df,
    _normalize_tqsdk_df,
    _parse_akshare_date,
    _parse_stooq_date,
    _save_local_history,
    _update_history,
    fetch_daily_quotes,
)
from src.models import DailyQuote


def make_akshare_df(rows: int = 5) -> pd.DataFrame:
    """构造AKShare返回格式的DataFrame。"""
    base_date = date(2026, 5, 15)
    data = []
    for i in range(rows):
        d = base_date + timedelta(days=i)
        data.append({
            "日期": d.strftime("%Y-%m-%d"),
            "股票代码": "000001",
            "开盘": 10.0 + i * 0.1,
            "收盘": 10.1 + i * 0.1,
            "最高": 10.5 + i * 0.1,
            "最低": 9.5 + i * 0.1,
            "成交量": 1000000 + i * 10000,
            "成交额": 1e8 + i * 1e6,
            "振幅": 1.0,
            "涨跌幅": 0.5 + i * 0.1,
            "涨跌额": 0.05,
            "换手率": 0.5,
        })
    return pd.DataFrame(data)


def make_tqsdk_df(rows: int = 5) -> pd.DataFrame:
    """构造TqSdk K线返回格式的DataFrame。"""
    base_date = pd.Timestamp("2026-05-15")
    data = []
    for i in range(rows):
        ts = base_date + timedelta(days=i)
        data.append({
            "datetime": ts.value,  # 纳秒时间戳
            "open": 100.0 + i * 1.0,
            "high": 105.0 + i * 1.0,
            "low": 95.0 + i * 1.0,
            "close": 101.0 + i * 1.0,
            "volume": 50000 + i * 1000,
            "open_oi": 100000,
            "close_oi": 100000,
        })
    return pd.DataFrame(data)


def make_us_quote(
    symbol: str = "AAPL",
    d: date = date(2026, 5, 22),
    close: float = 308.82,
    open_: float = 306.12,
    change_pct: float = 1.26,
) -> DailyQuote:
    return DailyQuote(
        symbol=symbol,
        date=d,
        open=open_,
        close=close,
        high=close + 3,
        low=close - 3,
        volume=43608864,
        turnover=0.0,
        change_pct=change_pct,
    )


class TestNormalizeAkshareDf:
    def test_normalizes_a_share_data(self):
        df = make_akshare_df(3)
        quotes = _normalize_akshare_df(df, "000001")
        assert len(quotes) == 3
        assert quotes[0].symbol == "000001"
        assert quotes[0].date == date(2026, 5, 15)
        assert quotes[0].open == pytest.approx(10.0)
        assert quotes[0].close == pytest.approx(10.1)

    def test_sorted_by_date(self):
        df = make_akshare_df(5)
        df = df.iloc[::-1].reset_index(drop=True)
        quotes = _normalize_akshare_df(df, "000001")
        dates = [q.date for q in quotes]
        assert dates == sorted(dates)

    def test_empty_df(self):
        df = pd.DataFrame()
        quotes = _normalize_akshare_df(df, "000001")
        assert quotes == []

    def test_turnover_from_data(self):
        df = make_akshare_df(1)
        quotes = _normalize_akshare_df(df, "000001")
        assert quotes[0].turnover == pytest.approx(1e8)

    def test_change_pct_from_data(self):
        df = make_akshare_df(1)
        quotes = _normalize_akshare_df(df, "000001")
        assert quotes[0].change_pct == pytest.approx(0.5)


class TestNormalizeTqsdkDf:
    def test_normalizes_futures_data(self):
        df = make_tqsdk_df(3)
        quotes = _normalize_tqsdk_df(df, "SHFE.cu2507")
        assert len(quotes) == 3
        assert quotes[0].symbol == "SHFE.cu2507"
        assert quotes[0].date == date(2026, 5, 15)
        assert quotes[0].open == pytest.approx(100.0)
        assert quotes[0].close == pytest.approx(101.0)

    def test_sorted_by_date(self):
        df = make_tqsdk_df(5)
        df = df.iloc[::-1].reset_index(drop=True)
        quotes = _normalize_tqsdk_df(df, "SHFE.cu2507")
        dates = [q.date for q in quotes]
        assert dates == sorted(dates)

    def test_empty_df(self):
        df = pd.DataFrame()
        quotes = _normalize_tqsdk_df(df, "SHFE.cu2507")
        assert quotes == []

    def test_skips_zero_close(self):
        df = make_tqsdk_df(2)
        df.loc[0, "close"] = 0
        quotes = _normalize_tqsdk_df(df, "SHFE.cu2507")
        assert len(quotes) == 1


class TestParseAkshareDate:
    def test_iso_string(self):
        assert _parse_akshare_date("2026-05-20") == date(2026, 5, 20)

    def test_iso_with_time(self):
        assert _parse_akshare_date("2026-05-20 10:30:00") == date(2026, 5, 20)

    def test_invalid_returns_today(self):
        assert _parse_akshare_date("invalid") == date.today()


class TestParseStooqDate:
    def test_valid_date(self):
        assert _parse_stooq_date("2026-05-22") == date(2026, 5, 22)

    def test_not_available(self):
        assert _parse_stooq_date("N/D") == date.today()

    def test_empty_string(self):
        assert _parse_stooq_date("") == date.today()


class TestLocalHistory:
    def test_save_and_load(self, tmp_path):
        path = str(tmp_path / "history.json")
        history = {"AAPL": [{"date": "2026-05-22", "open": 306, "close": 308, "high": 311, "low": 305, "volume": 43000000, "change_pct": 1.0}]}
        _save_local_history(path, history)
        loaded = _load_local_history(path)
        assert loaded == history

    def test_load_nonexistent(self, tmp_path):
        path = str(tmp_path / "nonexistent.json")
        assert _load_local_history(path) == {}

    def test_update_history_adds_new_date(self):
        history: dict[str, list[dict]] = {}
        quote = make_us_quote(d=date(2026, 5, 22))
        _update_history(history, "AAPL", quote)
        assert len(history["AAPL"]) == 1
        assert history["AAPL"][0]["date"] == "2026-05-22"

    def test_update_history_deduplicates(self):
        history: dict[str, list[dict]] = {}
        quote = make_us_quote(d=date(2026, 5, 22))
        _update_history(history, "AAPL", quote)
        _update_history(history, "AAPL", quote)
        assert len(history["AAPL"]) == 1

    def test_update_history_keeps_90_days(self):
        history: dict[str, list[dict]] = {}
        for i in range(100):
            d = date(2026, 1, 1) + timedelta(days=i)
            quote = make_us_quote(d=d)
            _update_history(history, "AAPL", quote)
        assert len(history["AAPL"]) == 90

    def test_get_history_quotes_filters_date_range(self):
        history = {"AAPL": [
            {"date": "2026-05-01", "open": 300, "close": 305, "high": 310, "low": 295, "volume": 40000000, "change_pct": 1.0},
            {"date": "2026-05-15", "open": 306, "close": 308, "high": 311, "low": 305, "volume": 43000000, "change_pct": 0.5},
            {"date": "2026-05-22", "open": 306, "close": 308, "high": 311, "low": 305, "volume": 43000000, "change_pct": 1.0},
        ]}
        quotes = _get_history_quotes("AAPL", date(2026, 5, 10), date(2026, 5, 25), history)
        assert len(quotes) == 2
        assert quotes[0].date == date(2026, 5, 15)

    def test_get_history_quotes_empty(self):
        assert _get_history_quotes("AAPL", date(2026, 5, 1), date(2026, 5, 22), {}) == []


class TestFetchDailyQuotes:
    @patch("src.fetcher.ak")
    def test_a_share_calls_akshare(self, mock_ak):
        mock_ak.stock_zh_a_hist.return_value = make_akshare_df(5)
        result = fetch_daily_quotes(
            ["000001"], days=30,
            market_map={"000001": "A股"}, output_dir="/tmp",
        )
        assert "000001" in result
        assert len(result["000001"]) == 5
        mock_ak.stock_zh_a_hist.assert_called_once()

    @patch("src.fetcher.httpx")
    def test_us_stock_finnhub_primary(self, mock_httpx, tmp_path):
        # Finnhub returns data
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"c": 308.82, "o": 306.12, "h": 311.4, "l": 305.84, "pc": 304.99, "dp": 1.26}
        mock_resp.raise_for_status = MagicMock()
        mock_httpx.get.return_value = mock_resp

        with patch.dict("os.environ", {"FINNHUB_API_KEY": "test-key"}):
            result = fetch_daily_quotes(
                ["AAPL"], days=30,
                market_map={"AAPL": "美股"}, output_dir=str(tmp_path),
            )
            assert "AAPL" in result
            assert result["AAPL"][0].close == pytest.approx(308.82)

    @patch("src.fetcher.httpx")
    def test_us_stock_stooq_fallback(self, mock_httpx, tmp_path):
        # Finnhub fails (no API key), Stooq works
        mock_resp = MagicMock()
        mock_resp.text = "Symbol,Date,Time,Open,High,Low,Close,Volume\nAAPL.US,2026-05-22,22:00:19,306.12,311.4,305.84,308.82,43608864"
        mock_resp.raise_for_status = MagicMock()
        mock_httpx.get.return_value = mock_resp

        # No FINNHUB_API_KEY set
        with patch.dict("os.environ", {}, clear=True):
            env = os.environ.copy()
            env.pop("FINNHUB_API_KEY", None)
            with patch("os.environ", env):
                result = fetch_daily_quotes(
                    ["AAPL"], days=30,
                    market_map={"AAPL": "美股"}, output_dir=str(tmp_path),
                )
                assert "AAPL" in result

    @patch("src.fetcher.httpx")
    def test_us_stock_history_merges(self, mock_httpx, tmp_path):
        # Pre-populate history
        history_path = tmp_path / "us_quote_history.json"
        history = {"AAPL": [
            {"date": "2026-05-20", "open": 300, "close": 302, "high": 305, "low": 298, "volume": 40000000, "change_pct": 0.5},
            {"date": "2026-05-21", "open": 302, "close": 305, "high": 308, "low": 300, "volume": 42000000, "change_pct": 1.0},
        ]}
        history_path.write_text(json.dumps(history))

        # Finnhub returns today
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"c": 308.82, "o": 306.12, "h": 311.4, "l": 305.84, "pc": 304.99, "dp": 1.26}
        mock_resp.raise_for_status = MagicMock()
        mock_httpx.get.return_value = mock_resp

        with patch.dict("os.environ", {"FINNHUB_API_KEY": "test-key"}):
            result = fetch_daily_quotes(
                ["AAPL"], days=30,
                market_map={"AAPL": "美股"}, output_dir=str(tmp_path),
            )
            # Should have 3 days: 2 from history + 1 from Finnhub
            assert len(result["AAPL"]) == 3

    def test_empty_when_all_fail(self, tmp_path):
        with patch("src.fetcher.ak") as mock_ak:
            mock_ak.stock_zh_a_hist.return_value = pd.DataFrame()
            result = fetch_daily_quotes(
                ["000001"], days=30,
                market_map={"000001": "A股"}, output_dir=str(tmp_path),
            )
            assert result == {}

    @patch("src.fetcher.ak")
    def test_auto_detects_market(self, mock_ak, tmp_path):
        mock_ak.stock_zh_a_hist.return_value = make_akshare_df(3)
        result = fetch_daily_quotes(["600519"], days=30, output_dir=str(tmp_path))
        assert "600519" in result
        mock_ak.stock_zh_a_hist.assert_called_once()

    def test_futures_skipped_without_config(self, tmp_path):
        result = fetch_daily_quotes(
            ["SHFE.cu2507"], days=30,
            market_map={"SHFE.cu2507": "期货"}, output_dir=str(tmp_path),
        )
        assert result == {}

    def test_futures_skipped_without_credentials(self, tmp_path):
        result = fetch_daily_quotes(
            ["SHFE.cu2507"], days=30,
            market_map={"SHFE.cu2507": "期货"}, output_dir=str(tmp_path),
            futures_config={"username": "", "password": ""},
        )
        assert result == {}
