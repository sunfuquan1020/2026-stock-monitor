# 股票监控系统

每日股票监控系统 - 监控A股+美股自选标的异动，自动搜索新闻并用AI分析原因，追踪核心投资假设，生成每日报告。

## 功能特性

- **多市场支持**: 同时监控A股和美股
- **异动检测**: 价格大幅波动、成交量异常、连续涨跌
- **AI分析**: 自动获取新闻并用LLM分析异动原因
- **假设追踪**: 追踪投资假设的验证状态
- **每日报告**: 生成Markdown格式的每日监控报告

## 数据源

| 市场 | 数据源 | 说明 |
|------|--------|------|
| A股历史行情 | AKShare (主) + mootdx (兜底) | AKShare 限流失败时自动用通达信 mootdx 兜底，不封 IP |
| A股基本面 | 腾讯财经 | PE/PB/市值/换手率/量比/涨跌停，GBK 直连、无需 key |
| A股新闻 | AKShare (东方财富) | 个股新闻 |
| 美股行情 | Finnhub (今日) + Stooq (备) + Yahoo (历史K线) | Finnhub 需 API Key；Yahoo 回填历史 OHLCV 含真实成交量 |
| 港股行情 | Yahoo Finance chart | 唯一日 K 线源，自动 `00700 -> 0700.HK` |
| 美股/港股基本面 | Yahoo quoteSummary | PE/前瞻PE/PB/PEG/市值/ROE/利润率/目标价/评级 |

