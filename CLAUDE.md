# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## ⛔ 硬性禁令 (最高优先级, 覆盖其他一切指引)

**绝对不要读取、修改、备份或以任何方式操作 `~/.zshrc`（以及其他 shell 配置文件：
`~/.zprofile` / `~/.bashrc` / `~/.bash_profile` / `~/.profile`）。**

这条不因任何理由放宽——包括"只是加一行"、"先备份再改"、"改完就能修好代理"。
即使排查结论指向 shell 配置是故障根因(见「数据源故障排查」), 也**只能在代码里适配**,
或者把问题写进报告/文档交给用户自己决定, 不得代为修改。

`run.sh` 会 `source ~/.zshrc`, 所以它的内容确实会影响本项目运行。正确做法是让代码
对任何 shell 环境都成立——例如 `src/http_urllib.ensure_domestic_via_proxy()` 在进程内
修正代理环境变量, 而不是去改用户的 shell 配置。

## Project Overview

每日股票监控系统 - 监控A股+美股(+港股)自选标的异动，自动搜索新闻并用AI分析原因，追踪核心投资假设，生成每日报告。

## Tech Stack

- Python 3.11+
- akshare: A股历史行情 (主) + A股新闻
- mootdx: 通达信A股日线 (AKShare限流时兜底, TCP不封IP)
- httpx: Finnhub美股行情 + 腾讯美股备用 + 腾讯财经A股基本面 + Yahoo美股港股K线/基本面 + Ollama HTTP调用
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
  - 美股: Finnhub quote (今日行情, 需FINNHUB_API_KEY) -> 腾讯 `qt.gtimg.cn/q=usXXX` (备, 涨跌幅按昨收自算; Stooq `q/l/` 2026-09 起 404 已移除), Yahoo chart 回填历史K线(含真实成交量), Finnhub的volume=0用Yahoo当日补全; Finnhub quote 按其时间戳 `t` 落**美东交易日**(`_finnhub_trade_date`), 周末/假日运行不再伪造当天行情; 加载历史时 `_purge_weekend_rows` 自动清除周末行。key 在 `~/.zshrc`, `run.sh` 自动 source
  - 港股: Yahoo chart 日K线 (唯一源, 见 global_stock.py)
  - 本地历史: `output/us_quote_history.json` 累积美股/港股每日行情, 涨跌幅按前收盘价重算
  - Rate limiting between requests (1s delay)
- **astock.py**: A股增强数据 (集成 a-stock-data skill 思路)
  - 基本面: 腾讯财经 `qt.gtimg.cn` (PE/PB/市值/换手率/量比/涨停跌停, GBK, 无需key, 不封IP)
  - 价格兜底: mootdx `client.bars` 通达信日K线 (TCP不封IP), 涨跌幅按前收盘价计算
  - 腾讯字段索引已校准 (43=振幅非PB, PB在46)
  - 资金面: 主力资金流(push2 `ulist.np` secids小批量) + 龙虎榜(东财→新浪兜底)
    + 北向资金(同花顺 hexin, 非东财) — 详见「数据源故障排查」
- **index_kline.py**: 指数量价结构 (mootdx TCP, 不走代理)
  - 补充腾讯简版行情拿不到的东西: **成交量、均线位置、相对强度**; 按指数名与 `market.CN_INDEXES` 合并
  - 宽基覆盖 沪深300/中证500/中证1000 — 主线发力端常在中小市值, 只看上证/创业板会漏
  - `build_style_strength()`: 小盘相对强度 = 中证1000/沪深300, 给 5/20/60 日三个尺度
    (短期修复与中期风格反转经常方向相反, 2026-08-12: 5日+3.29% 而 60日-8.47%)
  - 量比阈值按**指数口径**收窄至 1.15/0.85 — 指数是加权平均, 沿用个股的 1.3/0.7 会让它永远落在中间带
  - ⚠️ **不参与 `classify_regime`** — regime 序列必须跨日可比, 量价只作独立读数呈现
- **net_retry.py**: `retry_call()` 指数退避+抖动重试, 统一包住东财等易抖动的调用
- **global_stock.py**: 美股/港股增强数据 (集成 global-stock-data skill 思路)
  - K线: Yahoo chart v8 (`query2.finance.yahoo.com/v8`, 零crumb), 美股+港股完整OHLCV含成交量
  - 基本面: Yahoo quoteSummary (PE/前瞻PE/PB/PEG/市值/ROE/利润率/目标价/评级, 自动cookie+crumb)
  - `to_yahoo_symbol()`: 美股点号转横线(BRK.B->BRK-B), 港股补零加后缀(00700->0700.HK)
