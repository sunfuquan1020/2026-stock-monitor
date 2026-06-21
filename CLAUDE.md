# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

每日股票监控系统 - 监控A股+美股(+港股)自选标的异动，自动搜索新闻并用AI分析原因，追踪核心投资假设，生成每日报告。

## Tech Stack

- Python 3.11+
- akshare: A股历史行情 (主) + A股新闻
- mootdx: 通达信A股日线 (AKShare限流时兜底, TCP不封IP)
- httpx: Finnhub美股行情 + Stooq备用 + 腾讯财经A股基本面 + Yahoo美股港股K线/基本面 + Ollama HTTP调用
- pandas: 数据处理
- anthropic: Claude API分析
- pyyaml: 配置管理
- jinja2: 报告模板

## Development Commands

```bash
# Install dependencies
pip install -e ".[dev]"

# Run tests
pytest tests/

# Run a single test file
pytest tests/test_anomaly.py -v

# Run with dry-run (skip Claude API calls)
python -m src.main --config config.yaml --dry-run

# Full run
python -m src.main --config config.yaml
```

## Architecture

Linear pipeline: config -> fetch -> detect -> analyze -> report

- **config.py**: Load and validate YAML config, market detection (A股/美股/港股)
- **models.py**: Immutable data models (frozen dataclasses), StockConfig includes `market` field
- **fetcher.py**: Multi-market data fetching (A股 + 美股 + 港股)
  - A股: AKShare `stock_zh_a_hist` (primary) -> mootdx 通达信日线 (限流/失败兜底, 见 astock.py)
  - 美股: Finnhub quote (今日行情, 需FINNHUB_API_KEY) -> Stooq `q/l/` (备), Yahoo chart 回填历史K线(含真实成交量), Finnhub的volume=0用Yahoo当日补全
  - 港股: Yahoo chart 日K线 (唯一源, 见 global_stock.py)
  - 本地历史: `output/us_quote_history.json` 累积美股/港股每日行情, 涨跌幅按前收盘价重算
  - Rate limiting between requests (1s delay)
- **astock.py**: A股增强数据 (集成 a-stock-data skill 思路)
  - 基本面: 腾讯财经 `qt.gtimg.cn` (PE/PB/市值/换手率/量比/涨停跌停, GBK, 无需key, 不封IP)
  - 价格兜底: mootdx `client.bars` 通达信日K线 (TCP不封IP), 涨跌幅按前收盘价计算
  - 腾讯字段索引已校准 (43=振幅非PB, PB在46)
- **global_stock.py**: 美股/港股增强数据 (集成 global-stock-data skill 思路)
  - K线: Yahoo chart v8 (`query2.finance.yahoo.com/v8`, 零crumb), 美股+港股完整OHLCV含成交量
  - 基本面: Yahoo quoteSummary (PE/前瞻PE/PB/PEG/市值/ROE/利润率/目标价/评级, 自动cookie+crumb)
  - `to_yahoo_symbol()`: 美股点号转横线(BRK.B->BRK-B), 港股补零加后缀(00700->0700.HK)
- **anomaly.py**: Three detectors - price change, volume spike, consecutive move
- **llm.py**: LLM provider abstraction (Claude API / Ollama / OpenRouter / NVIDIA)
- **news.py**: News fetching + AI analysis
  - A股: AKShare `ak.stock_news_em` (东方财富新闻)
  - 美股/港股: WebSearch supplementary (无免费API)
  - `build_websearch_queries()` for generating search queries
- **hypothesis.py**: Investment hypothesis tracking with history
- **report.py**: Jinja2-based markdown report generation (含 A股基本面 + 美股/港股基本面 表格 + multi-market summary)
- **main.py**: CLI entry point orchestrating the pipeline
- **realtime.py**: Standalone realtime quotes (A股/美股/港股), separate from main pipeline

## Configuration

`config.yaml` contains:
- `watchlist`: Stock symbols with `market` field ("A股"/"美股"/"港股")
- `thresholds`: Anomaly detection thresholds
- `hypotheses`: Investment hypotheses to track (can include cross-market symbols)
- `llm.provider`: "claude" / "ollama" / "openrouter" / "nvidia"
- `claude`: API model settings (provider=claude)
- `ollama`: Ollama settings (provider=ollama)

API keys from env vars: `ANTHROPIC_API_KEY`, `OPENROUTER_API_KEY`, `NVIDIA_API_KEY`, `FINNHUB_API_KEY` (美股主要数据源).

## Output

Reports generated in `output/` directory as `YYYY-MM-DD.md`.
Hypothesis history in `output/hypothesis_history.json`.
US stock history in `output/us_quote_history.json`.
