"""realtime.py 美股备用源: 腾讯替代已失效的 Stooq(不打网络)。"""

from unittest.mock import MagicMock, patch

import pytest

from src import realtime
from tests.test_fetcher import _tencent_us_line


def _stock(symbol="AAPL"):
    return realtime.StockInfo(symbol=symbol, name="苹果", sector="消费电子", market="美股", cap_level="大盘")


@patch("src.fetcher.httpx")
def test_tencent_fallback_change_pct_uses_previous_close(mock_httpx):
    mock_resp = MagicMock()
    mock_resp.content = _tencent_us_line().encode("gbk")
    mock_httpx.get.return_value = mock_resp

    quotes = realtime._fetch_us_tencent([_stock()])

    assert quotes[0].change_pct == pytest.approx(1.53, abs=0.01)
    assert quotes[0].price == pytest.approx(341.07)


@patch("src.fetcher.httpx")
def test_tencent_fallback_skips_unparseable_symbol(mock_httpx):
    mock_resp = MagicMock()
    mock_resp.content = b'v_pv_none_match="1";'
    mock_httpx.get.return_value = mock_resp

    assert realtime._fetch_us_tencent([_stock("ZZZZ")]) == []
