"""投资假设追踪模块。"""

import json
import logging
from pathlib import Path

from src.llm import LLMProvider
from src.models import (
    AnalysisResult,
    DailyQuote,
    Hypothesis,
    HypothesisStatus,
    HypothesisUpdate,
    NewsItem,
)
from src.news import check_hypothesis_news, fetch_news

logger = logging.getLogger(__name__)


def check_hypotheses(
    hypotheses: list[Hypothesis],
    quotes: dict[str, list[DailyQuote]],
    analyses: list[AnalysisResult],
    llm: LLMProvider,
    max_tokens: int = 1024,
) -> list[HypothesisUpdate]:
    """检查所有投资假设的最新状态。

    Args:
        hypotheses: 投资假设列表
        quotes: 股票行情数据
        analyses: 异动分析结果
        llm: LLM提供商
        max_tokens: 最大token数

    Returns:
        假设更新列表
    """
    updates = []

    for hyp in hypotheses:
        try:
            update = _check_single_hypothesis(hyp, analyses, llm, max_tokens)
            if update:
                updates.append(update)
        except Exception as e:
            logger.error(f"Failed to check hypothesis {hyp.id}: {e}")

    return updates


def _check_single_hypothesis(
    hypothesis: Hypothesis,
    analyses: list[AnalysisResult],
    llm: LLMProvider,
    max_tokens: int,
) -> HypothesisUpdate | None:
    """检查单个投资假设。"""
    # 收集相关新闻
    all_news: list[NewsItem] = []
    for symbol in hypothesis.related_symbols:
        news = fetch_news(symbol, max_items=5)
        all_news.extend(news)

    if not all_news:
        return None

    # 构建异动上下文
    anomaly_context = _build_anomaly_context(hypothesis.related_symbols, analyses)

    # 调用LLM评估
    assessment = check_hypothesis_news(
        hypothesis=hypothesis,
        news=all_news,
        anomaly_context=anomaly_context,
        llm=llm,
        max_tokens=max_tokens,
    )

    # 解析建议状态
    suggested_status = _parse_suggested_status(assessment, hypothesis.status)

    # 构建证据摘要
    evidence = _build_evidence_summary(all_news)

    return HypothesisUpdate(
        hypothesis_id=hypothesis.id,
        hypothesis_text=hypothesis.text,
        new_evidence=evidence,
        claude_assessment=assessment,
        suggested_status=suggested_status,
    )


def _build_anomaly_context(
    related_symbols: list[str] | tuple[str, ...],
    analyses: list[AnalysisResult],
) -> str:
    """构建异动上下文信息。"""
    relevant = [a for a in analyses if a.symbol in related_symbols]
    if not relevant:
        return ""

    lines = []
    for a in relevant:
        anomaly = a.anomaly
        if anomaly:
            lines.append(
                f"- {anomaly.name}({anomaly.symbol}): "
                f"{anomaly.anomaly_type.value}, "
                f"严重程度: {anomaly.severity.value}"
            )
    return "\n".join(lines)


def _parse_suggested_status(
    assessment: str, current: HypothesisStatus
) -> HypothesisStatus:
    """从LLM评估中解析建议状态。"""
    if not assessment:
        return current
    lower = assessment.lower()
    if "已失效" in assessment or "invalidated" in lower:
        return HypothesisStatus.INVALIDATED
    elif "需要重新评估" in assessment or "needs_review" in lower:
        return HypothesisStatus.NEEDS_REVIEW
    elif "加强" in assessment or "confirmed" in lower:
        return HypothesisStatus.CONFIRMED
    return current


def _build_evidence_summary(news: list[NewsItem]) -> str:
    """构建证据摘要。"""
    if not news:
        return "无相关新闻"

    titles = [item.title for item in news[:5]]
    return "；".join(titles)


def load_hypothesis_history(path: str) -> list[dict]:
    """加载假设历史记录。"""
    history_path = Path(path)
    if not history_path.exists():
        return []

    try:
        with open(history_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        logger.error(f"Failed to load hypothesis history: {e}")
        return []


def save_hypothesis_history(path: str, updates: list[HypothesisUpdate]) -> None:
    """保存假设更新到历史记录。"""
    history = load_hypothesis_history(path)

    for update in updates:
        record = {
            "hypothesis_id": update.hypothesis_id,
            "hypothesis_text": update.hypothesis_text,
            "new_evidence": update.new_evidence,
            "claude_assessment": update.claude_assessment,
            "suggested_status": update.suggested_status.value,
            "timestamp": update.timestamp.isoformat(),
        }
        history.append(record)

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)
