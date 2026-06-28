"""新闻获取与AI分析模块。

新闻数据源:
- 全市场 (A股/美股/港股): WebSearch 补充
  A股原 AKShare stock_news_em 接口 TLS 不稳定 (curl 35)，已弃用，统一走 WebSearch。
  fetch_news 返回空列表 -> main.py 收集 WebSearch 查询 (见 build_websearch_queries)。
"""

import json
import logging

from src.config import MARKET_A_SHARE, MARKET_HK, MARKET_US, detect_market
from src.llm import LLMProvider
from src.models import Anomaly, Hypothesis, NewsItem

logger = logging.getLogger(__name__)


def fetch_news(
    symbol: str, max_items: int = 10, market: str | None = None
) -> list[NewsItem]:
    """获取股票相关新闻。

    所有市场均无稳定免费新闻API，统一返回空列表并由上层用 WebSearch 补充：
    - A股: AKShare stock_news_em TLS 不稳定，已弃用
    - 美股/港股: 无免费API

    Args:
        symbol: 股票代码
        max_items: 最多返回条数 (保留参数，当前未用)
        market: 市场标识 (A股/美股/港股)，未提供则自动检测

    Returns:
        空列表 (新闻由 WebSearch 补充)
    """
    if market is None:
        market = detect_market(symbol)

    if market == MARKET_A_SHARE:
        hint = f"'{symbol} A股 最新消息'"
    elif market == MARKET_HK:
        hint = f"'{symbol} 港股 最新消息'"
    else:
        hint = f"'{symbol} stock news today'"
    logger.info(f"{market} {symbol} 新闻: 无免费API, 需通过 WebSearch 补充搜索 {hint}")
    return []


def build_websearch_queries(symbol: str, name: str = "", market: str = "A股") -> list[str]:
    """构建WebSearch补充查询列表。

    当主要数据源失败或新闻不足时，生成搜索查询供外部WebSearch使用。

    Args:
        symbol: 股票代码
        name: 股票名称（可选）
        market: 市场标识

    Returns:
        搜索查询字符串列表
    """
    queries = []
    if market == MARKET_US:
        # 美股搜索查询
        if name:
            queries.append(f"{name} {symbol} stock news today")
            queries.append(f"{name} stock earnings analyst rating")
        else:
            queries.append(f"{symbol} stock news today")
            queries.append(f"{symbol} stock earnings analyst rating")
    elif market == MARKET_HK:
        # 港股搜索查询
        if name:
            queries.append(f"{name} {symbol} 港股 最新消息")
        else:
            queries.append(f"{symbol} 港股 最新消息")
    else:
        # A股搜索查询
        if name:
            queries.append(f"{name} {symbol} 股票 最新消息")
        else:
            queries.append(f"{symbol} A股 最新消息")
    return queries


def collect_websearch_queries(
    symbols_needing_news: list[tuple[str, str, str]],
) -> list[tuple[str, list[str]]]:
    """收集所有需要WebSearch补充的查询。

    Args:
        symbols_needing_news: [(symbol, name, market), ...] 需要补充新闻的股票列表

    Returns:
        [(symbol, queries), ...] 每只股票的搜索查询列表
    """
    result = []
    for symbol, name, market in symbols_needing_news:
        queries = build_websearch_queries(symbol, name, market)
        if queries:
            result.append((symbol, queries))
    return result


def analyze_anomaly(
    anomaly: Anomaly,
    news: list[NewsItem],
    llm: LLMProvider,
    max_tokens: int = 1024,
    market: str = "A股",
) -> str:
    """用LLM分析异动原因。

    Args:
        anomaly: 异动信息
        news: 相关新闻
        llm: LLM提供商
        max_tokens: 最大token数
        market: 市场标识

    Returns:
        分析文本
    """
    market_label = "A股" if market == MARKET_A_SHARE else "美股"
    prompt = f"""你是一个{market_label}市场分析师。以下是一只股票的异常波动信息和相关新闻。
请分析可能的原因，并给出简短的投资建议。

## 异常信息
- 股票: {anomaly.name}({anomaly.symbol})
- 市场: {market_label}
- 异常类型: {anomaly.anomaly_type.value}
- 严重程度: {anomaly.severity.value}
- 详情: {json.dumps(anomaly.details, ensure_ascii=False)}

## 近期新闻
{_format_news_for_prompt(news)}

请用中文回答，包含:
1. 异常波动的可能原因（基于新闻分析）
2. 短期风险/机会评估
3. 建议关注的后续信号"""

    return llm.generate(prompt, max_tokens)


def check_hypothesis_news(
    hypothesis: Hypothesis,
    news: list[NewsItem],
    anomaly_context: str,
    llm: LLMProvider,
    max_tokens: int = 1024,
) -> str:
    """用LLM评估投资假设。

    Args:
        hypothesis: 投资假设
        news: 相关新闻
        anomaly_context: 异动上下文信息
        llm: LLM提供商
        max_tokens: 最大token数

    Returns:
        评估文本
    """
    prompt = f"""你是一个投资研究助手。请根据以下新闻和市场信息，评估一个投资假设的最新状态。

## 投资假设
{hypothesis.text}

## 相关股票近期新闻
{_format_news_for_prompt(news)}

## 今日异动信息
{anomaly_context if anomaly_context else "今日无相关异动"}

请评估:
1. 这些新闻是否支持或反驳该假设？
2. 有无新的重要信息需要关注？
3. 假设状态建议: 保持(active) / 加强(confirmed) / 需要重新评估(needs_review) / 已失效(invalidated)
4. 一句话总结"""

    return llm.generate(prompt, max_tokens)


def _format_news_for_prompt(news: list[NewsItem]) -> str:
    """格式化新闻列表为prompt文本。"""
    if not news:
        return "暂无相关新闻"

    lines = []
    for i, item in enumerate(news, 1):
        lines.append(f"{i}. [{item.source}] {item.title}")
        if item.content_snippet:
            lines.append(f"   摘要: {item.content_snippet}")
    return "\n".join(lines)
