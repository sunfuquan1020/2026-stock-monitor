---
name: today
description: "运行每日股票监控系统，生成今日报告。Pipeline 只抓数据+检测异动(秒级)，异动分析由 Claude(agent) 直接完成，无需 LLM API key。包含A股+美股+港股。"
---

# /today - 每日股票监控

运行A股+美股+港股每日监控系统。**Pipeline 只负责抓数据 + 检测异动(几十秒)，
异动的 AI 分析由你(Claude agent)在对话里直接完成** —— 不调用任何 LLM API，
因此用 `--dry-run` 跳过 pipeline 内置的 LLM 调用(那个免费模型极慢)。

## 执行步骤

1. 运行 pipeline（**dry-run**：抓数据 + 检测异动 + 基本面，跳过内置 LLM，约几十秒）：
```bash
cd /Volumes/SSD2T/Users/fortune/2026-program/03-xiaomi-program/2026-stock-monitor
./run.sh --today --dry-run
```
> 不要去掉 `--dry-run`：带上它才不会触发那个慢速免费 LLM；分析交给你来做。

2. 如果 pipeline 失败，检查错误原因：
   - AKShare 获取A股失败 → 可能限流，稍后重试或检查网络
   - 美股/港股获取失败 → 检查网络 / `FINNHUB_API_KEY`（美股今日报价，可选；Yahoo 仍能回填历史）
   - 数据全空 → 检查 `.venv` 与网络

3. 读取 pipeline 生成的数据报告：
```bash
cat output/$(date +%Y-%m-%d).md          # 含：市场概览 + A股基本面 + 美股/港股基本面 + 异常波动信号表
```

4. **Claude 直接分析异动（核心步骤，代替内置 LLM）**：
   - 从上面报告的「异常波动信号」表读取全部异动（含类型/严重程度/涨跌幅/量比等 details）
   - 结合「A股基本面 / 美股港股基本面」表里的 PE/PB/市值/换手率/目标价等
   - 对每个 **high** 严重度异动写简明分析：①可能原因 ②短期风险/机会 ③关注信号
   - **medium** 异动汇总成表，逐行一句话点评
   - 把分析写入 `output/$(date +%Y-%m-%d)-analysis.md`（标题「🤖 Claude 异动分析 -- 日期」），并在对话中给出要点

5. (可选) WebSearch 补充新闻：对重点异动股票搜索 `'代码 A股/港股 最新消息'` 或 `'TICKER stock news today'`，把关键信息融进你的分析。

6. (可选) 实时行情：`python -m src.realtime --watchlist watchlist.md`

7. 汇总输出：
   - 各市场数据获取情况（A股/美股/港股 数量）
   - 异动数量；high/medium 分布
   - Claude 分析要点 + analysis 文件路径
   - 数据报告路径 output/日期.md

## 数据源

| 市场 | 数据源 | 依赖 |
|------|--------|------|
| A股 | AKShare (`ak.stock_zh_a_hist`) 主 + mootdx 兜底 | `akshare` / `mootdx` 包 |
| 美股 | Finnhub (主) + Stooq (备) + Yahoo (历史K线) | `FINNHUB_API_KEY` 环境变量 |
| 港股 | Yahoo Finance chart | 无 |
| 全市场新闻 | WebSearch 补充 (A股/美股/港股统一) | 无 |

## 注意事项

- 工作目录必须是项目根目录
- 使用 `.venv/bin/python` 确保虚拟环境正确
- 当前 LLM 提供商由 `config.yaml` 中 `llm.provider` 决定
- 报告输出在 `output/` 目录
- 美股历史数据累积在 `output/us_quote_history.json`
