"""Read-only adapter from stock-monitor artifacts to the Web dashboard contract."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "1.0"
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
REPORT_PATTERN = re.compile(r"^(\d{4}-\d{2}-\d{2})\.md$")
ANALYSIS_PATTERN = re.compile(r"^(\d{4}-\d{2}-\d{2})-analysis\.md$")


class InvalidArtifactRequest(ValueError):
    """Raised when a requested date or artifact kind is not whitelisted."""


class ArtifactNotFound(FileNotFoundError):
    """Raised when a valid artifact does not exist."""


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _extract_data_warnings(content: str) -> list[str]:
    """Extract the warning bullets from the report header only."""
    warnings: list[str] = []
    in_warning_block = False
    for line in content.splitlines():
        if "数据质量警告" in line:
            in_warning_block = True
            continue
        if not in_warning_block:
            continue
        if line.startswith("> - "):
            warnings.append(line[4:].strip())
            continue
        if line.startswith(">") or not line.strip():
            continue
        break
    return warnings


class DashboardRepository:
    """Project read model used by the FastAPI adapter and tests.

    The repository never mutates pipeline output. It converts the latest files
    into a versioned, stable response while keeping each source date explicit.
    """

    def __init__(self, output_dir: str | Path) -> None:
        self.output_dir = Path(output_dir)

    def _report_dates(self) -> list[str]:
        if not self.output_dir.is_dir():
            return []
        dates = {
            match.group(1)
            for path in self.output_dir.iterdir()
            if path.is_file() and (match := REPORT_PATTERN.match(path.name))
        }
        return sorted(dates, reverse=True)

    def _analysis_dates(self) -> list[str]:
        if not self.output_dir.is_dir():
            return []
        dates = {
            match.group(1)
            for path in self.output_dir.iterdir()
            if path.is_file() and (match := ANALYSIS_PATTERN.match(path.name))
        }
        return sorted(dates, reverse=True)

    def _judgment_dates(self) -> list[str]:
        directory = self.output_dir / "judgments"
        if not directory.is_dir():
            return []
        return sorted(
            (path.stem for path in directory.glob("*.json") if DATE_PATTERN.match(path.stem)),
            reverse=True,
        )

    @staticmethod
    def _latest_at_or_before(dates: list[str], as_of_date: str | None) -> str | None:
        if as_of_date is None:
            return dates[0] if dates else None
        return next((item for item in dates if item <= as_of_date), None)

    def list_reports(self) -> list[dict[str, Any]]:
        report_dates = set(self._report_dates())
        analysis_dates = set(self._analysis_dates())
        judgment_dates = set(self._judgment_dates())
        all_dates = sorted(report_dates | analysis_dates | judgment_dates, reverse=True)
        return [
            {
                "date": item,
                "has_report": item in report_dates,
                "has_analysis": item in analysis_dates,
                "has_judgment": item in judgment_dates,
            }
            for item in all_dates
        ]

    def snapshot(self) -> dict[str, Any]:
        report_date = self._latest_at_or_before(self._report_dates(), None)
        analysis_date = self._latest_at_or_before(self._analysis_dates(), report_date)
        judgment_date = self._latest_at_or_before(self._judgment_dates(), report_date)

        focus = _read_json(self.output_dir / "focus_list.json")
        focus_date = focus.get("updated") if isinstance(focus.get("updated"), str) else None
        judgment = (
            _read_json(self.output_dir / "judgments" / f"{judgment_date}.json")
            if judgment_date
            else {}
        )
        report_content = ""
        if report_date:
            try:
                report_content = (self.output_dir / f"{report_date}.md").read_text(encoding="utf-8")
            except OSError:
                report_content = ""

        raw_opportunities = focus.get("opportunity_list")
        opportunities = raw_opportunities if isinstance(raw_opportunities, dict) else {}
        a_items = _as_list(opportunities.get("A_actionable"))
        b_items = _as_list(opportunities.get("B_wait_for_entry"))
        c_items = _as_list(opportunities.get("C_watch"))

        return {
            "schema_version": SCHEMA_VERSION,
            "as_of_date": report_date,
            "sources": {
                "report_date": report_date,
                "analysis_date": analysis_date,
                "focus_date": focus_date,
                "judgment_date": judgment_date,
                "focus_is_stale": bool(report_date and focus_date and focus_date < report_date),
                "analysis_is_stale": bool(report_date and analysis_date and analysis_date < report_date),
            },
            "market": {
                "regime": focus.get("regime_system", judgment.get("regime", "未知")),
                "regime_adopted": focus.get("regime_adopted", ""),
                "trajectory": focus.get("regime_trajectory", judgment.get("regime_trajectory", "")),
                "stance": focus.get("stance", ""),
                "switch_note": focus.get("regime_switch_note", ""),
                "mainlines": {
                    "confirmed": _as_list(focus.get("mainline_confirmed")),
                    "pending": _as_list(focus.get("mainline_pending")),
                },
            },
            "opportunities": {
                "a": {
                    "items": a_items,
                    "empty_reason": opportunities.get("A_empty_reason", ""),
                },
                "b": {"items": b_items},
                "c": {"items": c_items},
            },
            "exits": _as_list(opportunities.get("exited_today")),
            "focus": {
                "core": _as_list(focus.get("focus_list_core")),
                "observation": _as_list(focus.get("observation_only")),
                "avoid": _as_list(focus.get("avoid_dont_catch_knife")),
            },
            "judgments": {
                "verified": _as_list(judgment.get("verified_from_prev")),
                "open": _as_list(judgment.get("judgments")),
            },
            "risks": {
                "events": _as_list(focus.get("pending_events")),
                "distribution_alerts": _as_list(focus.get("distribution_alert")),
            },
            "data_quality": {
                "as_of": focus.get("data_asof", ""),
                "warnings": _extract_data_warnings(report_content),
                "limitations": _as_list(focus.get("blind_spots")),
            },
            "reports": self.list_reports(),
        }

    def get_document(self, artifact_date: str, kind: str) -> dict[str, str]:
        if not DATE_PATTERN.fullmatch(artifact_date):
            raise InvalidArtifactRequest("date must use YYYY-MM-DD")
        suffixes = {"report": ".md", "analysis": "-analysis.md"}
        suffix = suffixes.get(kind)
        if suffix is None:
            raise InvalidArtifactRequest("kind must be report or analysis")

        path = self.output_dir / f"{artifact_date}{suffix}"
        if not path.is_file():
            raise ArtifactNotFound(str(path))
        return {
            "date": artifact_date,
            "kind": kind,
            "content": path.read_text(encoding="utf-8"),
        }
