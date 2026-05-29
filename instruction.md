# A股 + 美股每日监控系统 - 使用教程

## 目录

1. [系统概述](#1-系统概述)
2. [环境准备与安装](#2-环境准备与安装)
3. [配置文件详解](#3-配置文件详解)
4. [LLM提供商配置](#4-llm提供商配置)
5. [数据源配置](#5-数据源配置)
6. [运行系统](#6-运行系统)
7. [实时行情监控](#7-实时行情监控)
8. [理解报告](#8-理解报告)
9. [异动检测规则详解](#9-异动检测规则详解)
10. [投资假设追踪](#10-投资假设追踪)
11. [定时任务设置](#11-定时任务设置)
12. [常见问题](#12-常见问题)

---

## 1. 系统概述

本系统是一个多市场（A股 + 美股）每日监控系统，核心功能：

- **异动检测**：自动检测自选股的价格异动、放量、连续走势
- **AI分析**：对异动股票搜索新闻，用AI（Ollama本地模型 / Claude API）分析原因
- **假设追踪**：持续追踪投资假设，评估是否有新证据支持或反驳
- **多市场支持**：A股（akshare / 新浪财经）+ 美股（Finnhub / Stooq）
- **报告生成**：每日标准报告 + Today 日报

### 当前自选股规模

| 市场 | 数量 | 数据源 |
|------|------|--------|
| A股 | 34只 | akshare（历史）/ 新浪财经（实时） |
| 美股 | 28只 | Finnhub（主）/ Stooq（备） |
| **合计** | **62只** | |

---

## 2. 环境准备与安装

### 系统要求

- macOS / Linux / Windows (WSL)
- Python 3.11+

### 安装步骤

```bash
# 进入项目目录
cd /Volumes/SSD2T/Users/fortune/2026-program/01-claude-program/2026-stock-monitor

# 创建虚拟环境
python3 -m venv .venv

# 激活虚拟环境
source .venv/bin/activate

# 安装依赖
pip install -e ".[dev]"

# 验证安装
pytest tests/
```

应看到 `41 passed` 的输出。

### 依赖说明

| 包 | 用途 |
|----|------|
| akshare | A股历史数据 |
| anthropic | Claude API（可选） |
| httpx | HTTP请求（Ollama / Finnhub / 新浪财经） |
| pyyaml | 配置解析 |
| jinja2 | 报告模板 |
| pytest | 测试（开发） |

---

## 3. 配置文件详解

配置文件 `config.yaml` 控制系统全部行为。

### 3.1 自选股列表 (watchlist)

A股和美股统一配置在 `watchlist` 中：

```yaml
watchlist:
  # A股 - 6位数字代码
  - symbol: "000001"
    name: "平安银行"
    sector: "银行"
  - symbol: "688256"
    name: "寒武纪"
    sector: "半导体"

  # 美股 - 字母代码（含点号的如 BRK.B 也支持）
  - symbol: "NVDA"
    name: "英伟达"
    sector: "半导体"
  - symbol: "BRK.B"
    name: "伯克希尔(B)"
    sector: "金融"
```

**添加新股票：** 直接在 `watchlist` 下添加即可。系统会根据代码格式自动判断市场：
- 6位数字 → A股
- 字母（可含点号）→ 美股
- 4-5位数字 → 港股

### 3.2 异动检测阈值 (thresholds)

```yaml
thresholds:
  price_change_pct: 5.0       # 日涨跌幅阈值（%）
  volume_spike_ratio: 2.5     # 放量倍数阈值
  consecutive_days: 3          # 连续走势天数
  consecutive_change_pct: 3.0  # 连续走势累计变化（%）
  lookback_days: 30            # 历史数据窗口（天）
```

**调参建议：**

| 参数 | 默认值 | 更敏感 | 更保守 |
|------|--------|--------|--------|
| price_change_pct | 5.0 | 3.0 | 7.0 |
| volume_spike_ratio | 2.5 | 2.0 | 4.0 |
| consecutive_days | 3 | 2 | 5 |
| consecutive_change_pct | 3.0 | 2.0 | 5.0 |

### 3.3 投资假设追踪 (hypotheses)

```yaml
hypotheses:
  - id: "h1"
    text: "银行板块将受益于降息周期，估值修复"
    related_symbols: ["000001", "601398"]
    active: true
  - id: "h2"
    text: "AI算力需求爆发，利好GPU芯片和半导体设备"
    related_symbols: ["NVDA", "AMD", "688041"]
    active: true
```

详见 [第10节：投资假设追踪](#10-投资假设追踪)。

### 3.4 输出设置 (output)

```yaml
output:
  dir: "output"    # 报告输出目录
  keep_days: 30    # 保留最近多少天的报告，超出自动删除
```

---

## 4. LLM提供商配置

系统支持4种LLM提供商，通过 `llm.provider` 字段切换：`ollama` / `openrouter` / `nvidia` / `claude`

### 4.1 使用Ollama（本地部署）

无需API费用，数据完全本地处理。

```bash
# 安装
brew install ollama          # macOS
curl -fsSL https://ollama.com/install.sh | sh   # Linux

# 启动服务 + 下载模型
ollama serve
ollama pull gemma4:e4b       # 8B，推荐
ollama pull deepseek-r1:8b   # 8B，中文更好
```

```yaml
llm:
  provider: "ollama"
ollama:
  model: "gemma4:e4b"
  base_url: "http://localhost:11434"
```

### 4.2 使用OpenRouter（云端API聚合平台）

OpenRouter 聚合了上百个模型，一个Key用所有模型，注册送免费额度。

**注册：** https://openrouter.ai → 获取API Key

**设置环境变量：**

```bash
export OPENROUTER_API_KEY="sk-or-v1-xxxxxxxxxxxxx"

# 永久设置
echo 'export OPENROUTER_API_KEY="sk-or-v1-xxxxxxxxxxxxx"' >> ~/.zshrc
source ~/.zshrc
```

**配置config.yaml：**

```yaml
llm:
  provider: "openrouter"
openrouter:
  model: "qwen/qwen-2.5-72b-instruct"
```

**推荐模型：**

| 模型 | 价格 | 特点 |
|------|------|------|
| `qwen/qwen-2.5-72b-instruct` | 便宜 | 中文效果好 |
| `deepseek/deepseek-chat` | 极便宜 | 中文优秀 |
| `google/gemini-2.0-flash-exp:free` | 免费 | 速度快 |
| `anthropic/claude-sonnet-4` | 负责 | 高质量分析 |

完整模型列表：https://openrouter.ai/models

### 4.3 使用NVIDIA build.nvidia.com（云端GPU推理）

NVIDIA官方API，提供顶级开源模型的高速推理，注册有免费额度。

**注册：** https://build.nvidia.com → 获取API Key

**设置环境变量：**

```bash
export NVIDIA_API_KEY="nvapi-xxxxxxxxxxxxx"

# 永久设置
echo 'export NVIDIA_API_KEY="nvapi-xxxxxxxxxxxxx"' >> ~/.zshrc
source ~/.zshrc
```

**配置config.yaml：**

```yaml
llm:
  provider: "nvidia"
nvidia:
  model: "nvidia/llama-3.1-nemotron-70b-instruct"
```

**推荐模型：**

| 模型 | 特点 |
|------|------|
| `nvidia/llama-3.1-nemotron-70b-instruct` | NVIDIA自研优化，效果好 |
| `meta/llama-3.1-405b-instruct` | 最强开源模型 |
| `deepseek/deepseek-r1` | 推理能力强 |

完整模型列表：https://build.nvidia.com/explore/discover

### 4.4 使用Claude API（云端）

```yaml
llm:
  provider: "claude"
claude:
  model: "claude-sonnet-4-20250514"
  max_tokens: 1024
```

```bash
export ANTHROPIC_API_KEY="sk-ant-api03-xxxxxxxxxxxxx"
```

### 4.5 提供商对比

| 特性 | Ollama | OpenRouter | NVIDIA | Claude |
|------|--------|------------|--------|--------|
| 费用 | 免费(需GPU) | 按量付费(便宜) | 按量付费 | 按量付费(贵) |
| 免费额度 | - | 注册送 | 注册送 | - |
| 速度 | 取决于GPU | 快 | 很快 | 快 |
| 质量 | 取决于模型 | 取决于模型 | 取决于模型 | 最高 |
| 中文能力 | 取决于模型 | 好(Qwen/DeepSeek) | 好 | 好 |
| API Key | 不需要 | OPENROUTER_API_KEY | NVIDIA_API_KEY | ANTHROPIC_API_KEY |
| 推荐场景 | 隐私敏感 | 灵活切换模型 | 高速推理 | 最高质量 |

---

## 5. 数据源配置

### 5.1 A股数据

- **历史数据**：akshare（免费，自动获取）
- **实时数据**：新浪财经API（免费，`realtime.py` 使用）

无需额外配置，开箱即用。

### 5.2 美股数据

支持两个数据源，自动选择：

| 数据源 | 优先级 | 需要API Key | 说明 |
|--------|--------|-------------|------|
| Finnhub | 主 | 是（免费） | 实时数据，免费额度60次/分钟 |
| Stooq | 备 | 否 | 延迟数据，完全免费 |

**配置Finnhub API Key：**

1. 访问 https://finnhub.io/ 注册账号
2. 获取免费API Key
3. 设置环境变量：

```bash
# 临时设置
export FINNHUB_API_KEY="你的key"

# 永久设置
echo 'export FINNHUB_API_KEY="你的key"' >> ~/.zshrc
source ~/.zshrc
```

**逻辑：** 设置了 `FINNHUB_API_KEY` 就用Finnhub，未设置则自动回退到Stooq。

### 5.3 港股数据（可选）

使用 EOD Historical Data，需设置 `EOD_API_KEY` 环境变量。

---

## 6. 运行系统

### 6.1 使用run.sh（推荐）

```bash
# 完整运行（Ollama模式，无需API Key）
./run.sh

# 完整运行 + 生成Today日报
./run.sh --today

# 测试模式（跳过LLM调用）
./run.sh --dry-run

# 自定义输出目录
./run.sh --output-dir /path/to/reports
```

### 6.2 直接运行

```bash
source .venv/bin/activate

# Ollama模式（确保Ollama服务已启动）
python -m src.main --config config.yaml

# Claude API模式
export ANTHROPIC_API_KEY="sk-ant-xxx"
python -m src.main --config config.yaml
```

### 6.3 CLI参数一览

| 参数 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| --config | 是 | - | 配置文件路径 |
| --output-dir | 否 | output | 报告输出目录 |
| --dry-run | 否 | false | 跳过LLM调用，仅检测异动 |
| --today | 否 | false | 同时生成Today日报 |

### 6.4 Claude Code /today 命令（推荐）

在 Claude Code 中直接输入 `/today` 即可一键运行每日监控，无需手动执行命令。

**使用方式：**
```
/today
```

**执行内容：**
1. 运行 `./run.sh --today` 执行完整 pipeline
2. 自动处理错误并给出排查建议（Ollama 未启动、API Key 缺失等）
3. 读取并展示生成的 Today 日报
4. 汇总异动股票、AI 分析摘要、假设状态变化

**Skill 文件位置：** `.claude/skills/today/SKILL.md`

### 6.5 运行流程

```
config.yaml → 获取行情 → 检测异动 → AI分析新闻 → 假设追踪 → 生成报告
                                    ↓
                          output/YYYY-MM-DD.md
                          output/YYYY-MM-DD-today.md (如果带--today)
```

---

## 7. 实时行情监控

`src/realtime.py` 是独立的实时行情脚本，支持A股 + 美股，无需LLM。

### 7.1 基本用法

```bash
# 从 watchlist.md 读取（推荐）
python -m src.realtime --watchlist watchlist.md

# 从 config.yaml 读取
python -m src.realtime --config config.yaml

# 保存到文件
python -m src.realtime --watchlist watchlist.md --output realtime-report.md
```

### 7.2 市值级别异动阈值

实时监控使用基于市值的差异化阈值：

| 市值级别 | 异动阈值 | 代表股票 |
|----------|----------|----------|
| 大盘 | ±3% | NVDA, AAPL, MSFT, 贵州茅台, 宁德时代 |
| 中盘 | ±5% | PTC, MP, MSTR, 兆易创新, 赣锋锂业 |
| 小盘 | ±7% | CRCL, 东方钽业, 宜安科技 |

大盘股波动小，用更低阈值捕捉异动；小盘股波动大，用更高阈值减少噪音。

### 7.3 输出格式

```
# 实时行情监控 -- 2026-05-21 15:30:00

共监控 62 只股票，3 只触发异动

## 异动信号
| 股票 | 代码 | 市场 | 市值 | 现价 | 涨跌幅 | 原因 |
|------|------|------|------|------|--------|------|
| 英伟达 | NVDA | 美股 | 大盘 | 135.20 | ↑+4.52% | 涨跌幅+4.52%超过大盘阈值±3% |

## 涨幅前5
...

## 跌幅前5
...
```

### 7.4 数据源自动选择

```
美股:
  有 FINNHUB_API_KEY → Finnhub（实时，推荐）
  无 FINNHUB_API_KEY → Stooq（延迟，免费）

A股:
  新浪财经API（免费，需Referer头）
```

---

## 8. 理解报告

### 8.1 标准日报 (YYYY-MM-DD.md)

```markdown
# A股每日监控报告 -- 2026-05-21

## 市场概览
- 英伟达(NVDA): 135.20 ↑+4.52%
- 宁德时代(300750): 210.50 ↓-1.20%

## 异常波动信号
| 股票 | 代码 | 异常类型 | 严重程度 | 详情 |
|------|------|----------|----------|------|
| 英伟达 | NVDA | price_surge | high | ... |

### NVDA -- price_surge
（AI分析内容）

## 投资假设追踪
### h2: AI算力需求爆发，利好GPU芯片
- **最新评估**: 英伟达财报超预期，支持该假设...
- **状态建议**: confirmed
```

### 8.2 Today日报 (YYYY-MM-DD-today.md)

带 `--today` 参数时额外生成，包含4个章节：

1. **今日焦点 - 异动提醒**：分为"重要异动"和"普通异动"
2. **今日待办**：P0/P1/P2 手动编辑区域
3. **核心假设追踪**：假设状态和AI评估
4. **新闻来源汇总**：分析所用的新闻来源

### 8.3 异常类型说明

| 类型 | 含义 | 触发条件 |
|------|------|----------|
| price_surge | 暴涨 | 单日涨幅超过阈值 |
| price_drop | 暴跌 | 单日跌幅超过阈值 |
| volume_spike | 放量 | 成交量超过20日均量的N倍 |
| consecutive_move | 连续走势 | 连续N天同方向变化 |

### 8.4 严重程度说明

| 程度 | 价格异动 | 放量异动 | 连续走势 |
|------|----------|----------|----------|
| medium | 5-7% | 2.5-4倍 | 3天 |
| high | >7% | >4倍 | 5天+ |

---

## 9. 异动检测规则详解

### 9.1 价格异动检测

**规则：** 当日涨跌幅绝对值 >= `price_change_pct`

```
涨跌幅(%) = (今日收盘价 - 昨日收盘价) / 昨日收盘价 x 100
```

**A股涨跌停规则：**
- 主板（沪/深）：±10%
- 创业板/科创板：±20%
- ST股票：±5%

**美股：** 无涨跌停限制。

### 9.2 放量异动检测

**规则：** 当日成交量 / 近20日平均成交量 >= `volume_spike_ratio`

需要至少21天历史数据才能计算。

### 9.3 连续走势检测

**规则：** 连续同方向变化天数 >= `consecutive_days` 且累计变化 >= `consecutive_change_pct`

---

## 10. 投资假设追踪

### 10.1 定义假设

```yaml
hypotheses:
  - id: "h1"
    text: "降息周期下银行净息差企稳，带动估值修复"
    related_symbols: ["000001", "601398"]
    active: true
  - id: "h2"
    text: "AI算力需求爆发，GPU芯片供不应求，利好半导体"
    related_symbols: ["NVDA", "AMD", "AVGO", "688041"]
    active: true
```

### 10.2 假设编写建议

**好的假设：**
- 明确因果："因为X，所以Y"
- 可验证预测："板块将迎来估值修复"
- 有时间范围："下半年"、"未来一年"

```yaml
# 好的例子
- text: "降息周期下银行净息差企稳，带动估值修复"
- text: "AI算力需求爆发，GPU芯片供不应求，利好半导体设备商"
- text: "消费复苏叠加白酒去库存完成，下半年将迎来戴维斯双击"
```

**不好的假设：**
- 过于模糊："银行股会涨"
- 不可验证："某公司管理层很优秀"

### 10.3 假设状态

| 状态 | 含义 | 建议操作 |
|------|------|----------|
| active | 维持原判 | 继续观察 |
| confirmed | 假设得到验证 | 可考虑加仓 |
| needs_review | 需要重新评估 | 关注风险 |
| invalidated | 假设已失效 | 考虑止损 |

### 10.4 历史记录

假设更新历史保存在 `output/hypothesis_history.json`，每次运行自动追加。

---

## 11. 定时任务设置

### 11.1 macOS - launchd

```bash
cat > ~/Library/LaunchAgents/com.stock-monitor.plist << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.stock-monitor</string>
    <key>ProgramArguments</key>
    <array>
        <string>/Volumes/SSD2T/Users/fortune/2026-program/01-claude-program/2026-stock-monitor/.venv/bin/python</string>
        <string>-m</string>
        <string>src.main</string>
        <string>--config</string>
        <string>/Volumes/SSD2T/Users/fortune/2026-program/01-claude-program/2026-stock-monitor/config.yaml</string>
        <string>--today</string>
    </array>
    <key>WorkingDirectory</key>
    <string>/Volumes/SSD2T/Users/fortune/2026-program/01-claude-program/2026-stock-monitor</string>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>18</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>
    <key>EnvironmentVariables</key>
    <dict>
        <key>FINNHUB_API_KEY</key>
        <string>你的Finnhub密钥</string>
    </dict>
    <key>StandardOutPath</key>
    <string>/tmp/stock-monitor.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/stock-monitor-error.log</string>
</dict>
</plist>
EOF
```

加载/管理：

```bash
# 加载
launchctl load ~/Library/LaunchAgents/com.stock-monitor.plist

# 查看状态
launchctl list | grep stock-monitor

# 停止
launchctl unload ~/Library/LaunchAgents/com.stock-monitor.plist
```

### 11.2 Linux - cron

```bash
crontab -e

# 每个交易日18:00运行（A股收盘后，美股盘前）
0 18 * * 1-5 cd /path/to/2026-stock-monitor && .venv/bin/python -m src.main --config config.yaml --today >> /tmp/stock-monitor.log 2>&1
```

### 11.3 运行时间建议

| 时间 | 事件 | 说明 |
|------|------|------|
| 09:30 | A股开盘 | 可运行实时监控 |
| 15:00 | A股收盘 | 可运行每日报告 |
| 21:30 | 美股开盘 | 可运行实时监控 |
| 18:00 | 推荐 | A股已收盘，美股盘前，一次覆盖两个市场 |

---

## 12. 常见问题

### Q1: 运行时报错 "No module named 'akshare'"

虚拟环境未激活或依赖未安装：

```bash
source .venv/bin/activate
pip install -e ".[dev]"
```

### Q2: Ollama报错 "cannot connect"

Ollama服务未启动：

```bash
ollama serve
# 或检查是否已运行
curl http://localhost:11434/api/tags
```

### Q3: Ollama响应很慢

模型太大或GPU显存不足：

```bash
# 使用更小的模型
ollama pull gemma4:e4b
# 或
ollama pull qwen2.5:3b

# 检查GPU显存
nvidia-smi
```

### Q4: 美股数据获取失败

**Finnhub：** 检查API Key是否正确：
```bash
echo $FINNHUB_API_KEY
curl "https://finnhub.io/api/v1/quote?symbol=AAPL&token=$FINNHUB_API_KEY"
```

**Stooq：** 检查网络连接，Stooq偶尔不稳定，稍后重试即可。

### Q5: A股数据获取失败

可能是网络问题或akshare接口变化：

```bash
# 更新akshare
pip install --upgrade akshare

# 如果有代理，可能需要取消
unset http_proxy https_proxy
```

### Q6: 报告为空或无异动

- 当天确实没有异动（正常情况）
- 阈值过高，尝试降低 `thresholds` 中的值
- `lookback_days` 太少（放量检测需要至少21天）

### Q7: 如何切换LLM提供商

修改 `config.yaml` 中的 `llm.provider`，无需修改代码：

```yaml
# 本地Ollama
llm: { provider: "ollama" }

# OpenRouter（一个Key用所有模型）
llm: { provider: "openrouter" }

# NVIDIA高速推理
llm: { provider: "nvidia" }

# Claude API
llm: { provider: "claude" }
```

对应设置环境变量即可（见第4节）。

### Q8: 如何添加美股到自选股

在 `config.yaml` 的 `watchlist` 中添加：

```yaml
watchlist:
  - symbol: "TSLA"
    name: "特斯拉"
    sector: "汽车"
```

同时更新 `watchlist.md` 保持一致。系统会自动识别字母代码为美股。

### Q9: 如何查看运行日志

```bash
tail -f /tmp/stock-monitor.log
tail -f /tmp/stock-monitor-error.log
```

### Q10: 如何自定义报告模板

编辑 `templates/report.md.j2`（标准报告）或 `templates/today.md.j2`（Today日报），使用Jinja2语法。

---

## 附录：快速参考

### 常用命令

```bash
# Claude Code 一键运行（推荐）
/today

# 激活环境
source .venv/bin/activate

# 每日报告（Ollama模式）
./run.sh --today

# 测试运行
./run.sh --dry-run

# 实时行情监控
python -m src.realtime --watchlist watchlist.md

# 运行测试
pytest tests/
```

### 环境变量

| 变量 | 必要性 | 说明 |
|------|--------|------|
| OPENROUTER_API_KEY | OpenRouter模式必需 | OpenRouter API密钥（注册送额度） |
| NVIDIA_API_KEY | NVIDIA模式必需 | build.nvidia.com API密钥（注册送额度） |
| ANTHROPIC_API_KEY | Claude模式必需 | Anthropic API密钥 |
| FINNHUB_API_KEY | 美股推荐 | Finnhub API密钥（免费） |
| EOD_API_KEY | 港股可选 | EOD Historical Data密钥 |

### 文件结构

```
项目根目录/
├── config.yaml                    # 主配置（自选股、阈值、LLM）
├── watchlist.md                   # 自选股详细列表（带行业分类）
├── run.sh                         # 便捷运行脚本
├── src/
│   ├── main.py                    # CLI入口，每日报告pipeline
│   ├── realtime.py                # 实时行情监控（独立脚本）
│   ├── config.py                  # 配置加载
│   ├── models.py                  # 数据模型
│   ├── fetcher.py                 # A股历史数据（akshare）
│   ├── anomaly.py                 # 异动检测
│   ├── llm.py                     # LLM抽象层（Ollama/Claude）
│   ├── news.py                    # 新闻获取+AI分析
│   ├── hypothesis.py              # 假设追踪
│   └── report.py                  # 报告生成
├── templates/
│   ├── report.md.j2               # 标准日报模板
│   └── today.md.j2                # Today日报模板
├── output/                        # 生成的报告
│   ├── YYYY-MM-DD.md              # 标准日报
│   ├── YYYY-MM-DD-today.md        # Today日报
│   └── hypothesis_history.json    # 假设历史
└── tests/                         # 测试
```
