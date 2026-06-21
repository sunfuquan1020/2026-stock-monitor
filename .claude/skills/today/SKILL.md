---
name: today
description: "运行每日股票监控系统，生成今日报告。包含A股+美股异动检测、AI分析、假设追踪和Today日报。"
---

# /today - 每日股票监控

运行A股+美股每日监控系统，生成今日报告。

## 执行步骤

1. 运行每日监控 pipeline（含 Today 日报）：
```bash
cd /Volumes/SSD2T/Users/fortune/2026-program/03-xiaomi-program/2026-stock-monitor
./run.sh --today
```

2. 如果 pipeline 失败，检查错误原因：
   - AKShare 获取A股失败 → 可能限流，稍后重试或检查网络
   - Finnhub 获取美股失败 → 检查 `FINNHUB_API_KEY` 环境变量是否设置
   - Ollama 502/连接失败 → 提示用户检查 Ollama 服务：`ollama serve`
   - API Key 错误 → 提示用户设置对应环境变量

3. pipeline 成功后，读取并展示生成的报告：
```bash
# 读取 Today 日报
cat output/$(date +%Y-%m-%d)-today.md
```

4. 同时运行实时行情监控（可选，如果用户需要）：
```bash
python -m src.realtime --watchlist watchlist.md
```

5. WebSearch补充新闻（如果pipeline输出了WebSearch查询）：
   - 对pipeline日志中 `WebSearch补充查询` 列出的每条查询，使用WebSearch工具搜索
   - 将搜索结果中的关键信息补充到报告的新闻分析中
   - 美股通常需要WebSearch补充（无免费新闻API）

6. 汇总输出：
   - 各市场数据获取情况（A股/美股）
   - 异动股票数量和详情
   - WebSearch补充新闻结果
   - AI 分析摘要
   - 假设状态变化
   - Today 日报路径

## 数据源

| 市场 | 数据源 | 依赖 |
|------|--------|------|
| A股 | AKShare (`ak.stock_zh_a_hist`) | `akshare` 包 |
| 美股 | Finnhub (主) + Stooq (备) | `FINNHUB_API_KEY` 环境变量 |
| A股新闻 | AKShare (`ak.stock_news_em`) | `akshare` 包 |
| 美股新闻 | WebSearch 补充 | 无 |

## 注意事项

- 工作目录必须是项目根目录
- 使用 `.venv/bin/python` 确保虚拟环境正确
- 当前 LLM 提供商由 `config.yaml` 中 `llm.provider` 决定
- 报告输出在 `output/` 目录
- 美股历史数据累积在 `output/us_quote_history.json`
