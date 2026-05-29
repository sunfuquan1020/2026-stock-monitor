# 股票监控系统

多市场每日股票监控系统 - 监控A股+美股+期货自选标的异动，自动搜索新闻并用AI分析原因，追踪核心投资假设，生成每日报告。

## 功能特性

- **多市场支持**: 同时监控A股、美股和中国期货
- **异动检测**: 价格大幅波动、成交量异常、连续涨跌
- **AI分析**: 自动获取新闻并用LLM分析异动原因
- **假设追踪**: 追踪投资假设的验证状态
- **每日报告**: 生成Markdown格式的每日监控报告

## 数据源

| 市场 | 数据源 | 说明 |
|------|--------|------|
| A股 | AKShare | 历史行情 + 新闻 (东方财富) |
| 美股 | Finnhub (主) + Stooq (备) | 实时行情，Finnhub需API Key |
| 期货 | TqSdk (天勤量化) | 日K线数据，需天勤账号 |

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

### 5. 配置期货账号 (可选)

如果需要监控期货，在 `config.yaml` 中填写天勤账号:

```yaml
tqsdk:
  username: "your-tianqin-username"
  password: "your-tianqin-password"
```

注册地址: https://www.shinnytech.com/tianqin

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
  # 期货
  - symbol: "SHFE.au2508"
    name: "沪金2508"
    sector: "贵金属期货"
    market: "期货"
```

期货合约代码格式: `交易所.合约代码`
- SHFE: 上期所 (铜、铝、金、银、螺纹钢等)
- DCE: 大商所 (铁矿石、焦炭、豆粕等)
- CZCE: 郑商所 (甲醇、白糖、PTA等)
- CFFEX: 中金所 (股指期货IF/IH/IC/IM)
- INE: 能源中心 (原油SC)

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

### TqSdk期货配置

```yaml
tqsdk:
  username: ""  # 天勤账号
  password: ""  # 天勤密码
```

## 输出

- **每日报告**: `output/YYYY-MM-DD.md`
- **Today日报**: `output/YYYY-MM-DD-today.md`
- **假设历史**: `output/hypothesis_history.json`
- **美股历史**: `output/us_quote_history.json`
- **期货历史**: `output/futures_quote_history.json`

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

### v0.4.0 - 数据源重构

**重大变更**: 全面更换数据源，新增期货支持

#### 变更内容

1. **fetcher.py**
   - A股数据源: yfinance → AKShare (`ak.stock_zh_a_hist`)
   - 美股数据源: Stooq(主)+Finnhub(备) → Finnhub(主)+Stooq(备)
   - 新增期货数据源: TqSdk (`api.get_kline_serial`)
   - 成交额直接使用AKShare提供的精确值
   - 涨跌幅直接使用AKShare提供的数据

2. **news.py**
   - A股新闻源: yfinance → AKShare (`ak.stock_news_em`，东方财富新闻)
   - 美股新闻: 无免费API，依赖WebSearch补充

3. **config.py**
   - 新增 `MARKET_FUTURES` 市场常量
   - `detect_market()` 支持期货合约代码识别 (如 `SHFE.cu2501`)

4. **pyproject.toml**
   - 移除 `yfinance>=0.2.0`
   - 添加 `akshare>=1.14.0`, `tqsdk>=3.0.0`

5. **config.yaml**
   - 新增期货watchlist条目
   - 新增 `tqsdk` 配置段 (username/password)

#### 迁移原因

- AKShare提供更准确的A股数据（成交额、涨跌幅直接提供）
- Finnhub美股数据更稳定，提供精确涨跌幅
- TqSdk是中国期货行业标准数据源

#### 注意事项

- A股数据使用AKShare，国内网络直连无需代理
- 美股数据使用Finnhub，需要设置 `FINNHUB_API_KEY` 环境变量
- 期货数据使用TqSdk，需要注册天勤账号并填写到config.yaml
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
│   ├── fetcher.py       # 数据获取 (AKShare + Finnhub + TqSdk)
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
