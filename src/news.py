"""新闻获取与AI分析模块。

数据源:
- A股: AKShare stock_news_em (东方财富新闻)
- 美股: WebSearch辅助 (Finnhub不提供新闻)
"""

import json
import logging
import time

import akshare as ak

from src.config import MARKET_A_SHARE, MARKET_FUTURES, MARKET_US, detect_market
from src.llm import LLMProvider
from src.models import Anomaly, Hypothesis, NewsItem

logger = logging.getLogger(__name__)

# AKShare新闻获取重试配置
NEWS_MAX_RETRIES = 2
NEWS_RETRY_DELAY = 3.0


def fetch_news(
    symbol: str, max_items: int = 10, market: str | None = None
) -> list[NewsItem]:
    """获取股票相关新闻，按市场选择数据源。

    Args:
        symbol: 股票代码
        max_items: 最多返回条数
        market: 市场标识 (A股/美股)，未提供则自动检测

    Returns:
        NewsItem列表
    """
    if market is None:
        market = detect_market(symbol)

    if market == MARKET_A_SHARE:
        return _fetch_news_a_share(symbol, max_items)
    elif market == MARKET_US:
        return _fetch_news_us_stock(symbol, max_items)
    elif market == MARKET_FUTURES:
        return _fetch_news_futures(symbol, max_items)
    else:
        return _fetch_news_a_share(symbol, max_items)


def _fetch_news_a_share(symbol: str, max_items: int) -> list[NewsItem]:
    """通过AKShare获取A股新闻。"""
    for attempt in range(NEWS_MAX_RETRIES):
        try:
            df = ak.stock_news_em(symbol=symbol)

            if df is None or df.empty:
                return []

            items = []
            for _, row in df.head(max_items).iterrows():
                try:
                    item = NewsItem(
                        title=str(row.get("新闻标题", "")),
                        source=str(row.get("文章来源", "")),
                        url=str(row.get("新闻链接", "")),
                        publish_time=str(row.get("发布时间", "")),
                        content_snippet=str(row.get("新闻内容", ""))[:200],
                    )
                    items.append(item)
                except (ValueError, TypeError) as e:
                    logger.warning(f"Failed to parse news for {symbol}: {e}")

            return items

        except Exception as e:
            error_msg = str(e).lower()
            if "rate" in error_msg or "limit" in error_msg or "frequency" in error_msg:
                if attempt < NEWS_MAX_RETRIES - 1:
                    wait_time = NEWS_RETRY_DELAY * (attempt + 1)
                    logger.warning(
                        f"AKShare rate limited for news {symbol}, "
                        f"retrying in {wait_time}s (attempt {attempt + 1}/{NEWS_MAX_RETRIES})"
                    )
                    time.sleep(wait_time)
                else:
                    logger.error(f"Failed to fetch A-share news for {symbol}: {e}")
                    return []
            else:
                logger.error(f"Failed to fetch A-share news for {symbol}: {e}")
                return []
    return []


def _fetch_news_us_stock(symbol: str, max_items: int) -> list[NewsItem]:
    """获取美股新闻（目前无免费API，依赖WebSearch补充）。"""
    logger.info(
        f"US stock news for {symbol}: 无免费API, "
        f"需通过WebSearch补充搜索 '{symbol} stock news today'"
    )
    return []


def _fetch_news_futures(symbol: str, max_items: int) -> list[NewsItem]:
    """获取期货新闻（无专用API，依赖WebSearch补充）。"""
    logger.info(
        f"Futures news for {symbol}: 无免费API, "
        f"需通过WebSearch补充搜索 '{symbol} futures news'"
    )
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
    if market == MARKET_FUTURES:
        # 期货搜索查询
        if name:
            queries.append(f"{name} 期货 最新消息")
            queries.append(f"{name} 行情分析")
        else:
            queries.append(f"{symbol} futures news today")
    elif market == MARKET_US:
        # 美股搜索查询
        if name:
            queries.append(f"{name} {symbol} stock news today")
            queries.append(f"{name} stock earnings analyst rating")
        else:
            queries.append(f"{symbol} stock news today")
            queries.append(f"{symbol} stock earnings analyst rating")
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
