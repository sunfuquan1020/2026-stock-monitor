# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

多市场每日股票监控系统 - 监控A股+美股+期货自选标的异动，自动搜索新闻并用AI分析原因，追踪核心投资假设，生成每日报告。

## Tech Stack

- Python 3.11+
- akshare: A股数据获取 + A股新闻
- httpx: Finnhub美股行情 + Stooq备用 + Ollama HTTP调用
- tqsdk: 中国期货数据 (天勤量化)
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

- **config.py**: Load and validate YAML config, market detection (A股/美股/港股/期货)
- **models.py**: Immutable data models (frozen dataclasses), StockConfig includes `market` field
- **fetcher.py**: Multi-market data fetching
  - A股: AKShare `stock_zh_a_hist` (primary)
  - 美股: Finnhub quote (primary, 需FINNHUB_API_KEY) -> Stooq `q/l/` (备用, 无需API Key)
  - 期货: TqSdk `api.get_kline_serial` (需天勤账号)
  - 本地历史: `output/us_quote_history.json` + `output/futures_quote_history.json`
  - Rate limiting between requests (1s delay)
- **anomaly.py**: Three detectors - price change, volume spike, consecutive move
- **llm.py**: LLM provider abstraction (Claude API / Ollama / OpenRouter / NVIDIA)
- **news.py**: News fetching + AI analysis
  - A股: AKShare `ak.stock_news_em` (东方财富新闻)
  - 美股: WebSearch supplementary (无免费API)
  - `build_websearch_queries()` for generating search queries
- **hypothesis.py**: Investment hypothesis tracking with history
- **report.py**: Jinja2-based markdown report generation (multi-market summary)
- **main.py**: CLI entry point orchestrating the pipeline
- **realtime.py**: Standalone realtime quotes (A股/美股/港股), separate from main pipeline

## Configuration

`config.yaml` contains:
- `watchlist`: Stock symbols with `market` field ("A股"/"美股"/"期货")
- `thresholds`: Anomaly detection thresholds
- `hypotheses`: Investment hypotheses to track (can include cross-market symbols)
- `llm.provider`: "claude" / "ollama" / "openrouter" / "nvidia"
- `claude`: API model settings (provider=claude)
- `ollama`: Ollama settings (provider=ollama)
- `tqsdk`: TqSdk credentials (username/password) for futures data

API keys from env vars: `ANTHROPIC_API_KEY`, `OPENROUTER_API_KEY`, `NVIDIA_API_KEY`, `FINNHUB_API_KEY` (美股主要数据源).

## Output

Reports generated in `output/` directory as `YYYY-MM-DD.md`.
Hypothesis history in `output/hypothesis_history.json`.
US stock history in `output/us_quote_history.json`.
Futures history in `output/futures_quote_history.json`.
