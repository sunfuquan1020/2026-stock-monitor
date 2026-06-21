"""CLI入口，编排整个pipeline。"""

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

from src.anomaly import detect_anomalies
from src.astock import fetch_a_share_basics
from src.config import MARKET_HK, get_hypotheses, get_thresholds, get_watchlist, load_config
from src.fetcher import fetch_daily_quotes
from src.global_stock import fetch_global_basics
from src.hypothesis import check_hypotheses, save_hypothesis_history
from src.llm import LLMProvider, create_provider
from src.models import AnalysisResult, ReportData, StockConfig
from src.news import analyze_anomaly, build_websearch_queries, collect_websearch_queries, fetch_news
from src.report import cleanup_old_reports, generate_report, generate_today_report, save_report

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def run(config_path: str, output_dir: str, dry_run: bool = False, today: bool = False) -> str | None:
    """运行完整的监控pipeline。

    Args:
        config_path: 配置文件路径
        output_dir: 输出目录
        dry_run: 是否跳过LLM调用

    Returns:
        生成的报告路径，失败返回None
    """
    logger.info("Loading configuration...")
    config = load_config(config_path)

    watchlist = get_watchlist(config)
    thresholds = get_thresholds(config)
    hypotheses = get_hypotheses(config)

    # 构建市场映射
    market_map = {s.symbol: s.market for s in watchlist}
    symbols = [s.symbol for s in watchlist]

    # 按市场分组统计
    a_share_count = sum(1 for s in watchlist if s.market == "A股")
    us_count = sum(1 for s in watchlist if s.market == "美股")
    hk_count = sum(1 for s in watchlist if s.market == MARKET_HK)
    logger.info(
        f"Monitoring {len(symbols)} symbols: A股 {a_share_count}只, "
        f"美股 {us_count}只, 港股 {hk_count}只"
    )

    # Step 1: 获取行情数据（A股 + 美股）
    logger.info("Fetching daily quotes (A股 + 美股)...")
    quotes = fetch_daily_quotes(
        symbols, days=thresholds.lookback_days,
        market_map=market_map, output_dir=output_dir,
    )

    a_share_data = sum(1 for s in quotes if market_map.get(s) == "A股")
    us_data = sum(1 for s in quotes if market_map.get(s) == "美股")
    hk_data = sum(1 for s in quotes if market_map.get(s) == MARKET_HK)
    logger.info(f"Got data for {len(quotes)} symbols (A股: {a_share_data}, 美股: {us_data}, 港股: {hk_data})")

    # Step 1b: 获取A股基本面快照 (腾讯财经: PE/PB/市值/换手率)
    a_share_symbols = [s.symbol for s in watchlist if s.market == "A股"]
    a_share_basics = []
    if a_share_symbols:
        logger.info(f"Fetching A股 basics for {len(a_share_symbols)} symbols...")
        basics_map = fetch_a_share_basics(a_share_symbols)
        # 按 watchlist 顺序排列
        a_share_basics = [basics_map[s] for s in a_share_symbols if s in basics_map]
        logger.info(f"Got basics for {len(a_share_basics)} A股")

    # Step 1c: 获取美股/港股基本面 (Yahoo: PE/PB/PEG/市值/ROE/目标价)
    global_items = [
        (s.symbol, s.name, s.market)
        for s in watchlist
        if s.market in ("美股", MARKET_HK)
    ]
    global_basics = []
    if global_items:
        logger.info(f"Fetching 美股/港股 basics for {len(global_items)} symbols...")
        gmap = fetch_global_basics(global_items)
        global_basics = [gmap[sym] for sym, _, _ in global_items if sym in gmap]
        logger.info(f"Got basics for {len(global_basics)} 美股/港股")

    # Step 2: 检测异动
    logger.info("Detecting anomalies...")
    anomalies = detect_anomalies(quotes, watchlist, thresholds)
    logger.info(f"Found {len(anomalies)} anomalies")

    # Step 3: 新闻分析
    analyses: list[AnalysisResult] = []
    llm: LLMProvider | None = None
    websearch_needed: list[tuple[str, str, str]] = []  # (symbol, name, market)

    if not dry_run:
        llm = create_provider(config)
        provider_name = config.get("llm", {}).get("provider", "claude")
        logger.info(f"Using LLM provider: {provider_name}")

    if llm and anomalies:
        logger.info("Analyzing anomalies...")
        max_tokens = config.get("claude", {}).get("max_tokens", 1024)
        name_map = {s.symbol: s.name for s in watchlist}

        for anomaly in anomalies:
            market = market_map.get(anomaly.symbol, "A股")
            news = fetch_news(anomaly.symbol, market=market)

            # 记录需要WebSearch补充的股票
            if not news:
                websearch_needed.append((
                    anomaly.symbol,
                    name_map.get(anomaly.symbol, anomaly.symbol),
                    market,
                ))

            analysis_text = analyze_anomaly(
                anomaly, news, llm, max_tokens, market=market
            )
            analyses.append(
                AnalysisResult(
                    symbol=anomaly.symbol,
                    anomaly=anomaly,
                    news_items=tuple(news),
                    claude_analysis=analysis_text,
                )
            )
    elif dry_run:
        logger.info("Dry run: skipping LLM calls")

    # 输出WebSearch补充查询
    if websearch_needed:
        queries = collect_websearch_queries(websearch_needed)
        logger.info(f"=== WebSearch补充查询 ({len(queries)}只股票需要补充新闻) ===")
        for symbol, qs in queries:
            for q in qs:
                logger.info(f"  WebSearch: {q}")

    # Step 4: 假设追踪
    hypothesis_updates = []
    if llm and hypotheses:
        logger.info("Checking hypotheses...")
        max_tokens = config.get("claude", {}).get("max_tokens", 1024)
        hypothesis_updates = check_hypotheses(
            hypotheses, quotes, analyses, llm, max_tokens
        )
        # 保存历史
        history_path = str(Path(output_dir) / "hypothesis_history.json")
        save_hypothesis_history(history_path, hypothesis_updates)

    # Step 5: 生成报告
    logger.info("Generating report...")
    market_summary = _build_market_summary(quotes, watchlist)

    report_data = ReportData(
        date=date.today(),
        anomalies=tuple(anomalies),
        analyses=tuple(analyses),
        hypothesis_updates=tuple(hypothesis_updates),
        market_summary=market_summary,
        a_share_basics=tuple(a_share_basics),
        global_basics=tuple(global_basics),
    )

    template_dir = str(Path(__file__).parent.parent / "templates")

    # 生成标准报告
    report_content = generate_report(report_data, template_dir)
    report_path = save_report(report_content, output_dir, date.today())
    logger.info(f"Report saved to {report_path}")

    # 生成 Today 日报
    if today:
        today_content = generate_today_report(report_data, template_dir)
        today_path = save_report(today_content, output_dir, date.today(), suffix="today")
        logger.info(f"Today report saved to {today_path}")

    # 清理旧报告
    keep_days = config.get("output", {}).get("keep_days", 30)
    deleted = cleanup_old_reports(output_dir, keep_days)
    if deleted:
        logger.info(f"Cleaned up {deleted} old reports")

    return report_path