> A股增强集成自 [a-stock-data](https://github.com/simonlin1212/a-stock-data)（腾讯财经 + mootdx），美股/港股增强集成自 [global-stock-data](https://github.com/simonlin1212/global-stock-data)（Yahoo Finance）。

## 安装

### 1. 克隆项目

```bash
git clone <repository-url>
cd stock-monitor
```

### 2. 创建虚拟环境

```bash
python -m venv .venv
source .venv/bin/activate  # macOS/Linux
# 或
.venv\Scripts\activate  # Windows
```

### 3. 安装依赖

```bash
pip install -e ".[dev]"
```

### 4. 配置API Key

根据你选择的LLM提供商，设置相应的环境变量:

```bash
# Claude API
export ANTHROPIC_API_KEY="your-key"

# OpenRouter
export OPENROUTER_API_KEY="your-key"

# NVIDIA
export NVIDIA_API_KEY="your-key"

# Finnhub (美股主要数据源)
export FINNHUB_API_KEY="your-key"
```

## 使用方法

### 基本运行

```bash
# 使用配置文件运行
python -m src.main --config config.yaml

# 使用shell脚本运行
./run.sh
```

### 命令行参数

```bash
python -m src.main [OPTIONS]

Options:
  --config PATH    配置文件路径 (默认: config.yaml)
  --dry-run        跳过LLM API调用，仅获取数据和检测异动
  --today          生成Today日报
  --help           显示帮助信息
```

### 示例

```bash
# 干跑模式 (不调用LLM)
python -m src.main --config config.yaml --dry-run

# 生成Today日报
python -m src.main --config config.yaml --today

# 组合使用
python -m src.main --config config.yaml --dry-run --today
```

## 配置说明

配置文件 `config.yaml` 包含以下部分:

### 监控股票列表

```yaml
watchlist:
  # A股
  - symbol: "000001"
    name: "平安银行"
    sector: "金融"
    market: "A股"
  # 美股
  - symbol: "AAPL"
    name: "苹果"
    sector: "消费电子"
    market: "美股"
```

代码格式自动识别市场:
- A股: 6位数字 (如 `000001`、`600519`、`300750`)
- 美股: 字母代码 (如 `AAPL`、`NVDA`、`BRK.B`)

### 异动检测阈值

```yaml
thresholds:
  price_change_pct: 5.0      # 价格变动阈值 (%)
  volume_spike_ratio: 2.5    # 成交量异常倍数
  consecutive_days: 3        # 连续涨跌天数
  consecutive_change_pct: 3.0 # 连续涨跌幅度 (%)
  lookback_days: 30          # 回看天数
```

### LLM配置

```yaml
llm:
  provider: "openrouter"  # 可选: claude, ollama, openrouter, nvidia

claude:
  model: "claude-sonnet-4-20250514"
  max_tokens: 1024

openrouter:
  model: "openrouter/free"
```

### 投资假设

```yaml
hypotheses:
  - id: "h1"
    text: "AI芯片需求持续增长"
    related_symbols: ["NVDA", "AMD"]
    active: true
```

## 输出

- **每日报告**: `output/YYYY-MM-DD.md`
- **Today日报**: `output/YYYY-MM-DD-today.md`
- **假设历史**: `output/hypothesis_history.json`
- **美股历史**: `output/us_quote_history.json`

## 运行测试

```bash
# 运行所有测试
pytest tests/

# 运行特定测试文件
pytest tests/test_fetcher.py -v

# 运行并显示覆盖率
pytest tests/ --cov=src
```

## 更新日志

### v0.7.0 - 美股/港股数据增强

集成 [global-stock-data](https://github.com/simonlin1212/global-stock-data) skill 思路，增强美股行情、新增港股支持与美股/港股基本面。

#### 变更内容

1. **新增 `src/global_stock.py`**
   - Yahoo chart v8 日 K 线（零 crumb）：美股 + 港股完整 OHLCV，含真实成交量
   - Yahoo quoteSummary 基本面（自动 cookie+crumb）：PE/前瞻PE/PB/PEG/市值/ROE/利润率/目标价/评级
   - `to_yahoo_symbol()`：美股点号转横线（BRK.B→BRK-B），港股补零加后缀（00700→0700.HK）

2. **fetcher.py — 美股行情增强 + 港股路由**
   - 美股：Finnhub 今日行情为主，Yahoo 回填历史 K 线（修复 Finnhub `volume=0` 导致成交量异动检测失效），今日成交量用 Yahoo 补全
   - 港股：识别 4-5 位数字代码，路由到 Yahoo chart

3. **报告**
   - 新增「美股/港股基本面」表格
   - `models.py` 新增 `GlobalStockBasicInfo`，`ReportData` 新增 `global_basics`
   - 市场概览新增「港股」分组

4. **news.py**
   - 港股新闻并入「无免费 API → WebSearch 补充」路径

### v0.6.0 - A股数据增强

集成 [a-stock-data](https://github.com/simonlin1212/a-stock-data) skill 思路，新增 A股基本面 + 价格兜底。

#### 变更内容

1. **新增 `src/astock.py`**
   - 腾讯财经基本面: PE(TTM)/PE(静)/PB/总市值/流通市值/换手率/量比/涨停跌停价（GBK 直连，无需 key，不封 IP）
   - mootdx 通达信日 K 线兜底（TCP 不封 IP），涨跌幅按前收盘价计算
   - 腾讯字段索引已校准（43=振幅 非 PB，PB 在 46）

2. **fetcher.py**
   - A股历史行情改为「AKShare 主 + mootdx 兜底」：AKShare 限流/返回空时自动切换 mootdx

3. **报告**
   - 每日报告新增「A股基本面」表格（现价/涨跌幅/PE/PB/总市值/换手率/量比）
   - `models.py` 新增 `AShareBasicInfo`，`ReportData` 新增 `a_share_basics`

4. **依赖**
   - 新增 `mootdx>=0.10`
   - `httpx` 下限放宽到 `>=0.25.0`（与 mootdx 兼容）

### v0.5.0 - 聚焦股票，移除期货

**重大变更**: 移除期货 (TqSdk) 数据源，系统专注于A股 + 美股

#### 变更内容

1. **移除期货支持**
   - 删除 TqSdk 数据源及 `_normalize_tqsdk_df` 等相关代码
   - 移除 `MARKET_FUTURES` 市场常量及期货合约识别
   - 移除 watchlist 中的期货条目和 `tqsdk` 配置段
   - 移除 `tqsdk>=3.0.0` 依赖

2. **美股涨跌幅修正 (fetcher.py)**
   - 从本地历史构建行情时，涨跌幅改为按"前一交易日收盘价"重算
   - 修正 Stooq 仅提供盘中 (开盘→收盘) 涨跌幅导致的异动误判
   - 口径与A股一致，使美股异动检测在累积≥2天数据后正常工作

#### 注意事项

- A股数据使用AKShare，国内网络直连无需代理
- 美股数据使用Finnhub，需要设置 `FINNHUB_API_KEY` 环境变量
- 部分A股可能因AKShare限流而获取失败，属正常现象

## 项目结构

```
stock-monitor/
├── config.yaml          # 配置文件
├── pyproject.toml       # 项目配置
├── run.sh               # 运行脚本
├── src/
│   ├── config.py        # 配置加载
│   ├── models.py        # 数据模型
│   ├── fetcher.py       # 数据获取 (AKShare + Finnhub + Stooq)
│   ├── astock.py        # A股增强 (腾讯财经基本面 + mootdx 兜底)
│   ├── global_stock.py  # 美股/港股增强 (Yahoo K线 + 基本面)
│   ├── anomaly.py       # 异动检测
│   ├── news.py          # 新闻获取 (AKShare)
│   ├── llm.py           # LLM接口
│   ├── hypothesis.py    # 假设追踪
│   ├── report.py        # 报告生成
│   ├── realtime.py      # 实时行情
│   └── main.py          # 主入口
├── tests/               # 测试文件
├── templates/           # 报告模板
└── output/              # 输出目录
```

## 许可证

MIT License
