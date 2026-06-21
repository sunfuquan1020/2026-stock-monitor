"""Tests for config loading."""

import pytest
import tempfile
import os

from src.config import (
    detect_market,
    get_hypotheses,
    get_thresholds,
    get_watchlist,
    load_config,
    MARKET_A_SHARE,
    MARKET_US,
    MARKET_HK,
)
from src.models import HypothesisStatus


VALID_CONFIG = """
watchlist:
  - symbol: "000001"
    name: "平安银行"
    sector: "金融"
    market: "A股"
  - symbol: "600519"
    name: "贵州茅台"

thresholds:
  price_change_pct: 5.0
  volume_spike_ratio: 2.5
  consecutive_days: 3

claude:
  model: "claude-sonnet-4-20250514"
  max_tokens: 1024

hypotheses:
  - id: "h1"
    text: "银行板块估值修复"
    related_symbols: ["000001"]
    active: true
  - id: "h2"
    text: "已失效的假设"
    related_symbols: ["600519"]
    active: false
"""


MULTI_MARKET_CONFIG = """
watchlist:
  - symbol: "000001"
    name: "平安银行"
    sector: "银行"
    market: "A股"
  - symbol: "NVDA"
    name: "英伟达"
    sector: "半导体"
    market: "美股"
  - symbol: "AAPL"
    name: "苹果"
    sector: "消费电子"
    market: "美股"
  - symbol: "600519"
    name: "贵州茅台"
    sector: "白酒"
    market: "A股"

thresholds:
  price_change_pct: 5.0

claude:
  model: "test"
  max_tokens: 512
"""


@pytest.fixture
def config_file():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(VALID_CONFIG)
        f.flush()
        yield f.name
    os.unlink(f.name)


@pytest.fixture
def multi_market_config_file():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(MULTI_MARKET_CONFIG)
        f.flush()
        yield f.name
    os.unlink(f.name)


class TestLoadConfig:
    def test_load_valid_config(self, config_file):
        config = load_config(config_file)
        assert "watchlist" in config
        assert "thresholds" in config
        assert "claude" in config

    def test_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            load_config("/nonexistent/config.yaml")

    def test_missing_required_key(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("watchlist:\n  - symbol: '000001'\n    name: 'test'\n")
            f.flush()
            try:
                with pytest.raises(ValueError, match="Missing required config key"):
                    load_config(f.name)
            finally:
                os.unlink(f.name)


class TestGetWatchlist:
    def test_returns_stock_configs(self, config_file):
        config = load_config(config_file)
        watchlist = get_watchlist(config)
        assert len(watchlist) == 2
        assert watchlist[0].symbol == "000001"
        assert watchlist[1].symbol == "600519"

    def test_default_sector(self, config_file):
        config = load_config(config_file)
        watchlist = get_watchlist(config)
        assert watchlist[1].sector == ""

    def test_explicit_market(self, config_file):
        config = load_config(config_file)
        watchlist = get_watchlist(config)
        assert watchlist[0].market == "A股"

    def test_auto_detect_market_a_share(self, config_file):
        config = load_config(config_file)
        watchlist = get_watchlist(config)
        # 600519 无market字段，应自动检测为A股
        assert watchlist[1].market == "A股"

    def test_multi_market_watchlist(self, multi_market_config_file):
        config = load_config(multi_market_config_file)
        watchlist = get_watchlist(config)
        assert len(watchlist) == 4
        assert watchlist[0].market == "A股"
        assert watchlist[1].market == "美股"
        assert watchlist[2].market == "美股"
        assert watchlist[3].market == "A股"


class TestDetectMarket:
    def test_a_share_shanghai(self):
        assert detect_market("600519") == MARKET_A_SHARE
        assert detect_market("601318") == MARKET_A_SHARE

    def test_a_share_shenzhen(self):
        assert detect_market("000001") == MARKET_A_SHARE
        assert detect_market("300750") == MARKET_A_SHARE

    def test_a_share_chinext(self):
        assert detect_market("688041") == MARKET_A_SHARE

    def test_us_stock(self):
        assert detect_market("AAPL") == MARKET_US
        assert detect_market("NVDA") == MARKET_US
        assert detect_market("MSFT") == MARKET_US

    def test_us_stock_with_dot(self):
        assert detect_market("BRK.B") == MARKET_US
        assert detect_market("GOOGL") == MARKET_US

    def test_hk_stock(self):
        assert detect_market("00700") == MARKET_HK
        assert detect_market("9988") == MARKET_HK


class TestGetThresholds:
    def test_returns_threshold_config(self, config_file):
        config = load_config(config_file)
        t = get_thresholds(config)
        assert t.price_change_pct == 5.0
        assert t.volume_spike_ratio == 2.5

    def test_defaults_when_missing(self):
        t = get_thresholds({})
        assert t.price_change_pct == 5.0
        assert t.lookback_days == 30


class TestGetHypotheses:
    def test_returns_active_hypotheses(self, config_file):
        config = load_config(config_file)
        hypotheses = get_hypotheses(config)
        assert len(hypotheses) == 1
        assert hypotheses[0].id == "h1"
        assert hypotheses[0].status == HypothesisStatus.ACTIVE

    def test_excludes_inactive(self, config_file):
        config = load_config(config_file)
        hypotheses = get_hypotheses(config)
        ids = [h.id for h in hypotheses]
        assert "h2" not in ids
