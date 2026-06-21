"""Configuration loading and validation."""

import re

import yaml
from pathlib import Path

from src.models import (
    Hypothesis,
    HypothesisStatus,
    StockConfig,
    ThresholdConfig,
)

# 市场常量
MARKET_A_SHARE = "A股"
MARKET_US = "美股"
MARKET_HK = "港股"


def load_config(path: str) -> dict:
    """Load and validate YAML config file."""
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    _validate_required_keys(config)
    return config


def _validate_required_keys(config: dict) -> None:
    """Validate that all required config keys exist."""
    required_keys = ["watchlist", "thresholds", "claude"]
    for key in required_keys:
        if key not in config:
            raise ValueError(f"Missing required config key: {key}")

    if not isinstance(config["watchlist"], list) or len(config["watchlist"]) == 0:
        raise ValueError("watchlist must be a non-empty list")

    for stock in config["watchlist"]:
        for field in ["symbol", "name"]:
            if field not in stock:
                raise ValueError(f"Each stock must have '{field}' field")


def get_watchlist(config: dict) -> list[StockConfig]:
    """Extract watchlist as StockConfig objects."""
    return [
        StockConfig(
            symbol=s["symbol"],
            name=s["name"],
            sector=s.get("sector", ""),
            market=s.get("market", detect_market(s["symbol"])),
        )
        for s in config["watchlist"]
    ]


def detect_market(symbol: str) -> str:
    """根据代码格式判断市场。

    规则:
    - 6位数字，首位0/3/6 -> A股
    - 4-5位数字 -> 港股
    - 纯字母(可含点号) -> 美股
    """
    if re.match(r"^[036]\d{5}$", symbol):
        return MARKET_A_SHARE
    elif re.match(r"^\d{4,5}$", symbol):
        return MARKET_HK
    elif re.match(r"^[A-Z]{1,5}(\.[A-Z])?$", symbol):
        return MARKET_US
    return MARKET_A_SHARE  # 默认A股


def get_thresholds(config: dict) -> ThresholdConfig:
    """Extract threshold configuration."""
    t = config.get("thresholds", {})
    return ThresholdConfig(
        price_change_pct=t.get("price_change_pct", 5.0),
        volume_spike_ratio=t.get("volume_spike_ratio", 2.5),
        consecutive_days=t.get("consecutive_days", 3),
        consecutive_change_pct=t.get("consecutive_change_pct", 3.0),
        lookback_days=t.get("lookback_days", 30),
    )


def get_hypotheses(config: dict) -> list[Hypothesis]:
    """Extract hypotheses from config."""
    raw = config.get("hypotheses", [])
    return [
        Hypothesis(
            id=h["id"],
            text=h["text"],
            related_symbols=tuple(h.get("related_symbols", [])),
            status=HypothesisStatus(h.get("status", "active")),
        )
        for h in raw
        if h.get("active", True)
    ]
