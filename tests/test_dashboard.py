"""Tests for the read-only DSA dashboard adapter."""

import json
from pathlib import Path

import pytest

from src.dashboard import DashboardRepository, InvalidArtifactRequest


@pytest.fixture
def output_dir(tmp_path: Path) -> Path:
    (tmp_path / "judgments").mkdir()
    (tmp_path / "2026-08-14.md").write_text("# 日报 14\n", encoding="utf-8")
    (tmp_path / "2026-08-14-analysis.md").write_text("# 分析 14\n", encoding="utf-8")
    (tmp_path / "2026-08-15.md").write_text(
        "# 日报 15\n\n> ⚠️ **数据质量警告**\n> - 美股数据未更新\n",
        encoding="utf-8",
    )
    (tmp_path / "focus_list.json").write_text(
        json.dumps(
            {
                "updated": "2026-08-14",
                "regime_system": "震荡",
                "regime_adopted": "震荡 (采纳系统判定)",
                "regime_trajectory": "08-11熄火 → 08-14震荡",
                "stance": "控制追高",
                "data_asof": "A股完整，美股失效",
                "opportunity_list": {
                    "A_actionable": [],
                    "A_empty_reason": "缺量",
                    "B_wait_for_entry": [
                        {
                            "symbol": "688981",
                            "name": "中芯国际",
                            "price": 132.87,
                            "class": "B半导体",
                            "wyckoff": "拉升初期候选",
                            "why": "量价资金同步",
                            "trigger": "量比>1.3",
                            "invalidate": "跌回129.44",
                        }
                    ],
                    "C_watch": [],
                    "exited_today": [{"symbol": "300033", "name": "同花顺"}],
                },
                "focus_list_core": ["中芯国际 688981 — B档"],
                "observation_only": ["新易盛 300502 — C档"],
                "avoid_dont_catch_knife": ["同花顺 300033 — 回避"],
                "mainline_confirmed": [],
                "mainline_pending": ["半导体 — 待确认"],
                "pending_events": [{"date": "2026-08-20", "type": "财报"}],
                "blind_spots": ["美股量价失效"],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (tmp_path / "judgments" / "2026-08-14.json").write_text(
        json.dumps(
            {
                "date": "2026-08-14",
                "regime": "震荡",
                "verified_from_prev": [{"id": "j1", "status": "确认"}],
                "judgments": [
                    {
                        "id": "j2",
                        "claim": "没有A档",
                        "falsifiable": "放量普涨则作废",
                        "horizon_days": 2,
                        "status": "open",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return tmp_path


def test_snapshot_uses_latest_report_and_marks_state_dates(output_dir: Path):
    snapshot = DashboardRepository(output_dir).snapshot()

    assert snapshot["as_of_date"] == "2026-08-15"
    assert snapshot["sources"]["focus_date"] == "2026-08-14"
    assert snapshot["sources"]["judgment_date"] == "2026-08-14"
    assert snapshot["sources"]["focus_is_stale"] is True
    assert snapshot["market"]["regime"] == "震荡"
    assert snapshot["opportunities"]["a"]["items"] == []
    assert snapshot["opportunities"]["a"]["empty_reason"] == "缺量"
    assert snapshot["opportunities"]["b"]["items"][0]["symbol"] == "688981"
    assert snapshot["data_quality"]["warnings"] == ["美股数据未更新"]


def test_report_index_groups_artifacts_by_date(output_dir: Path):
    reports = DashboardRepository(output_dir).list_reports()

    assert [item["date"] for item in reports] == ["2026-08-15", "2026-08-14"]
    assert reports[0] == {
        "date": "2026-08-15",
        "has_report": True,
        "has_analysis": False,
        "has_judgment": False,
    }
    assert reports[1]["has_analysis"] is True
    assert reports[1]["has_judgment"] is True


def test_document_reads_only_known_artifacts(output_dir: Path):
    repository = DashboardRepository(output_dir)

    assert repository.get_document("2026-08-14", "analysis")["content"] == "# 分析 14\n"
    with pytest.raises(InvalidArtifactRequest):
        repository.get_document("../../etc/passwd", "report")
    with pytest.raises(InvalidArtifactRequest):
        repository.get_document("2026-08-14", "secret")


def test_missing_focus_file_degrades_without_crashing(tmp_path: Path):
    (tmp_path / "2026-08-15.md").write_text("# 日报\n", encoding="utf-8")

    snapshot = DashboardRepository(tmp_path).snapshot()

    assert snapshot["market"]["regime"] == "未知"
    assert snapshot["opportunities"]["a"]["items"] == []
    assert snapshot["sources"]["focus_date"] is None