- **anomaly.py**: Three detectors - price change, volume spike, consecutive move
- **llm.py**: LLM provider abstraction (Claude API / OpenAI / DeepSeek / Ollama / OpenRouter / NVIDIA). OpenAI-compatible providers (openai/deepseek/openrouter/nvidia) share `_OpenAICompatibleProvider`; API key resolves config `api_key` first, then env var via `_resolve_api_key()`
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
- `llm.provider`: "claude" / "openai" / "deepseek" / "ollama" / "openrouter" / "nvidia"
- `claude`: API model settings (provider=claude)
- `openai`: model + optional `api_key` + `base_url` (provider=openai; base_url overridable for Azure/proxy)
- `deepseek`: model + optional `api_key` + `base_url` (provider=deepseek, OpenAI-compatible)
- `ollama`: Ollama settings (provider=ollama, local/cloud)

API keys: OpenAI-compatible providers accept `api_key` directly in `config.yaml` (takes precedence), otherwise fall back to env vars — `OPENAI_API_KEY`, `DEEPSEEK_API_KEY`, `OPENROUTER_API_KEY`, `NVIDIA_API_KEY`. Also `ANTHROPIC_API_KEY` (Claude), `FINNHUB_API_KEY` (美股主要数据源).

## 数据源故障排查 (重要 — 先读这段再动手)

### 东财 `SSL: UNEXPECTED_EOF_WHILE_READING` 不是"东财挂了"

症状: `push2.eastmoney.com` / `push2his.eastmoney.com` / `datacenter-web.eastmoney.com`
连续多日报 `SSLError(SSLEOFError)` 或 `ProxyError` / `RemoteProtocolError`,
表现为主力资金流/龙虎榜/解禁/新股全灭, 严重时连 A股K线(push2his) 也全灭。

> ⚠️ **2026-08-03 更新: 下面这段旧结论("代理不稳, 代码层面无解")已被推翻。**
> 真实根因是 `no_proxy` 让东财走了**直连**, 而本机没有境内出口。已在代码里修好,
> 见「真实根因」小节。旧结论保留在此仅作对照, 不要再据此下判断。

### 真实根因: `no_proxy` 把东财踢出了代理 (2026-08-03 定位)

`~/.zshrc` 里有这一行, 而 `run.sh` 会 `source ~/.zshrc`:

```
no_proxy=localhost,127.0.0.1,eastmoney.com,push2his.eastmoney.com
```

于是管线内所有东财请求**绕过 Clash 直连**——本机无境内出口, 直连东财在 TLS 层
即被阻断。同一 URL 对照实测(2026-08-03):

| 环境 | urllib | requests | requests + 强制代理 |
|------|--------|----------|--------------------|
| 东财走代理 | ✅ | ✅ | ✅ |
| 东财走直连(`no_proxy`) | ❌ SSL EOF | ❌ SSL EOF | ✅ |

**两种客户端表现完全一致 —— 决定成败的是走不走代理, 不是用哪个 HTTP 库。**
(此前怀疑过 "urllib 比 requests 稳", 是测试时 shell 环境不同造成的假象。)

**已修复(在代码里, 不动 shell 配置)**: `src/http_urllib.ensure_domestic_via_proxy()`
在 `main()` 第一行执行, 把 `DOMESTIC_PROXY_HOSTS` 里的域名从 `NO_PROXY`/`no_proxy`
**在进程内**摘除, 一次性对 akshare(requests) / httpx / urllib **三条通道同时生效**,
项目因此不再依赖用户 shell 配置。若将来本机有了直连境内的能力,
设 `STOCK_ALLOW_DIRECT_CN=1` 可关闭该修正。

⛔ **不要去改 `~/.zshrc` 里那行 `no_proxy`**(见顶部「硬性禁令」)。它对本项目已无影响——
进程内修正覆盖了它。若用户在项目外(如手动跑 `python -m src.realtime`)踩到同样的坑,
把现象和原因告诉用户, 由用户自己决定改不改。

