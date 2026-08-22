# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## Project Overview

每日股票监控系统 - 监控A股+美股(+港股)自选标的异动，自动搜索新闻并用AI分析原因，追踪核心投资假设，生成每日报告。

## Tech Stack

- Python 3.11+
- akshare: A股历史行情 (主) + A股新闻
- mootdx: 通达信A股日线 (AKShare限流时兜底, TCP不封IP)
- httpx: Finnhub美股行情 + Stooq备用 + 腾讯财经A股基本面 + Yahoo美股港股K线/基本面 + Ollama HTTP调用
- pandas: 数据处理
- anthropic: Codex API分析
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

# Run with dry-run (skip Codex API calls)
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
  - 资金面: 主力资金流(push2 `ulist.np` secids小批量) + 龙虎榜(东财→新浪兜底)
    + 北向资金(同花顺 hexin, 非东财) — 详见「数据源故障排查」
- **net_retry.py**: `retry_call()` 指数退避+抖动重试, 统一包住东财等易抖动的调用
- **global_stock.py**: 美股/港股增强数据 (集成 global-stock-data skill 思路)
  - K线: Yahoo chart v8 (`query2.finance.yahoo.com/v8`, 零crumb), 美股+港股完整OHLCV含成交量
  - 基本面: Yahoo quoteSummary (PE/前瞻PE/PB/PEG/市值/ROE/利润率/目标价/评级, 自动cookie+crumb)
  - `to_yahoo_symbol()`: 美股点号转横线(BRK.B->BRK-B), 港股补零加后缀(00700->0700.HK)
- **anomaly.py**: Three detectors - price change, volume spike, consecutive move
- **llm.py**: LLM provider abstraction (Codex API / OpenAI / DeepSeek / Ollama / OpenRouter / NVIDIA). OpenAI-compatible providers (openai/deepseek/openrouter/nvidia) share `_OpenAICompatibleProvider`; API key resolves config `api_key` first, then env var via `_resolve_api_key()`
- **news.py**: News fetching + AI analysis
  - 全市场 (A股/美股/港股): WebSearch supplementary (统一无免费API; A股原 AKShare stock_news_em TLS 不稳定已弃用)
  - `fetch_news()` 返回空 -> main.py 收集 `build_websearch_queries()` 生成的查询
- **hypothesis.py**: Investment hypothesis tracking with history
- **report.py**: Jinja2-based markdown report generation (含 A股基本面 + 美股/港股基本面 表格 + multi-market summary)
- **main.py**: CLI entry point orchestrating the pipeline
- **realtime.py**: Standalone realtime quotes (A股/美股/港股), separate from main pipeline

## Configuration

`config.yaml` contains:
- `watchlist`: Stock symbols with `market` field ("A股"/"美股"/"港股")
- `thresholds`: Anomaly detection thresholds
- `hypotheses`: Investment hypotheses to track (can include cross-market symbols)
- `llm.provider`: "Codex" / "openai" / "deepseek" / "ollama" / "openrouter" / "nvidia"
- `Codex`: API model settings (provider=Codex)
- `openai`: model + optional `api_key` + `base_url` (provider=openai; base_url overridable for Azure/proxy)
- `deepseek`: model + optional `api_key` + `base_url` (provider=deepseek, OpenAI-compatible)
- `ollama`: Ollama settings (provider=ollama, local/cloud)

API keys: OpenAI-compatible providers accept `api_key` directly in `config.yaml` (takes precedence), otherwise fall back to env vars — `OPENAI_API_KEY`, `DEEPSEEK_API_KEY`, `OPENROUTER_API_KEY`, `NVIDIA_API_KEY`. Also `ANTHROPIC_API_KEY` (Codex), `FINNHUB_API_KEY` (美股主要数据源).

## 数据源故障排查 (重要 — 先读这段再动手)

### 东财 `SSL: UNEXPECTED_EOF_WHILE_READING` 不是"东财挂了"

症状: `push2.eastmoney.com` / `push2his.eastmoney.com` / `datacenter-web.eastmoney.com`
连续多日报 `SSLError(SSLEOFError)` 或 `ProxyError` / `RemoteProtocolError`,
表现为主力资金流/龙虎榜/解禁/新股全灭, 严重时连 A股K线(push2his) 也全灭。

**根因是本地 Clash 代理 (`HTTP_PROXY=http://127.0.0.1:7890`), 不是东财接口失效。**
2026-07-24 实测证据:

| 测试 | 结果 | 说明 |
|------|------|------|
| `curl` 打 push2 真实API路径 | HTTP 200 | 东财接口本身正常 |
| `requests`(akshare) 走代理 | `ProxyError` | urllib3 穿该代理隧道不稳 |
| `httpx` 直连 (`trust_env=False`) | `SSL EOF` | push2 直连被 TLS 层阻断 |
| `httpx` 走代理 (`trust_env=True`) | 200, 但大响应/连发会掐断 | **push2 必须走代理, 但代理不稳** |

即: 境内财经数据经境外 VPN 隧道 = 时通时断。**代码层面无法让不稳定的代理变稳定**,
不要再把时间花在"换东财接口"上——免费源里龙虎榜/解禁/新股/主力资金流近乎东财独家
(a-stock-data skill 也全走东财 datacenter)。

**根治方向 (改 Clash 配置, 不改代码)**: 给境内数据域名加直连规则——
```yaml
rules:
  - DOMAIN-SUFFIX,eastmoney.com,DIRECT
  - DOMAIN-SUFFIX,gtimg.cn,DIRECT
  - DOMAIN-SUFFIX,hexin.cn,DIRECT
  - DOMAIN-SUFFIX,sina.com.cn,DIRECT
```
⚠️ 前提是本机 DIRECT 能连通东财; 若机器无境内出口, DIRECT 反而不通,
则需反向确保这些域名走一个能稳定连境内的节点。

### 已内置的降级链 (代理挂了报告仍能产出)

- **重试**: `net_retry.retry_call()` 指数退避+抖动, 包住全部东财调用
- **A股K线**: AKShare(东财push2his) → mootdx 通达信 TCP (不走代理, 代理全挂时的救命稻草)
- **龙虎榜**: 东财 → 新浪 `stock_lhb_detail_daily_sina` (非东财; 无净买额字段)
- **解禁**: 东财 → 新浪 `stock_restricted_release_queue_sina` (非东财; 逐标的查询)
- **主力资金流**: push2 `ulist.np/get` 按 `secids` **小批量**(15/批) 取 watchlist,
  而非全市场6000行——大响应最易被代理截断。经 httpx(`trust_env=True`) 绕开 requests 的 TLS 失败
- **北向资金**: 同花顺 hexin `hsgtApi` (非东财; 东财北向字段2024-08起上游断供)。
  上游 sgt 数组常损坏, 已做长度/量级校验, 异常字段置 None 并降级为警告
- 任一源失败只降级不中断, 失败原因进报告顶部「数据质量警告」

## Output

Reports generated in `output/` directory as `YYYY-MM-DD.md`.
Hypothesis history in `output/hypothesis_history.json`.
US stock history in `output/us_quote_history.json`.
