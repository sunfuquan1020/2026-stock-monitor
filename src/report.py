"""Markdown报告生成模块。"""

import logging
from datetime import date, datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from src.models import ReportData

logger = logging.getLogger(__name__)


def generate_report(data: ReportData, template_dir: str) -> str:
    """生成Markdown报告。

    Args:
        data: 报告数据
        template_dir: 模板目录路径

    Returns:
        渲染后的Markdown字符串
    """
    env = Environment(loader=FileSystemLoader(template_dir))
    template = env.get_template("report.md.j2")

    return template.render(
        date=data.date.isoformat(),
        market_summary=data.market_summary,
        anomalies=data.anomalies,
        analyses=data.analyses,
        hypothesis_updates=data.hypothesis_updates,
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )


def generate_today_report(data: ReportData, template_dir: str) -> str:
    """生成Today日报。

    Args:
        data: 报告数据
        template_dir: 模板目录路径

    Returns:
        渲染后的Markdown字符串
    """
    env = Environment(loader=FileSystemLoader(template_dir))
    template = env.get_template("today.md.j2")

    # 收集所有关联假设的股票代码
    related_symbols = set()
    for h in data.hypothesis_updates:
        related_symbols.update(h.new_evidence.split("；") if h.new_evidence else [])

    return template.render(
        date=data.date.isoformat(),
        anomalies=data.anomalies,
        analyses=data.analyses,
        hypothesis_updates=data.hypothesis_updates,
        related_symbols=related_symbols,
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )


def save_report(content: str, output_dir: str, report_date: date, suffix: str = "") -> str:
    """保存报告到文件。

    Args:
        content: 报告内容
        output_dir: 输出目录
        report_date: 报告日期
        suffix: 文件名后缀（如 "today"）

    Returns:
        保存的文件路径
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    if suffix:
        filename = f"{report_date.isoformat()}-{suffix}.md"
    else:
        filename = f"{report_date.isoformat()}.md"
    filepath = output_path / filename

    filepath.write_text(content, encoding="utf-8")
    logger.info(f"Report saved to {filepath}")

    return str(filepath)


def cleanup_old_reports(output_dir: str, keep_days: int) -> int:
    """清理过期报告。

    Args:
        output_dir: 输出目录
        keep_days: 保留天数

    Returns:
        删除的文件数
    """
    output_path = Path(output_dir)
    if not output_path.exists():
        return 0

    cutoff = date.today().toordinal() - keep_days
    deleted = 0

    for filepath in output_path.glob("*.md"):
        try:
            file_date = date.fromisoformat(filepath.stem)
            if file_date.toordinal() < cutoff:
                filepath.unlink()
                deleted += 1
                logger.info(f"Deleted old report: {filepath}")
        except ValueError:
            # 文件名不是日期格式，跳过
            continue

    return deleted