def _build_market_summary(
    quotes: dict[str, list],
    watchlist: list[StockConfig],
) -> str:
    """构建市场概览摘要（多市场支持）。"""
    name_map = {s.symbol: s.name for s in watchlist}
    market_map = {s.symbol: s.market for s in watchlist}

    if not quotes:
        return "无可用数据"

    # 按市场分组
    a_share_lines = []
    us_lines = []
    hk_lines = []

    for symbol, daily_quotes in quotes.items():
        if not daily_quotes:
            continue
        latest = daily_quotes[-1]
        name = name_map.get(symbol, symbol)
        market = market_map.get(symbol, "A股")
        direction = "↑" if latest.change_pct > 0 else "↓" if latest.change_pct < 0 else "→"
        line = f"- {name}({symbol}): {latest.close:.2f} {direction}{latest.change_pct:+.2f}%"

        if market == "美股":
            us_lines.append(line)
        elif market == MARKET_HK:
            hk_lines.append(line)
        else:
            a_share_lines.append(line)

    sections = []
    if a_share_lines:
        sections.append("### A股\n" + "\n".join(a_share_lines))
    if us_lines:
        sections.append("### 美股\n" + "\n".join(us_lines))
    if hk_lines:
        sections.append("### 港股\n" + "\n".join(hk_lines))

    return "\n\n".join(sections) if sections else "无可用数据"


def cli():
    """CLI入口点。"""
    parser = argparse.ArgumentParser(description="多市场每日监控系统")
    parser.add_argument(
        "--config",
        required=True,
        help="配置文件路径 (YAML)",
    )
    parser.add_argument(
        "--output-dir",
        default="output",
        help="输出目录 (默认: output)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="跳过LLM调用，仅检测异动",
    )
    parser.add_argument(
        "--today",
        action="store_true",
        help="同时生成 Today 日报",
    )

    args = parser.parse_args()

    try:
        report_path = run(args.config, args.output_dir, args.dry_run, args.today)
        if report_path:
            print(f"Report generated: {report_path}")
        else:
            print("Failed to generate report")
            sys.exit(1)
    except Exception as e:
        logger.error(f"Pipeline failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    cli()