⚠️ 别再往 Clash 里加 `DOMAIN-SUFFIX,eastmoney.com,DIRECT` —— 本机 DIRECT 打不通境内,
加了就是复现这个 bug。方向要反过来: 确保这些域名走一个能连境内的节点。

**仍然是真问题的**: `push2.eastmoney.com`(实时资金流) 即使走代理也只有偶发成功
(实测连发 0/8), 属大响应被隧道截断, 只能换源 → 已降级新浪日级资金流。

<details>
<summary>旧结论(2026-07-24, 已被推翻, 仅存档)</summary>

当时认为: 境内财经数据经境外 VPN 隧道 = 时通时断, 代码层面无法让不稳定的代理变稳定,
并建议给东财加 Clash DIRECT 规则。实际上 DIRECT 正是故障本身。

</details>

### 已内置的降级链 (主源挂了报告仍能产出)

备用端点集中在 **`src/fallback_sources.py`**, 来源是
[a-stock-data](https://github.com/simonlin1212/a-stock-data) /
[global-stock-data](https://github.com/simonlin1212/global-stock-data) 的「备用源速查」,
全部于 2026-08-03 在本机实测通过。原则: 备胎必须**换域名**, 与主源不共用风控面。

- **重试**: `net_retry.retry_call()` 指数退避+抖动, 包住全部东财调用
- **A股K线**: AKShare(东财push2his) → mootdx 通达信 TCP (不走代理, 主源全挂时的救命稻草)
- **龙虎榜**: 东财 datacenter(**唯一带净买额**) → 深交所+上交所官方 → akshare → 新浪
- **解禁 / 新股**: 东财 datacenter → akshare → 新浪逐标的
- **重大事件预警** (`src/calendar_events.py`): A股业绩披露预约日 `RPT_PUBLIC_BS_APPOIN` + 分红送转除权除息 `RPT_SHAREBONUS_DET` (东财 datacenter/urllib, 多代码 `SECURITY_CODE in (...)` 一次查); 港股财报 + 美港股除息日 Yahoo `calendarEvents` (美股财报已由 Nasdaq 覆盖的不重复加); 报告按倒计时 🔴≤3天 🟡≤7天 分级 (`report.calendar_rows`)。各源独立容错, 失败只进数据警告
- **上游周检** (`src/upstream_watch.py`): 每周比对 a-stock-data / global-stock-data 的 GitHub HEAD 与 `upstream_baseline.json`, 只改 README/资源不触发同步; 同步流程见 `.agents/skills/stock/SKILL.md`「上游数据源周检」, 完成后 `--mark-synced` 推进基线
- **财报日历**: Nasdaq `api.nasdaq.com/api/calendar/earnings` (**零鉴权, 主源**)
  → Finnhub(需 `FINNHUB_API_KEY`, 备)
- **主力资金流**: push2 `ulist.np` 小批量(15/批) → **新浪日级四档净额**(逐标的)。
  ⚠️ 新浪按大单方向聚合, 与东财口径不同且无净占比, `FundFlowInfo.source` 标记来源, 不可混算
- **美股成交量**: Yahoo 回填长期返回 `volume=0`(会让 anomaly 产出大量伪 high 信号),
  `fallback_sources.repair_us_volume()` 在管线 Step 0 用**新浪美股日K**补 0 值(不覆盖非零值)
- **北向资金**: 同花顺 hexin `hsgtApi` (东财北向字段2024-08起上游断供)。
  上游 sgt 数组常损坏, 已做长度/量级校验; 另有**陈旧检测**——连续多次返回同一数值即
  在报告里标注"本日数值不可信"(实测曾连续三日返回 -9.28亿)
- **北向十大活跃股**: HKEX 官方日统计。⚠️ HKEX 北向**只有成交额没有买卖拆分**,
  拿不到净买入, 不能替代 hexin 的净流入, 只作"北向在交易什么"的补充证据
- 任一源失败只降级不中断, 失败原因进报告顶部「数据质量警告」

### 管线顺序上的一个约束

`main()` 里日历(Step 0b)与资金面(Step 0c)排在抓行情(Step 1)**之前**。它们不依赖行情数据,
放前面是为了在代理最干净的时候先取完——A股K线阶段会向 push2his 连发 74 次请求。

## Output

Reports generated in `output/` directory as `YYYY-MM-DD.md`.
Hypothesis history in `output/hypothesis_history.json`.
US stock history in `output/us_quote_history.json`.
